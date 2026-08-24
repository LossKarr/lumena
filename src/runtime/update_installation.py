"""Transactional application and rollback for certified lightweight updates."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping

from src.runtime.update_manifest import ReleaseManifest, UpdateManifestError, sha256_path
from src.utils.persistence import atomic_write_json, safe_read_json

PROTECTED_TOP_LEVEL = frozenset({
    ".env", ".git", "data", "workspace", "models", "backups", "output",
    "venv", ".venv", "installer",
})
_TRANSACTION_FILE = "transaction.json"


class UpdateInstallationError(RuntimeError):
    pass


@dataclass(frozen=True)
class TransactionFile:
    path: str
    existed: bool
    previous_sha256: str | None


@dataclass(frozen=True)
class UpdateTransaction:
    schema_version: int
    root: str
    version: str
    commit: str
    payload_dir: str
    snapshot_dir: str
    files: tuple[str, ...]
    remove: tuple[str, ...]
    rollback_files: tuple[TransactionFile, ...]

    def as_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["rollback_files"] = [asdict(item) for item in self.rollback_files]
        return value

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "UpdateTransaction":
        if value.get("schema_version") != 1:
            raise UpdateInstallationError("transaction schema invalide")
        rollback = value.get("rollback_files")
        if not isinstance(rollback, list):
            raise UpdateInstallationError("rollback transaction invalide")
        return cls(
            schema_version=1, root=str(value.get("root") or ""),
            version=str(value.get("version") or ""), commit=str(value.get("commit") or ""),
            payload_dir=str(value.get("payload_dir") or ""),
            snapshot_dir=str(value.get("snapshot_dir") or ""),
            files=tuple(str(item) for item in value.get("files", [])),
            remove=tuple(str(item) for item in value.get("remove", [])),
            rollback_files=tuple(TransactionFile(
                path=str(item.get("path") or ""), existed=bool(item.get("existed")),
                previous_sha256=str(item.get("previous_sha256")) if item.get("previous_sha256") else None,
            ) for item in rollback if isinstance(item, Mapping)),
        )


def safe_managed_path(value: str) -> str:
    raw = str(value or "")
    if not raw or "\\" in raw or ":" in raw:
        raise UpdateInstallationError(f"chemin gere invalide: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise UpdateInstallationError(f"chemin gere invalide: {raw!r}")
    if path.parts[0].lower() in PROTECTED_TOP_LEVEL:
        raise UpdateInstallationError(f"chemin protege dans le paquet: {raw}")
    return path.as_posix()


def load_managed_files(path: Path) -> dict[str, dict[str, Any]]:
    value = safe_read_json(path, default={})
    if not isinstance(value, Mapping) or value.get("schema_version") != 1:
        return {}
    result: dict[str, dict[str, Any]] = {}
    for item in value.get("files", []):
        if not isinstance(item, Mapping):
            raise UpdateInstallationError("entree de manifeste gere invalide")
        rel = safe_managed_path(str(item.get("path") or ""))
        digest = str(item.get("sha256") or "").lower()
        if len(digest) != 64:
            raise UpdateInstallationError(f"checksum gere invalide: {rel}")
        result[rel] = dict(item)
    return result


def prepare_transaction(
    *, root: Path, archive: Path, release_manifest_path: Path,
    transaction_dir: Path,
) -> Path:
    root = root.resolve()
    archive = archive.resolve()
    transaction_dir = transaction_dir.resolve()
    if transaction_dir == root:
        raise UpdateInstallationError("le staging ne peut pas etre la racine applicative")
    manifest = ReleaseManifest.from_json(release_manifest_path.read_bytes())
    if sha256_path(archive) != manifest.sha256:
        raise UpdateInstallationError("archive differente du manifeste certifie")

    if transaction_dir.exists():
        shutil.rmtree(transaction_dir)
    payload_dir = transaction_dir / "payload"
    snapshot_dir = transaction_dir / "snapshot"
    payload_dir.mkdir(parents=True)
    snapshot_dir.mkdir(parents=True)
    _safe_extract(archive, payload_dir)

    managed_path = payload_dir / "managed-files.json"
    build_info_path = payload_dir / "build-info.json"
    if not managed_path.is_file() or not build_info_path.is_file():
        raise UpdateInstallationError("paquet incomplet: identite ou manifeste gere absent")
    if sha256_path(managed_path) != manifest.managed_files_sha256:
        raise UpdateInstallationError("manifeste des fichiers geres invalide")
    build_info = safe_read_json(build_info_path, default={})
    if build_info.get("version") != str(manifest.version) or build_info.get("commit") != manifest.commit:
        raise UpdateInstallationError("identite du paquet differente de la release")

    target_files = load_managed_files(managed_path)
    target_files["managed-files.json"] = {
        "path": "managed-files.json", "size": managed_path.stat().st_size,
        "sha256": sha256_path(managed_path),
    }
    target_files["build-info.json"] = {
        "path": "build-info.json", "size": build_info_path.stat().st_size,
        "sha256": sha256_path(build_info_path),
    }
    for rel, item in target_files.items():
        source = payload_dir / Path(rel)
        if not source.is_file() or source.stat().st_size != int(item.get("size") or -1):
            raise UpdateInstallationError(f"fichier gere absent ou tronque: {rel}")
        if sha256_path(source) != str(item.get("sha256") or "").lower():
            raise UpdateInstallationError(f"checksum fichier gere invalide: {rel}")

    current_files = load_managed_files(root / "managed-files.json")
    current_files.update({name: {} for name in ("managed-files.json", "build-info.json") if (root / name).exists()})
    affected = sorted(set(target_files) | set(current_files))
    rollback: list[TransactionFile] = []
    for rel in affected:
        safe_managed_path(rel)
        current = root / Path(rel)
        if current.is_file():
            destination = snapshot_dir / Path(rel)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(current, destination)
            rollback.append(TransactionFile(rel, True, sha256_path(current)))
        else:
            rollback.append(TransactionFile(rel, False, None))

    transaction = UpdateTransaction(
        schema_version=1, root=str(root), version=str(manifest.version), commit=manifest.commit,
        payload_dir=str(payload_dir), snapshot_dir=str(snapshot_dir),
        files=tuple(sorted(target_files)), remove=tuple(sorted(set(current_files) - set(target_files))),
        rollback_files=tuple(rollback),
    )
    plan_path = transaction_dir / _TRANSACTION_FILE
    atomic_write_json(plan_path, transaction.as_dict())
    return plan_path


def apply_transaction(plan_path: Path) -> UpdateTransaction:
    transaction = _load_transaction(plan_path)
    root = Path(transaction.root).resolve()
    payload = Path(transaction.payload_dir).resolve()
    try:
        for rel in transaction.files:
            safe_managed_path(rel)
            _atomic_copy(payload / Path(rel), root / Path(rel))
        for rel in transaction.remove:
            safe_managed_path(rel)
            target = root / Path(rel)
            if target.is_file():
                target.unlink()
        _prune_empty_managed_dirs(root, transaction.remove)
    except Exception as exc:
        rollback_transaction(plan_path)
        raise UpdateInstallationError(f"application echouee, rollback execute: {exc}") from exc
    return transaction


def rollback_transaction(plan_path: Path) -> UpdateTransaction:
    transaction = _load_transaction(plan_path)
    root = Path(transaction.root).resolve()
    snapshot = Path(transaction.snapshot_dir).resolve()
    for item in reversed(transaction.rollback_files):
        rel = safe_managed_path(item.path)
        target = root / Path(rel)
        if item.existed:
            source = snapshot / Path(rel)
            if not source.is_file() or sha256_path(source) != item.previous_sha256:
                raise UpdateInstallationError(f"snapshot rollback invalide: {rel}")
            _atomic_copy(source, target)
        elif target.is_file():
            target.unlink()
    return transaction


def git_checkout_ready(root: Path) -> tuple[bool, str]:
    if not (root / ".git").exists():
        return False, "ce dossier n'est pas un checkout Git"
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=root, capture_output=True,
        text=True, encoding="utf-8", errors="replace", timeout=20, check=False,
    )
    if result.returncode != 0:
        return False, "etat Git illisible"
    if result.stdout.strip():
        return False, "depot Git modifie: mise a jour refusee sans stash/reset"
    return True, "checkout Git propre"


def git_fast_forward_to(root: Path, commit: str) -> None:
    ready, reason = git_checkout_ready(root)
    if not ready:
        raise UpdateInstallationError(reason)
    for command in (["git", "fetch", "origin"], ["git", "merge", "--ff-only", commit]):
        result = subprocess.run(
            command, cwd=root, capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=180, check=False,
        )
        if result.returncode != 0:
            raise UpdateInstallationError((result.stderr or result.stdout or "Git fast-forward refuse").strip())


def _safe_extract(archive_path: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive_path) as archive:
        for info in archive.infolist():
            rel = safe_managed_path(info.filename.rstrip("/"))
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise UpdateInstallationError(f"lien symbolique interdit: {rel}")
            if info.is_dir():
                (destination / Path(rel)).mkdir(parents=True, exist_ok=True)
                continue
            target = destination / Path(rel)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _atomic_copy(source: Path, target: Path) -> None:
    if not source.is_file():
        raise UpdateInstallationError(f"source transaction absente: {source.name}")
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".update", dir=target.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        shutil.copy2(source, temp)
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)


def _load_transaction(path: Path) -> UpdateTransaction:
    value = safe_read_json(path, default={})
    transaction = UpdateTransaction.from_mapping(value)
    if Path(transaction.root).resolve() == Path(transaction.payload_dir).resolve():
        raise UpdateInstallationError("transaction auto-referente invalide")
    return transaction


def _prune_empty_managed_dirs(root: Path, removed: Iterable[str]) -> None:
    directories = sorted({(root / Path(rel)).parent for rel in removed}, key=lambda p: len(p.parts), reverse=True)
    for directory in directories:
        if directory == root:
            continue
        try:
            directory.rmdir()
        except OSError:
            pass
