"""Verified, atomic and offline-capable Voice Pack installation."""
from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
from typing import Any, Callable, Dict, Mapping, Optional
import zipfile

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_ALLOWED_SUFFIXES = {
    ".onnx", ".json", ".wav", ".txt", ".md", ".model", ".bin",
    ".safetensors", ".tiktoken", ".vocab", ".merges",
}
_DEFAULT_MAX_MANIFEST_BYTES = 1024 * 1024
_DEFAULT_MAX_FILES = 256
_DEFAULT_MAX_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024
_DEFAULT_MAX_COMPRESSION_RATIO = 200.0
_COPY_CHUNK_BYTES = 1024 * 1024


class VoicePackError(RuntimeError):
    pass


def canonical_manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    payload = dict(manifest)
    payload.pop("signature", None)
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _path_digest(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        while True:
            chunk = source.read(_COPY_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _safe_relative_path(value: str) -> PurePosixPath:
    path = PurePosixPath(str(value or ""))
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise VoicePackError(f"chemin de pack interdit: {value!r}")
    if path.suffix.lower() not in _ALLOWED_SUFFIXES:
        raise VoicePackError(f"type de fichier interdit: {value!r}")
    return path


def _validate_manifest(manifest: Mapping[str, Any]) -> None:
    if int(manifest.get("schema_version", 0)) != 1:
        raise VoicePackError("schema Voice Pack non supporté")
    for field in ("pack_id", "version", "engine"):
        if not _SAFE_ID.fullmatch(str(manifest.get(field, ""))):
            raise VoicePackError(f"champ {field} invalide")
    languages = manifest.get("languages")
    if not isinstance(languages, list) or not languages:
        raise VoicePackError("langues du pack absentes")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise VoicePackError("fichiers du pack absents")
    seen = set()
    for item in files:
        if not isinstance(item, dict):
            raise VoicePackError("entrée fichier invalide")
        path = _safe_relative_path(str(item.get("path", "")))
        if str(path) in seen:
            raise VoicePackError(f"fichier dupliqué: {path}")
        seen.add(str(path))
        digest = str(item.get("sha256", ""))
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise VoicePackError(f"empreinte invalide: {path}")
        if int(item.get("size", -1)) < 0:
            raise VoicePackError(f"taille invalide: {path}")
    licenses = manifest.get("licenses")
    if not isinstance(licenses, list) or not licenses:
        raise VoicePackError("licences du pack absentes")
    for license_item in licenses:
        if not isinstance(license_item, dict) or not license_item.get("component") \
                or not license_item.get("license") or not license_item.get("source"):
            raise VoicePackError("preuve de licence incomplète")
    consent = manifest.get("voice_consent")
    if not isinstance(consent, dict) or consent.get("confirmed") is not True \
            or not str(consent.get("rights_scope", "")).strip():
        raise VoicePackError("preuve de consentement vocal absente")


class VoicePackManager:
    def __init__(
        self,
        root: str | Path,
        *,
        trusted_keys: Mapping[str, bytes | str],
        in_use: Optional[Callable[[str, str], bool]] = None,
        max_manifest_bytes: int = _DEFAULT_MAX_MANIFEST_BYTES,
        max_files: int = _DEFAULT_MAX_FILES,
        max_uncompressed_bytes: int = _DEFAULT_MAX_UNCOMPRESSED_BYTES,
        max_compression_ratio: float = _DEFAULT_MAX_COMPRESSION_RATIO,
    ) -> None:
        self.root = Path(root)
        self.packs_dir = self.root / "packs"
        self.active_file = self.root / "active.json"
        self.trusted_keys = dict(trusted_keys)
        self.in_use = in_use or (lambda _pack, _version: False)
        self.max_manifest_bytes = max(1024, int(max_manifest_bytes))
        self.max_files = max(1, int(max_files))
        self.max_uncompressed_bytes = max(1, int(max_uncompressed_bytes))
        self.max_compression_ratio = max(1.0, float(max_compression_ratio))

    def _verify_signature(self, manifest: Mapping[str, Any]) -> None:
        signature = manifest.get("signature")
        if not isinstance(signature, dict) or signature.get("algorithm") != "ed25519":
            raise VoicePackError("signature Ed25519 absente")
        key_id = str(signature.get("key_id", ""))
        raw_key = self.trusted_keys.get(key_id)
        if raw_key is None:
            raise VoicePackError("clé de signature non approuvée")
        try:
            key_bytes = (
                base64.b64decode(raw_key, validate=True)
                if isinstance(raw_key, str) else bytes(raw_key)
            )
            public_key = Ed25519PublicKey.from_public_bytes(key_bytes)
            public_key.verify(
                base64.b64decode(str(signature.get("value", "")), validate=True),
                canonical_manifest_bytes(manifest),
            )
        except Exception as exc:
            raise VoicePackError("signature Voice Pack invalide") from exc

    def _read_manifest(self, archive: zipfile.ZipFile) -> Dict[str, Any]:
        try:
            info = archive.getinfo("manifest.json")
            if info.file_size > self.max_manifest_bytes:
                raise VoicePackError("manifest Voice Pack trop volumineux")
            if info.flag_bits & 0x1:
                raise VoicePackError("les archives chiffrées sont interdites")
            raw = archive.read("manifest.json")
            manifest = json.loads(raw.decode("utf-8"))
        except VoicePackError:
            raise
        except Exception as exc:
            raise VoicePackError("manifest.json absent ou invalide") from exc
        if not isinstance(manifest, dict):
            raise VoicePackError("manifest Voice Pack invalide")
        return manifest

    def _validate_archive_limits(self, archive: zipfile.ZipFile) -> None:
        infos = [info for info in archive.infolist() if not info.is_dir()]
        if len(infos) > self.max_files + 1:  # fichiers déclarés + manifest.json
            raise VoicePackError("Voice Pack contient trop de fichiers")
        total = 0
        for info in infos:
            if info.flag_bits & 0x1:
                raise VoicePackError("les archives chiffrées sont interdites")
            total += int(info.file_size)
            if total > self.max_uncompressed_bytes + self.max_manifest_bytes:
                raise VoicePackError("Voice Pack décompressé trop volumineux")
            if info.file_size:
                if info.compress_size <= 0:
                    raise VoicePackError("ratio de compression Voice Pack invalide")
                ratio = float(info.file_size) / float(info.compress_size)
                if ratio > self.max_compression_ratio:
                    raise VoicePackError("ratio de compression Voice Pack excessif")

    @staticmethod
    def _stream_digest(archive: zipfile.ZipFile, filename: str) -> str:
        digest = hashlib.sha256()
        with archive.open(filename, "r") as source:
            while True:
                chunk = source.read(_COPY_CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
        return digest.hexdigest()

    def inspect(self, archive_path: str | Path) -> Dict[str, Any]:
        with zipfile.ZipFile(archive_path, "r") as archive:
            self._validate_archive_limits(archive)
            manifest = self._read_manifest(archive)
            _validate_manifest(manifest)
            if len(manifest["files"]) > self.max_files:
                raise VoicePackError("Voice Pack contient trop de fichiers")
            self._verify_signature(manifest)
            declared = {str(_safe_relative_path(item["path"])): item for item in manifest["files"]}
            actual = {name for name in archive.namelist() if name != "manifest.json" and not name.endswith("/")}
            if actual != set(declared):
                raise VoicePackError("contenu du pack différent du manifeste")
            for info in archive.infolist():
                if info.filename == "manifest.json" or info.is_dir():
                    continue
                _safe_relative_path(info.filename)
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise VoicePackError("les liens symboliques sont interdits")
                item = declared[info.filename]
                if info.file_size != int(item["size"]):
                    raise VoicePackError(f"taille incorrecte: {info.filename}")
                digest = self._stream_digest(archive, info.filename)
                if digest != item["sha256"]:
                    raise VoicePackError(f"empreinte incorrecte: {info.filename}")
            required = sum(int(item["size"]) for item in manifest["files"])
            if required > self.max_uncompressed_bytes:
                raise VoicePackError("Voice Pack décompressé trop volumineux")
            archive_digest = _path_digest(archive_path)
            return {
                "manifest": manifest,
                "required_bytes": required,
                "archive_sha256": archive_digest,
            }

    def install(self, archive_path: str | Path, *, activate: bool = True) -> Path:
        audit = self.inspect(archive_path)
        manifest = audit["manifest"]
        pack_id = manifest["pack_id"]
        version = manifest["version"]
        target = self.packs_dir / pack_id / version
        self.packs_dir.mkdir(parents=True, exist_ok=True)
        available = shutil.disk_usage(self.packs_dir).free
        required = int(audit["required_bytes"])
        if available < required * 2 + 10 * 1024 * 1024:
            raise VoicePackError("espace disque insuffisant pour installation atomique")
        staging_parent = self.root / ".staging"
        staging_parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="voice-pack-", dir=staging_parent))
        try:
            # Hash and extraction use the same open handle: a package swapped on
            # disk after inspection can never be extracted (TOCTOU boundary).
            with Path(archive_path).open("rb") as package:
                current_digest = hashlib.sha256(package.read()).hexdigest()
                if current_digest != audit["archive_sha256"]:
                    raise VoicePackError("Voice Pack modifié pendant l'installation")
                package.seek(0)
                with zipfile.ZipFile(package, "r") as archive:
                    self._validate_archive_limits(archive)
                    for item in manifest["files"]:
                        rel = _safe_relative_path(item["path"])
                        destination = staging.joinpath(*rel.parts)
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        written = 0
                        with archive.open(str(rel), "r") as source, destination.open("wb") as output:
                            while True:
                                chunk = source.read(_COPY_CHUNK_BYTES)
                                if not chunk:
                                    break
                                written += len(chunk)
                                if written > int(item["size"]):
                                    raise VoicePackError(f"taille extraite incorrecte: {rel}")
                                output.write(chunk)
                        if written != int(item["size"]):
                            raise VoicePackError(f"taille extraite incorrecte: {rel}")
            (staging / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            if target.exists():
                shutil.rmtree(staging)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                os.replace(staging, target)
            if activate:
                self.activate(pack_id, version)
            return target
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise

    def _active_payload(self) -> Dict[str, Any]:
        try:
            value = json.loads(self.active_file.read_text(encoding="utf-8"))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    def activate(self, pack_id: str, version: str) -> None:
        target = self.packs_dir / pack_id / version
        if not target.is_dir():
            raise VoicePackError("Voice Pack non installé")
        current = self._active_payload()
        payload = {
            "pack_id": pack_id,
            "version": version,
            "previous": (
                {"pack_id": current.get("pack_id"), "version": current.get("version")}
                if current.get("pack_id") and current.get("version") else None
            ),
        }
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.active_file.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, self.active_file)

    def rollback(self) -> None:
        current = self._active_payload()
        previous = current.get("previous")
        if not isinstance(previous, dict):
            raise VoicePackError("aucun Voice Pack précédent")
        self.activate(str(previous.get("pack_id", "")), str(previous.get("version", "")))

    def uninstall(self, pack_id: str, version: str) -> None:
        current = self._active_payload()
        if current.get("pack_id") == pack_id and current.get("version") == version:
            raise VoicePackError("impossible de supprimer le Voice Pack actif")
        if self.in_use(pack_id, version):
            raise VoicePackError("Voice Pack actuellement utilisé")
        target = self.packs_dir / pack_id / version
        if target.exists():
            shutil.rmtree(target)
