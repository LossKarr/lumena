"""Build deterministic, attestable Lumena lightweight update artifacts."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.version import DEFAULT_GUARD_SMOKE_PROFILE, MIN_UPDATE_VERSION, __version__  # noqa: E402

UPDATE_ASSET = "lumena-update-windows-x64.zip"
MANAGED_MANIFEST = "managed-files.json"
BUILD_INFO = "build-info.json"
PROTECTED_TOP_LEVEL = frozenset(
    {".env", "data", "workspace", "models", "backups", "output", "venv", ".venv"}
)
PAYLOAD_ROOTS = ("src", "web", "skills", "assets", "scripts")
PAYLOAD_FILES = (
    ".env.example",
    ".lumena_rules",
    "CHANGELOG.md",
    "Dockerfile",
    "Dockerfile.sandbox",
    "HEARTBEAT.md",
    "INSTALL.bat",
    "LICENSE",
    "README.md",
    "START.bat",
    "START_DESKTOP.bat",
    "docker-compose.yml",
    "lumena_ultime.py",
    "pyproject.toml",
    "pytest.ini",
    "requirements-lock.txt",
    "requirements.txt",
    "run_daemon.py",
    "run_desktop.py",
    "run_telegram.py",
    "run_twitter.py",
    "run_whatsapp.py",
)
_IGNORED_NAMES = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
_IGNORED_SUFFIXES = (".pyc", ".pyo", ".tmp", ".log")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(data: object) -> bytes:
    return (json.dumps(data, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _is_payload_file(root: Path, path: Path) -> bool:
    rel = path.relative_to(root)
    if any(part in _IGNORED_NAMES for part in rel.parts):
        return False
    if path.name.startswith(".") and path.name not in {".env.example", ".lumena_rules"}:
        return False
    return not path.name.lower().endswith(_IGNORED_SUFFIXES)


def collect_payload(root: Path) -> list[tuple[str, Path]]:
    """Return a sorted explicit allowlist of release-managed files."""
    root = root.resolve()
    found: list[tuple[str, Path]] = []
    for dirname in PAYLOAD_ROOTS:
        base = root / dirname
        if not base.is_dir():
            raise FileNotFoundError(f"Required payload directory missing: {dirname}")
        for path in base.rglob("*"):
            if path.is_symlink():
                raise ValueError(f"Symlinks are forbidden in update payloads: {path}")
            if path.is_file() and _is_payload_file(root, path):
                found.append((path.relative_to(root).as_posix(), path))
    for filename in PAYLOAD_FILES:
        path = root / filename
        if path.is_file():
            found.append((filename, path))

    unique = {rel: path for rel, path in found}
    for rel in unique:
        parts = PurePosixPath(rel).parts
        if not parts or parts[0].lower() in PROTECTED_TOP_LEVEL or ".." in parts:
            raise ValueError(f"Protected or invalid payload path: {rel}")
    return sorted(unique.items())


def build_managed_manifest(payload: Iterable[tuple[str, Path]]) -> dict[str, object]:
    files = [
        {"path": rel, "size": path.stat().st_size, "sha256": sha256_file(path)}
        for rel, path in payload
    ]
    return {"schema_version": 1, "version": __version__, "files": files}


def _zip_write_bytes(archive: zipfile.ZipFile, name: str, content: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    archive.writestr(info, content)


def build_release_artifacts(
    *, root: Path, output: Path, commit: str, ci_run_id: int,
    ci_run_url: str, release_notes_url: str, published_at: str | None = None,
) -> dict[str, Path]:
    if not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise ValueError("commit must be the exact 40-character Git SHA")
    if ci_run_id <= 0 or not ci_run_url.startswith("https://github.com/"):
        raise ValueError("a valid successful GitHub Actions run is required")

    root = root.resolve()
    output.mkdir(parents=True, exist_ok=True)
    payload = collect_payload(root)
    managed = build_managed_manifest(payload)
    managed_bytes = _canonical_json(managed)
    managed_sha = hashlib.sha256(managed_bytes).hexdigest()
    requirements_sha = sha256_file(root / "requirements-lock.txt")
    build_info = {
        "version": __version__, "commit": commit.lower(),
        "managed_manifest_sha256": managed_sha,
        "guard_smoke_profile": DEFAULT_GUARD_SMOKE_PROFILE,
        "data_schema_version": 1,
    }

    archive_path = output / UPDATE_ASSET
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for rel, path in payload:
            _zip_write_bytes(archive, rel, path.read_bytes())
        _zip_write_bytes(archive, MANAGED_MANIFEST, managed_bytes)
        _zip_write_bytes(archive, BUILD_INFO, _canonical_json(build_info))

    archive_sha = sha256_file(archive_path)
    timestamp = published_at or datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    manifest = {
        "schema_version": 1, "version": __version__, "channel": "stable",
        "published_at": timestamp, "commit": commit.lower(),
        "asset_name": UPDATE_ASSET, "asset_size": archive_path.stat().st_size,
        "sha256": archive_sha, "min_supported_version": MIN_UPDATE_VERSION, "python": "3.12",
        "requirements_lock_sha256": requirements_sha, "data_schema_version": 1,
        "reads_data_schema_min": 1, "reads_data_schema_max": 1,
        "downgrade_supported": True,
        "ci": {"workflow": "Certified Release - Lumena", "run_id": ci_run_id,
               "run_url": ci_run_url, "conclusion": "success", "commit": commit.lower()},
        "guard_smoke_profile": DEFAULT_GUARD_SMOKE_PROFILE,
        "managed_files_sha256": managed_sha, "restart_required": True,
        "release_notes_url": release_notes_url,
    }
    certification = {
        "schema_version": 1, "version": __version__, "commit": commit.lower(),
        "ci_run_id": ci_run_id, "ci_run_url": ci_run_url, "ci_conclusion": "success",
        "full_regression_required": True, "guard_smoke_profile": DEFAULT_GUARD_SMOKE_PROFILE,
        "asset_sha256": archive_sha, "managed_files_sha256": managed_sha,
    }
    manifest_path = output / "update-manifest.json"
    certification_path = output / "release-certification.json"
    checksum_path = output / f"{UPDATE_ASSET}.sha256"
    manifest_path.write_bytes(_canonical_json(manifest))
    certification_path.write_bytes(_canonical_json(certification))
    checksum_path.write_text(f"{archive_sha}  {UPDATE_ASSET}\n", encoding="ascii")
    return {"archive": archive_path, "manifest": manifest_path,
            "certification": certification_path, "checksum": checksum_path}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--ci-run-id", type=int, required=True)
    parser.add_argument("--ci-run-url", required=True)
    parser.add_argument("--release-notes-url", required=True)
    args = parser.parse_args()
    artifacts = build_release_artifacts(
        root=args.root, output=args.output, commit=args.commit,
        ci_run_id=args.ci_run_id, ci_run_url=args.ci_run_url,
        release_notes_url=args.release_notes_url,
    )
    for name, path in artifacts.items():
        print(f"{name}={path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
