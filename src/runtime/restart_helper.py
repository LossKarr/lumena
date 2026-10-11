"""Detached helper used by :mod:`src.runtime.restart_service`.

It contains no server imports, accepts no shell command and only relaunches the
fully specified local command passed by the authenticated restart endpoint.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def _process_exists(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(
            ["tasklist.exe", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=5, check=False,
        )
        return result.returncode == 0 and f'"{pid}"' in (result.stdout or "")
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def wait_for_exit(pid: int, *, timeout_s: float = 30.0) -> bool:
    deadline = time.monotonic() + max(1.0, timeout_s)
    while time.monotonic() < deadline:
        if not _process_exists(pid):
            return True
        time.sleep(0.25)
    return not _process_exists(pid)


def _powershell_literal(value: object) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _audit(root: Path, event: str) -> None:
    """Leave a bounded, secret-free proof when the helper has no console."""
    try:
        path = root / "data" / "logs" / "restart.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp} {event[:160]}\n")
    except OSError:
        pass


def _validate_command(command: list[str], *, root: Path) -> bool:
    if not 2 <= len(command) <= 8:
        return False
    executable = Path(command[0]).resolve()
    if executable.suffix.lower() == ".bat":
        try:
            executable.relative_to(root)
        except ValueError:
            return False
        return (
            executable.name.lower() == "start_desktop.bat"
            and executable.is_file()
            and command == [str(executable), "_RUNNING_HIDDEN"]
        )
    if not executable.is_file():
        return False
    try:
        Path(command[1]).resolve().relative_to(root)
    except (IndexError, ValueError):
        return False
    return Path(command[1]).name.lower() == "run_desktop.py"


def launch(command: list[str], *, root: Path) -> None:
    if not _validate_command(command, root=root):
        raise RuntimeError("restart_command_invalid")
    if os.name == "nt" and Path(command[0]).suffix.lower() == ".bat":
        # Start the same hidden batch chain used by the normal desktop shortcut.
        # Its hidden console is inherited by Node/cmd MCP children, preventing a
        # burst of visible terminal windows during the next boot.
        script = (
            "$ErrorActionPreference='Stop';"
            f"$p=Start-Process -FilePath {_powershell_literal(command[0])} "
            f"-ArgumentList {_powershell_literal(command[1])} "
            f"-WorkingDirectory {_powershell_literal(root)} "
            "-WindowStyle Hidden -PassThru;"
            "if(-not $p.Id){exit 2}"
        )
        completed = subprocess.run(
            [
                "powershell.exe", "-NoProfile", "-NonInteractive",
                "-WindowStyle", "Hidden", "-Command", script,
            ],
            cwd=str(root), stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=15, check=False,
        )
        if completed.returncode != 0:
            raise RuntimeError("restart_launch_failed")
        return
    flags = 0
    if os.name == "nt":
        flags = (
            subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
    subprocess.Popen(
        command, cwd=str(root), stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=flags, start_new_session=(os.name != "nt"),
        close_fds=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", required=True)
    args = parser.parse_args(argv)
    if len(args.payload) > 32_768:
        return 2
    try:
        raw = base64.b64decode(args.payload, altchars=b"-_", validate=True)
        payload = json.loads(raw.decode("utf-8"))
        wait_pid = int(payload["wait_pid"])
        root = Path(payload["root"]).resolve()
        command = [str(item) for item in payload["command"]]
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return 2
    if wait_pid <= 0 or not root.is_dir() or not 2 <= len(command) <= 8:
        return 2
    if not _validate_command(command, root=root):
        return 2
    _audit(root, f"scheduled wait_pid={wait_pid}")
    if not wait_for_exit(wait_pid):
        _audit(root, "failed old_process_timeout")
        return 3
    try:
        launch(command, root=root)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        _audit(root, "failed relaunch")
        return 4
    _audit(root, "launched desktop_hidden")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
