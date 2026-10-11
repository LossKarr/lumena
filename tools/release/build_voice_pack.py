"""Build reproducible, signed Lumena Voice Packs for release.

The private Ed25519 key is encrypted, lives outside the repository and is never
copied into the archive or printed.  The resulting archive is re-opened through
the product verifier before it is published.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import tempfile
from typing import Any, Mapping, Sequence
import zipfile

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))


_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_ALLOWED_SUFFIXES = {
    ".onnx", ".json", ".wav", ".txt", ".md", ".model", ".bin",
    ".safetensors", ".tiktoken", ".vocab", ".merges",
}
_COPY_CHUNK_BYTES = 1024 * 1024
_ZIP_TIMESTAMP = (2026, 1, 1, 0, 0, 0)


class VoicePackBuildError(RuntimeError):
    """A bounded release error that never contains key material."""


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _safe_id(value: str, *, field: str) -> str:
    result = str(value or "").strip()
    if not _SAFE_ID.fullmatch(result):
        raise VoicePackBuildError(f"champ {field} invalide")
    return result


def _safe_relative_path(value: str) -> PurePosixPath:
    raw = str(value or "")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or "\\" in raw:
        raise VoicePackBuildError(f"chemin de payload interdit: {raw!r}")
    if path.suffix.lower() not in _ALLOWED_SUFFIXES:
        raise VoicePackBuildError(f"type de payload interdit: {raw!r}")
    return path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(_COPY_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_atomic(path: Path, payload: bytes, *, mode: int = 0o644) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _password(name: str) -> bytes:
    value = os.getenv(name, "")
    if len(value) < 16:
        raise VoicePackBuildError(
            f"{name} doit contenir au moins 16 caractères pour chiffrer la clé"
        )
    return value.encode("utf-8")


def _public_raw(private_key: Ed25519PrivateKey) -> bytes:
    return private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )


def generate_release_key(
    private_key_path: str | Path,
    public_key_path: str | Path,
    *,
    key_id: str,
    password_env: str = "LUMENA_VOICE_PACK_SIGNING_KEY_PASSWORD",
) -> dict[str, Any]:
    """Create one encrypted release key outside the repository, atomically."""
    key_id = _safe_id(key_id, field="key_id")
    private_path = Path(private_key_path).expanduser().resolve()
    public_path = Path(public_key_path).expanduser().resolve()
    if _inside(private_path, ROOT_DIR):
        raise VoicePackBuildError("la clé privée doit rester hors du dépôt Lumena")
    if private_path.exists() or public_path.exists():
        raise VoicePackBuildError("un fichier de clé existe déjà; aucun écrasement autorisé")
    password = _password(password_env)
    private = Ed25519PrivateKey.generate()
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.BestAvailableEncryption(password),
    )
    public_b64 = base64.b64encode(_public_raw(private)).decode("ascii")
    trust = json.dumps(
        {"schema_version": 1, "keys": {key_id: public_b64}},
        ensure_ascii=True,
        sort_keys=True,
        indent=2,
    ).encode("utf-8") + b"\n"
    _write_atomic(private_path, pem, mode=0o600)
    try:
        _write_atomic(public_path, trust)
    except Exception:
        private_path.unlink(missing_ok=True)
        raise
    return {
        "key_id": key_id,
        "public_key_file": str(public_path),
        "public_key_base64": public_b64,
    }


def _load_private_key(path: str | Path, *, password_env: str) -> Ed25519PrivateKey:
    key_path = Path(path).expanduser().resolve()
    if _inside(key_path, ROOT_DIR):
        raise VoicePackBuildError("la clé privée doit rester hors du dépôt Lumena")
    try:
        raw = key_path.read_bytes()
    except OSError as exc:
        raise VoicePackBuildError("clé privée de publication introuvable") from exc
    if len(raw) > 64 * 1024:
        raise VoicePackBuildError("fichier de clé privée invalide")
    try:
        loaded = serialization.load_pem_private_key(raw, password=_password(password_env))
    except Exception as exc:
        raise VoicePackBuildError("clé privée illisible ou mot de passe incorrect") from exc
    if not isinstance(loaded, Ed25519PrivateKey):
        raise VoicePackBuildError("la clé de publication doit être Ed25519")
    return loaded


def _load_descriptor(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise VoicePackBuildError("descripteur Voice Pack JSON illisible") from exc
    if not isinstance(payload, dict):
        raise VoicePackBuildError("descripteur Voice Pack invalide")
    if "signature" in payload or "files" in payload:
        raise VoicePackBuildError("signature et empreintes sont calculées par le builder")
    return payload


def _payload_paths(descriptor: Mapping[str, Any]) -> list[PurePosixPath]:
    values = descriptor.get("payload_files", [])
    if not isinstance(values, list):
        raise VoicePackBuildError("payload_files doit être une liste")
    raw_paths = list(values)
    engine = str(descriptor.get("engine", "")).lower()
    if engine == "piper":
        models = descriptor.get("models")
        if not isinstance(models, list) or not models:
            raise VoicePackBuildError("modèles Piper absents")
        for model in models:
            if not isinstance(model, dict):
                raise VoicePackBuildError("entrée modèle Piper invalide")
            raw_paths.extend((model.get("model_path"), model.get("config_path")))
    elif engine == "qwen3":
        identity = descriptor.get("qwen")
        required = {
            "model_id", "model_revision", "reference_path", "transcript_path",
            "config_path", "provenance_path",
        }
        if not isinstance(identity, Mapping) or set(identity) != required:
            raise VoicePackBuildError("identité Qwen incomplète")
        raw_paths.extend(identity.get(key) for key in (
            "reference_path", "transcript_path", "config_path", "provenance_path",
        ))
        languages = descriptor.get("languages")
        if not isinstance(languages, list) or not languages:
            raise VoicePackBuildError("langues Qwen absentes")
    else:
        raise VoicePackBuildError("moteur Voice Pack non supporté")
    result: list[PurePosixPath] = []
    seen: set[str] = set()
    for value in raw_paths:
        path = _safe_relative_path(str(value or ""))
        if str(path) not in seen:
            result.append(path)
            seen.add(str(path))
    return sorted(result, key=str)


def _validate_release_attestations(
    descriptor: Mapping[str, Any], payload_paths: Sequence[PurePosixPath],
) -> None:
    licenses = descriptor.get("licenses")
    if not isinstance(licenses, list) or not licenses:
        raise VoicePackBuildError("preuves de licence absentes")
    consent = descriptor.get("voice_consent")
    if not isinstance(consent, dict) or consent.get("confirmed") is not True:
        raise VoicePackBuildError("consentement vocal signé non confirmé")
    if not str(consent.get("rights_scope", "")).strip():
        raise VoicePackBuildError("périmètre du consentement vocal absent")
    evidence = _safe_relative_path(str(consent.get("evidence", "")))
    if evidence.suffix.lower() not in {".txt", ".md", ".json"}:
        raise VoicePackBuildError("preuve de consentement non documentaire")
    if str(evidence) not in {str(path) for path in payload_paths}:
        raise VoicePackBuildError("preuve de consentement absente du payload signé")


def _zip_write(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(name, _ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    info.create_system = 3
    archive.writestr(info, payload, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)


def _zip_write_file(archive: zipfile.ZipFile, name: str, source: Path) -> None:
    """Stream a potentially multi-gigabyte model without copying it into RAM."""
    info = zipfile.ZipInfo(name, _ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o100644 << 16
    info.create_system = 3
    with source.open("rb") as input_file, archive.open(info, "w", force_zip64=True) as output:
        for chunk in iter(lambda: input_file.read(_COPY_CHUNK_BYTES), b""):
            output.write(chunk)


def build_voice_pack(
    descriptor_path: str | Path,
    assets_dir: str | Path,
    private_key_path: str | Path,
    output_path: str | Path,
    *,
    key_id: str,
    password_env: str = "LUMENA_VOICE_PACK_SIGNING_KEY_PASSWORD",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Build, sign and product-verify one deterministic Voice Pack archive."""
    # Deferred so ``--help`` and key generation do not initialize Voice/STT or
    # require a complete application runtime.  A build still uses the exact
    # product verifier before publication.
    from src.voice.v2.voice_pack import (
        VoicePackError,
        VoicePackManager,
        canonical_manifest_bytes,
    )

    key_id = _safe_id(key_id, field="key_id")
    descriptor = _load_descriptor(Path(descriptor_path).resolve())
    assets = Path(assets_dir).resolve()
    output = Path(output_path).resolve()
    if output.exists() and not overwrite:
        raise VoicePackBuildError("archive de sortie déjà présente")
    engine = str(descriptor.get("engine", "")).lower()
    if engine not in {"piper", "qwen3"}:
        raise VoicePackBuildError("moteur Voice Pack non supporté")
    pack_id = _safe_id(str(descriptor.get("pack_id", "")), field="pack_id")
    version = _safe_id(str(descriptor.get("version", "")), field="version")
    private = _load_private_key(private_key_path, password_env=password_env)

    paths = _payload_paths(descriptor)
    _validate_release_attestations(descriptor, paths)
    file_entries: list[dict[str, Any]] = []
    resolved_files: list[tuple[PurePosixPath, Path]] = []
    for relative in paths:
        source = assets.joinpath(*relative.parts)
        if source.is_symlink() or not source.is_file() or not _inside(source, assets):
            raise VoicePackBuildError(f"payload absent ou non régulier: {relative}")
        size = source.stat().st_size
        file_entries.append({"path": str(relative), "sha256": _sha256(source), "size": size})
        resolved_files.append((relative, source))

    manifest = {key: value for key, value in descriptor.items() if key != "payload_files"}
    manifest["schema_version"] = 1
    manifest["pack_id"] = pack_id
    manifest["version"] = version
    manifest["engine"] = engine
    if engine == "piper":
        manifest["languages"] = list(dict.fromkeys(
            str(model.get("language", "")).replace("_", "-")
            for model in descriptor.get("models", [])
        ))
    else:
        manifest["languages"] = list(dict.fromkeys(
            str(language).replace("_", "-")
            for language in descriptor.get("languages", [])
        ))
    manifest["files"] = file_entries
    manifest["signature"] = {
        "algorithm": "ed25519",
        "key_id": key_id,
        "value": base64.b64encode(private.sign(canonical_manifest_bytes(manifest))).decode("ascii"),
    }
    manifest_payload = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, indent=2,
    ).encode("utf-8") + b"\n"

    output.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(handle)
    temporary = Path(temporary_name)
    public_raw = _public_raw(private)
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED,
            compresslevel=9, allowZip64=True,
        ) as archive:
            _zip_write(archive, "manifest.json", manifest_payload)
            for relative, source in resolved_files:
                _zip_write_file(archive, str(relative), source)
        with tempfile.TemporaryDirectory(prefix="lumena-voice-pack-verify-") as verify_dir:
            verified = VoicePackManager(
                verify_dir, trusted_keys={key_id: public_raw}
            ).inspect(temporary)
            from src.voice.v2.voice_pack_service import VoicePackService
            VoicePackService(
                Path(verify_dir) / "product", trusted_keys={key_id: public_raw},
            ).install(temporary, activate=False)
        os.replace(temporary, output)
    except (VoicePackError, OSError, ValueError, zipfile.BadZipFile) as exc:
        raise VoicePackBuildError(f"Voice Pack produit invalide: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)

    return {
        "schema_version": 1,
        "pack_id": pack_id,
        "version": version,
        "archive": str(output),
        "archive_bytes": output.stat().st_size,
        "archive_sha256": _sha256(output),
        "key_id": key_id,
        "public_key_base64": base64.b64encode(public_raw).decode("ascii"),
        "payload_files": len(file_entries),
        "verified_required_bytes": int(verified["required_bytes"]),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Publier un Voice Pack Lumena signé")
    sub = parser.add_subparsers(dest="command", required=True)
    key = sub.add_parser("generate-key", help="Créer une clé Ed25519 chiffrée hors dépôt")
    key.add_argument("--private-key", required=True)
    key.add_argument("--public-key-json", required=True)
    key.add_argument("--key-id", default="release")
    key.add_argument("--password-env", default="LUMENA_VOICE_PACK_SIGNING_KEY_PASSWORD")
    build = sub.add_parser("build", help="Construire et vérifier un pack")
    build.add_argument("--descriptor", required=True)
    build.add_argument("--assets-dir", required=True)
    build.add_argument("--private-key", required=True)
    build.add_argument("--output", required=True)
    build.add_argument("--key-id", default="release")
    build.add_argument("--password-env", default="LUMENA_VOICE_PACK_SIGNING_KEY_PASSWORD")
    build.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "generate-key":
            result = generate_release_key(
                args.private_key, args.public_key_json,
                key_id=args.key_id, password_env=args.password_env,
            )
        else:
            result = build_voice_pack(
                args.descriptor, args.assets_dir, args.private_key, args.output,
                key_id=args.key_id, password_env=args.password_env,
                overwrite=args.overwrite,
            )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except VoicePackBuildError as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
