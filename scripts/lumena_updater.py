"""Detached Lumena update helper. Never run inside the web process."""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.runtime.update_installation import (  # noqa: E402
    UpdateInstallationError, apply_transaction, git_fast_forward_to,
    rollback_transaction,
)
from src.utils.persistence import atomic_write_json, safe_read_json  # noqa: E402


def _state(path: Path, state: str, **details) -> None:
    value = safe_read_json(path, default={})
    if not isinstance(value, dict):
        value = {}
    value.update(details)
    value.update({"schema_version": 1, "state": state, "updated_at": time.time()})
    atomic_write_json(path, value)


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=5, check=False,
        )
        return result.returncode == 0 and f'"{pid}"' in (result.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _terminate_parent(pid: int) -> None:
    if not _alive(pid):
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=15, check=False)
    else:
        os.kill(pid, signal.SIGTERM)


def _wait_stopped(pid: int, timeout: float = 45.0) -> None:
    deadline = time.monotonic() + timeout
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.25)
    if _alive(pid):
        raise UpdateInstallationError("Lumena ne s'est pas arretee avant la mise a jour")


def _run_smoke(python: str, root: Path, version: str, commit: str) -> None:
    result = subprocess.run(
        [python, "-m", "src.runtime.update_guard_smoke", "--root", str(root),
         "--version", version, "--commit", commit],
        cwd=root, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=120, check=False,
    )
    if result.returncode != 0:
        raise UpdateInstallationError(f"fumee update-v1 rouge: {(result.stdout or result.stderr).strip()}")


def _launch(command: list[str], root: Path) -> subprocess.Popen:
    flags = 0
    if os.name == "nt":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(command, cwd=root, creationflags=flags, close_fds=True)


def _wait_health(
    url: str, version: str, commit: str, timeout: float = 90.0,
    *, require_manifest: bool = True,
) -> dict:
    deadline = time.monotonic() + timeout
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as response:
                data = json.loads(response.read())
            identity_ok = (
                data.get("status") == "ok" and data.get("version") == version
                and data.get("commit") == commit
            )
            manifest_ok = bool(data.get("managed_manifest_sha256")) or not require_manifest
            if identity_ok and manifest_ok:
                return data
            last_error = f"identite inattendue: {data}"
        except Exception as exc:
            last_error = str(exc)
        time.sleep(1)
    raise UpdateInstallationError(f"health exact non confirme: {last_error}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--mode", choices=("installed", "portable", "git"), required=True)
    parser.add_argument("--transaction", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--restart-json", required=True)
    parser.add_argument("--health-url", required=True)
    parser.add_argument("--rollback-only", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    restart = json.loads(args.restart_json)
    if not isinstance(restart, list) or not restart or not all(isinstance(item, str) for item in restart):
        raise UpdateInstallationError("commande de redemarrage invalide")

    _state(args.state, "waiting_idle", target_version=args.version)
    time.sleep(1.0)
    _terminate_parent(args.parent_pid)
    _wait_stopped(args.parent_pid)
    applied = False
    try:
        _state(args.state, "applying")
        if args.rollback_only:
            if args.transaction is None:
                raise UpdateInstallationError("transaction de rollback absente")
            rollback_transaction(args.transaction)
        elif args.mode == "git":
            git_fast_forward_to(root, args.commit)
        else:
            if args.transaction is None:
                raise UpdateInstallationError("transaction de fichiers absente")
            apply_transaction(args.transaction)
        applied = True
        _run_smoke(args.python, root, args.version, args.commit)
        _state(args.state, "restarting")
        _launch(restart, root)
        identity = _wait_health(
            args.health_url, args.version, args.commit,
            require_manifest=args.mode != "git",
        )
        _state(args.state, "healthy", installed_version=args.version, health=identity, error=None)
        return 0
    except Exception as exc:
        _state(args.state, "rolling_back", error=str(exc))
        if applied and not args.rollback_only and args.mode != "git" and args.transaction is not None:
            try:
                rollback_transaction(args.transaction)
                _launch(restart, root)
                plan = safe_read_json(args.transaction, default={})
                previous = safe_read_json(Path(str(plan.get("snapshot_dir") or "")) / "build-info.json", default={})
                rollback_health = None
                if previous.get("version") and previous.get("commit"):
                    rollback_health = _wait_health(
                        args.health_url, str(previous["version"]), str(previous["commit"]), timeout=90,
                    )
                _state(
                    args.state, "failed", error=f"mise a jour annulee et rollback applique: {exc}",
                    rollback_health=rollback_health,
                )
            except Exception as rollback_exc:
                _state(args.state, "failed", error=f"mise a jour et rollback en echec: {exc}; {rollback_exc}")
        elif args.mode == "git":
            # Git updates are fast-forward-only and never hidden behind reset.
            # Restart the checkout so its owner can inspect/recover a failed smoke.
            try:
                _launch(restart, root)
            except Exception:
                pass
            _state(args.state, "failed", error=f"mise a jour Git appliquee mais non certifiee: {exc}")
        else:
            _state(args.state, "failed", error=str(exc))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
