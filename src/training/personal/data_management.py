"""Bounded discovery, backup inventory, and deletion of learning data."""

from __future__ import annotations

import hashlib
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from filelock import FileLock

from .experience_store import ExperienceStore


_LEARNING_DIRS = ("experiences", "index", "datasets", "quarantine", "judge")
_LEGACY_DIRS = ("training_pool", "training_validated", "training_dpo", "training_retrain")
_LEGACY_FILES = ("model_versions.json", "memory/finetuned_models.json", "ops/finetuning_job.json")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PersonalDataManager:
    """Manage only user-owned learning data below the personal-model root."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    def list_backups(self) -> list[dict[str, Any]]:
        backup_root = self.root / "backups"
        if not backup_root.is_dir():
            return []
        result = []
        for path in sorted(backup_root.glob("*.lumena-model.zip"), reverse=True)[:100]:
            stat = path.stat()
            result.append({
                "name": path.name,
                "size_bytes": stat.st_size,
                "sha256": _sha256_file(path),
                "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            })
        return result

    def discover_migration_sources(self) -> list[dict[str, Any]]:
        data_root = self.root.parent.resolve()
        candidates: set[Path] = set()
        for name in _LEGACY_DIRS:
            directory = data_root / name
            if directory.is_dir():
                candidates.update(path.resolve() for path in directory.rglob("*.jsonl") if path.is_file())
        for relative in _LEGACY_FILES:
            path = (data_root / relative).resolve()
            if path.is_file():
                candidates.add(path)
        result = []
        for path in sorted(candidates)[:500]:
            if self.root == path or self.root in path.parents:
                continue
            if data_root != path and data_root not in path.parents:
                continue
            stat = path.stat()
            result.append({
                "path": path.relative_to(data_root).as_posix(),
                "size_bytes": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
            })
        return result

    def delete_learning_data(self, owner_scope: str, *, approval_id: str) -> dict[str, Any]:
        if not approval_id:
            raise PermissionError("learning_data_delete_requires_approval")
        base = ExperienceStore(self.root).owner_root(owner_scope).resolve()
        if self.root != base and self.root not in base.parents:
            raise PermissionError("learning_data_owner_path_invalid")
        base.mkdir(parents=True, exist_ok=True)
        lock = FileLock(str(base / ".experience_store.lock"), timeout=10)
        deleted_files = 0
        deleted_bytes = 0
        with lock:
            for name in _LEARNING_DIRS:
                target = (base / name).resolve()
                if base != target and base not in target.parents:
                    raise PermissionError("learning_data_path_invalid")
                if not target.exists():
                    continue
                files = [target] if target.is_file() else [item for item in target.rglob("*") if item.is_file()]
                deleted_files += len(files)
                deleted_bytes += sum(item.stat().st_size for item in files)
                if target.is_dir():
                    shutil.rmtree(target)
                else:
                    target.unlink()
        remaining = [name for name in _LEARNING_DIRS if (base / name).exists()]
        if remaining:
            raise RuntimeError("learning_data_delete_incomplete")
        return {"deleted_files": deleted_files, "deleted_bytes": deleted_bytes, "remaining": remaining}
