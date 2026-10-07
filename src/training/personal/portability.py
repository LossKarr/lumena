"""Verified backup and restore of a personal-model lineage."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from src.utils.persistence import atomic_write_json

from .contracts import canonical_json, utc_now


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class PersonalModelArchive:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    def create(self, destination: Path, *, approval_id: str) -> dict[str, Any]:
        if not approval_id:
            raise PermissionError("backup_requires_approval")
        destination = Path(destination).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        excluded_parts = {"approvals", "audit", "quarantine", "backups"}
        files = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.name.endswith(".lock") or any(part in excluded_parts for part in path.relative_to(self.root).parts):
                continue
            relative = path.relative_to(self.root).as_posix()
            files.append({"path": relative, "sha256": _hash_file(path), "size": path.stat().st_size})
        manifest = {"schema_version": 1, "created_at": utc_now(), "files": files}
        fd, temporary_name = tempfile.mkstemp(prefix=destination.name, suffix=".tmp", dir=str(destination.parent))
        os.close(fd)
        temporary = Path(temporary_name)
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", canonical_json(manifest).encode("utf-8"))
                for item in files:
                    archive.write(self.root / item["path"], f"payload/{item['path']}")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return {"archive": str(destination), "file_count": len(files), "manifest_sha256": _hash_bytes(canonical_json(manifest).encode("utf-8"))}

    def restore(self, archive_path: Path, *, approval_id: str) -> dict[str, Any]:
        if not approval_id:
            raise PermissionError("restore_requires_approval")
        archive_path = Path(archive_path).resolve()
        staging_parent = self.root / "backups"
        staging_parent.mkdir(parents=True, exist_ok=True)
        restored = skipped = 0
        with tempfile.TemporaryDirectory(prefix="restore-", dir=staging_parent) as temporary_name:
            staging = Path(temporary_name)
            with zipfile.ZipFile(archive_path, "r") as archive:
                try:
                    manifest_info = archive.getinfo("manifest.json")
                except KeyError as exc:
                    raise ValueError("backup_manifest_missing") from exc
                if manifest_info.file_size > 64 * 1024 * 1024:
                    raise ValueError("backup_manifest_too_large")
                manifest = json.loads(archive.read(manifest_info))
                if manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), list):
                    raise ValueError("backup_manifest_invalid")
                if len(manifest["files"]) > 100_000:
                    raise ValueError("backup_file_count_exceeded")
                validated: list[tuple[dict[str, Any], Path, Path]] = []
                seen: set[str] = set()
                for item in manifest["files"]:
                    if not isinstance(item, dict):
                        raise ValueError("backup_manifest_invalid")
                    relative = Path(str(item.get("path", "")))
                    relative_posix = relative.as_posix()
                    size = item.get("size")
                    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
                        raise ValueError("backup_path_unsafe")
                    if relative_posix in seen:
                        raise ValueError("backup_path_duplicate")
                    seen.add(relative_posix)
                    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
                        raise ValueError("backup_size_invalid")
                    try:
                        member = archive.getinfo(f"payload/{relative_posix}")
                    except KeyError as exc:
                        raise ValueError("backup_payload_missing") from exc
                    if member.file_size != size:
                        raise ValueError("backup_size_mismatch")
                    staged = (staging / relative).resolve()
                    target = (self.root / relative).resolve()
                    if staging != staged and staging not in staged.parents:
                        raise ValueError("backup_path_unsafe")
                    if self.root != target and self.root not in target.parents:
                        raise ValueError("backup_path_unsafe")
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    digest = hashlib.sha256()
                    copied = 0
                    with archive.open(member, "r") as source, staged.open("wb") as destination:
                        while chunk := source.read(1024 * 1024):
                            copied += len(chunk)
                            if copied > size:
                                raise ValueError("backup_size_mismatch")
                            digest.update(chunk)
                            destination.write(chunk)
                    if copied != size or digest.hexdigest() != item.get("sha256"):
                        raise ValueError("backup_hash_mismatch")
                    if target.exists():
                        if not target.is_file() or _hash_file(target) != item["sha256"]:
                            raise FileExistsError(f"restore_conflict:{relative_posix}")
                        skipped += 1
                    else:
                        validated.append((item, staged, target))
            for _item, staged, target in validated:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staged, target)
                restored += 1
        proof = {"schema_version": 1, "archive_sha256": _hash_file(archive_path), "restored": restored, "skipped": skipped, "created_at": utc_now()}
        atomic_write_json(self.root / "backups" / "last-restore.json", proof)
        return proof
