"""Verify and repair Lumena's production ``venv`` from the signed wheelhouse.

The guard intentionally uses only the Python standard library so it can still
run when an application dependency is missing.  It never reads or writes
``.env`` and never accesses the network: repairs are restricted to the bundled
wheelhouse and the exact ``requirements-lock.txt`` contract.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import struct
import subprocess
import sys
import time
from contextlib import contextmanager
from importlib import metadata
from pathlib import Path
from typing import Callable, Iterator, Mapping, Sequence


SCHEMA_VERSION = 1
STATE_FILENAME = ".lumena-runtime-state.json"
LOCK_FILENAME = ".lumena-runtime-repair.lock"
CRITICAL_IMPORTS = (
    "fastapi",
    "httpx",
    "playwright",
    "webview",
    "chromadb_rust_bindings",
    "faster_whisper",
    "piper",
)


class RuntimeGuardError(RuntimeError):
    """A bounded, user-displayable runtime verification failure."""


def _normalise_distribution(name: str) -> str:
    return re.sub(r"[-_.]+", "-", str(name or "")).lower()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_exact_lock(path: Path) -> dict[str, str]:
    """Read the generated exact lock and reject an ambiguous repair contract."""
    if not path.is_file():
        raise RuntimeGuardError("requirements-lock.txt est absent")
    locked: dict[str, str] = {}
    for line_number, raw in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), start=1
    ):
        value = raw.strip()
        if not value or value.startswith("#"):
            continue
        if "==" not in value or value.count("==") != 1:
            raise RuntimeGuardError(
                f"Lock non exact à la ligne {line_number}; réparation refusée"
            )
        name, version = (part.strip() for part in value.split("==", 1))
        if not name or not version:
            raise RuntimeGuardError(f"Lock invalide à la ligne {line_number}")
        locked[_normalise_distribution(name)] = version
    if not locked:
        raise RuntimeGuardError("requirements-lock.txt est vide")
    return locked


def installed_versions() -> dict[str, str]:
    result: dict[str, str] = {}
    for distribution in metadata.distributions():
        name = distribution.metadata.get("Name")
        if name:
            result[_normalise_distribution(name)] = distribution.version
    return result


def compare_versions(
    locked: Mapping[str, str], installed: Mapping[str, str]
) -> tuple[list[str], list[str]]:
    missing = [
        f"{name}=={version}"
        for name, version in sorted(locked.items())
        if name not in installed
    ]
    mismatched = [
        f"{name}: {installed[name]} != {version}"
        for name, version in sorted(locked.items())
        if name in installed and installed[name] != version
    ]
    return missing, mismatched


def missing_critical_imports(
    finder: Callable[[str], object | None] = importlib.util.find_spec,
) -> list[str]:
    missing: list[str] = []
    for module in CRITICAL_IMPORTS:
        try:
            if finder(module) is None:
                missing.append(module)
        except (ImportError, ModuleNotFoundError, ValueError):
            missing.append(module)
    return missing


def resolve_wheelhouse(app_dir: Path) -> Path:
    for candidate in (app_dir / "wheelhouse", app_dir / "installer" / "wheelhouse"):
        if candidate.is_dir():
            return candidate
    raise RuntimeGuardError("wheelhouse hors ligne absent")


def verify_wheelhouse(wheelhouse: Path, lock_sha256: str) -> None:
    stamp = wheelhouse / ".requirements.sha256"
    if not stamp.is_file():
        raise RuntimeGuardError("empreinte du wheelhouse absente")
    if stamp.read_text(encoding="ascii").strip().lower() != lock_sha256.lower():
        raise RuntimeGuardError("wheelhouse différent du lock courant")


def _state_path(app_dir: Path) -> Path:
    return app_dir / "venv" / STATE_FILENAME


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _append_log(app_dir: Path, message: str) -> None:
    path = app_dir / "data" / "logs" / "runtime-repair.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    safe = str(message).replace("\r", " ").replace("\n", " ")[:1000]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {safe}\n")


def _expected_python(app_dir: Path) -> Path:
    return app_dir / "venv" / "Scripts" / "python.exe"


def _assert_production_python(app_dir: Path) -> None:
    expected = _expected_python(app_dir)
    if not expected.is_file():
        raise RuntimeGuardError("venv\\Scripts\\python.exe est absent")
    if Path(sys.executable).resolve() != expected.resolve():
        raise RuntimeGuardError("le garde doit être exécuté par le venv produit")
    if sys.version_info[:2] != (3, 12) or struct.calcsize("P") != 8:
        raise RuntimeGuardError("Lumena requiert Python 3.12 x64")


def _lock_is_stale(path: Path, *, stale_after_s: float) -> bool:
    try:
        return time.time() - path.stat().st_mtime > stale_after_s
    except OSError:
        return False


@contextmanager
def repair_lock(
    app_dir: Path, *, timeout_s: float = 120.0, stale_after_s: float = 1800.0
) -> Iterator[None]:
    """Serialise repairs without requiring any third-party lock package."""
    path = app_dir / "venv" / LOCK_FILENAME
    deadline = time.monotonic() + max(1.0, timeout_s)
    descriptor: int | None = None
    while descriptor is None:
        try:
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(descriptor, f"{os.getpid()} {time.time():.3f}\n".encode("ascii"))
        except FileExistsError:
            if _lock_is_stale(path, stale_after_s=stale_after_s):
                try:
                    path.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise RuntimeGuardError("une autre réparation du runtime est en cours")
            time.sleep(0.2)
    try:
        yield
    finally:
        try:
            if descriptor is not None:
                os.close(descriptor)
        finally:
            try:
                path.unlink()
            except OSError:
                pass


def _run(
    command: Sequence[str], *, cwd: Path, timeout_s: float
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(command),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_s,
            check=False,
            creationflags=(0x08000000 if os.name == "nt" else 0),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeGuardError("timeout pendant la réparation du runtime") from exc


def _pip_check(app_dir: Path) -> None:
    result = _run(
        [sys.executable, "-m", "pip", "check"], cwd=app_dir, timeout_s=120.0
    )
    if result.returncode == 0:
        return

    detail = (result.stdout or result.stderr or "pip check a échoué").strip()
    locked = parse_exact_lock(app_dir / "requirements-lock.txt")
    blocking: list[str] = []
    optional: list[str] = []
    for line in detail.splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)\s", line.strip())
        if match is None:
            blocking.append(line)
        elif _normalise_distribution(match.group(1)) in locked:
            blocking.append(line)
        else:
            optional.append(line)

    if blocking:
        raise RuntimeGuardError("; ".join(blocking)[:500])
    if optional:
        _append_log(
            app_dir,
            "Conflit(s) d'extension optionnelle hors lock principal: "
            + "; ".join(optional),
        )


def _native_import_probe(app_dir: Path) -> None:
    """Load native production modules once per lock, in a disposable process.

    ``find_spec`` proves that metadata exists but does not prove that a Windows
    DLL can be loaded.  Running this only when the lock changes keeps ordinary
    launches fast and prevents a broken native wheel from reaching the UI.
    """
    modules = (
        "chromadb_rust_bindings", "ctranslate2", "faster_whisper", "piper", "webview"
    )
    code = "; ".join(f"import {module}" for module in modules)
    result = _run([sys.executable, "-c", code], cwd=app_dir, timeout_s=90.0)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "échec import natif").strip()
        _append_log(app_dir, detail[-4000:])
        raise RuntimeGuardError(
            "un composant natif du runtime ne charge pas; consultez runtime-repair.log"
        )


def _repair_from_wheelhouse(
    app_dir: Path, wheelhouse: Path, requirements: Path
) -> None:
    result = _run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--no-index",
            "--find-links",
            str(wheelhouse),
            "-r",
            str(requirements),
        ],
        cwd=app_dir,
        timeout_s=1800.0,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "pip install a échoué").strip()
        _append_log(app_dir, detail[-4000:])
        raise RuntimeGuardError("réparation hors ligne impossible; consultez runtime-repair.log")


def verify_runtime(app_dir: Path) -> dict[str, object]:
    app_dir = app_dir.resolve()
    _assert_production_python(app_dir)
    requirements = app_dir / "requirements-lock.txt"
    locked = parse_exact_lock(requirements)
    missing, mismatched = compare_versions(locked, installed_versions())
    imports = missing_critical_imports()
    return {
        "schema_version": SCHEMA_VERSION,
        "lock_sha256": sha256_file(requirements),
        "locked_count": len(locked),
        "missing": missing,
        "mismatched": mismatched,
        "missing_imports": imports,
        "ready": not missing and not mismatched and not imports,
    }


def ensure_runtime(app_dir: Path, *, repair: bool) -> dict[str, object]:
    """Return a proof of alignment, repairing only from the local wheelhouse."""
    app_dir = app_dir.resolve()
    report = verify_runtime(app_dir)
    state_path = _state_path(app_dir)
    if report["ready"]:
        previous: dict[str, object] = {}
        try:
            previous = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            pass
        if previous.get("lock_sha256") != report["lock_sha256"]:
            _pip_check(app_dir)
            _native_import_probe(app_dir)
            _write_json_atomic(state_path, {
                "schema_version": SCHEMA_VERSION,
                "lock_sha256": report["lock_sha256"],
                "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "locked_count": report["locked_count"],
                "verified_at": int(time.time()),
            })
        report.update({"action": "verified", "repaired": False})
        return report

    if not repair:
        report.update({"action": "repair_required", "repaired": False})
        return report

    requirements = app_dir / "requirements-lock.txt"
    wheelhouse = resolve_wheelhouse(app_dir)
    verify_wheelhouse(wheelhouse, str(report["lock_sha256"]))
    with repair_lock(app_dir):
        # A concurrent launcher may have completed the repair while we waited.
        current = verify_runtime(app_dir)
        if not current["ready"]:
            _append_log(
                app_dir,
                f"Réparation requise: {len(current['missing'])} absent(s), "
                f"{len(current['mismatched'])} version(s) divergente(s), "
                f"{len(current['missing_imports'])} import(s) absent(s)",
            )
            _repair_from_wheelhouse(app_dir, wheelhouse, requirements)
        repaired = verify_runtime(app_dir)
        if not repaired["ready"]:
            raise RuntimeGuardError("le runtime reste différent du lock après réparation")
        _pip_check(app_dir)
        _native_import_probe(app_dir)
        _write_json_atomic(state_path, {
            "schema_version": SCHEMA_VERSION,
            "lock_sha256": repaired["lock_sha256"],
            "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            "locked_count": repaired["locked_count"],
            "verified_at": int(time.time()),
        })
        repaired.update({"action": "repaired", "repaired": True})
        _append_log(app_dir, "Runtime aligné au lock depuis le wheelhouse hors ligne")
        return repaired


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vérifie le runtime produit Lumena")
    parser.add_argument("--app-dir", type=Path, default=Path.cwd())
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    try:
        report = ensure_runtime(args.app_dir, repair=args.repair)
        if args.as_json:
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        elif report["ready"]:
            label = "réparé" if report.get("repaired") else "vérifié"
            print(f"[OK] Runtime Lumena {label} ({report['locked_count']} dépendances)")
        else:
            print("[ERREUR] Runtime Lumena incomplet; réparation requise", file=sys.stderr)
        return 0 if report["ready"] else 2
    except RuntimeGuardError as exc:
        try:
            _append_log(args.app_dir.resolve(), f"ERREUR: {exc}")
        except OSError:
            pass
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
