"""Canonical Lumena version and immutable build identity helpers.

``__version__`` is the only hand-edited product version. Release builds add a
``build-info.json`` file at the repository/application root so an installed
runtime can prove which commit and managed-file manifest it is running.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

__version__ = "1.0.52"
VERSION = __version__
MIN_UPDATE_VERSION = "1.0.47"
BUILD_INFO_FILENAME = "build-info.json"
MANAGED_FILES_FILENAME = "managed-files.json"
DEFAULT_GUARD_SMOKE_PROFILE = "update-v1"


@dataclass(frozen=True)
class BuildIdentity:
    version: str
    commit: str
    managed_manifest_sha256: str
    guard_smoke_profile: str
    data_schema_version: int

    def as_dict(self) -> dict[str, str | int]:
        return asdict(self)


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError:
        return ""
    return digest.hexdigest()


def _safe_schema_version(value: object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 1
    return max(1, parsed)


def get_build_identity(root: Path | None = None) -> BuildIdentity:
    """Return the identity used by health checks and the detached updater.

    Environment values are useful for CI and packaged launchers. The immutable
    build file is the normal installed source. Development checkouts fall back
    to explicit, non-certified values rather than inventing a release identity.
    """

    app_root = (root or Path(__file__).resolve().parent.parent).resolve()
    info = _read_json_object(app_root / BUILD_INFO_FILENAME)
    managed_path = app_root / MANAGED_FILES_FILENAME

    commit = str(
        os.getenv("LUMENA_BUILD_COMMIT")
        or info.get("commit")
        or _git_commit(app_root)
        or "development"
    ).strip()
    managed_sha = str(
        os.getenv("LUMENA_MANAGED_MANIFEST_SHA256")
        or info.get("managed_manifest_sha256")
        or _sha256_file(managed_path)
    ).strip().lower()
    profile = str(
        os.getenv("LUMENA_GUARD_SMOKE_PROFILE")
        or info.get("guard_smoke_profile")
        or DEFAULT_GUARD_SMOKE_PROFILE
    ).strip()
    schema = _safe_schema_version(
        os.getenv("LUMENA_DATA_SCHEMA_VERSION")
        or info.get("data_schema_version")
        or 1
    )

    return BuildIdentity(
        version=__version__,
        commit=commit,
        managed_manifest_sha256=managed_sha,
        guard_smoke_profile=profile,
        data_schema_version=schema,
    )


def _git_commit(root: Path) -> str:
    if not (root / ".git").exists():
        return ""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=3, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    value = (result.stdout or "").strip().lower()
    return value if len(value) == 40 and all(char in "0123456789abcdef" for char in value) else ""


__all__ = [
    "BUILD_INFO_FILENAME",
    "BuildIdentity",
    "DEFAULT_GUARD_SMOKE_PROFILE",
    "MANAGED_FILES_FILENAME",
    "MIN_UPDATE_VERSION",
    "VERSION",
    "__version__",
    "get_build_identity",
]
