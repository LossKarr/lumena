"""Product integration for signed local Voice Packs.

The low-level :mod:`voice_pack` verifier owns archive safety and signatures.
This service adds the product contract: trusted-key loading, Piper asset
validation, profile routing and bounded status for the authenticated UI.
"""
from __future__ import annotations

import base64
from copy import deepcopy
import json
import os
from pathlib import Path, PurePosixPath
import re
import threading
from typing import Any, Dict, Mapping, Optional

from src.utils.paths import DATA_DIR, ROOT_DIR

from .voice_pack import VoicePackError, VoicePackManager
from .voice_profile import VoiceProfile


_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,95}$")
_LANGUAGE = re.compile(r"^[a-z]{2}(?:[-_][A-Za-z]{2})?$")
_MAX_PUBLIC_KEYS = 32


def _safe_identifier(value: str, *, field: str) -> str:
    identifier = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", identifier):
        raise VoicePackError(f"champ {field} invalide")
    return identifier


def _decode_trusted_keys(payload: Any) -> Dict[str, str]:
    if isinstance(payload, dict) and isinstance(payload.get("keys"), dict):
        payload = payload["keys"]
    if not isinstance(payload, dict) or len(payload) > _MAX_PUBLIC_KEYS:
        return {}
    result: Dict[str, str] = {}
    for key_id, encoded in payload.items():
        key_id = str(key_id or "").strip()
        encoded = str(encoded or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,95}", key_id):
            continue
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception:
            continue
        if len(raw) == 32:
            result[key_id] = encoded
    return result


def load_trusted_voice_pack_keys(
    *, root_dir: Path = ROOT_DIR, data_dir: Path = DATA_DIR,
) -> Dict[str, str]:
    """Load public Ed25519 keys; invalid entries are ignored, never guessed."""
    merged: Dict[str, str] = {}
    sources = [
        root_dir / "config" / "voice_pack_trusted_keys.json",
        data_dir / "voice" / "trusted_keys.json",
    ]
    for path in sources:
        try:
            merged.update(_decode_trusted_keys(json.loads(path.read_text(encoding="utf-8"))))
        except Exception:
            continue
    raw_env = os.getenv("LUMENA_VOICE_PACK_TRUSTED_KEYS_JSON", "").strip()
    if raw_env:
        try:
            merged.update(_decode_trusted_keys(json.loads(raw_env)))
        except Exception:
            pass
    return dict(list(merged.items())[:_MAX_PUBLIC_KEYS])


def _safe_pack_path(value: str) -> PurePosixPath:
    path = PurePosixPath(str(value or ""))
    if not value or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise VoicePackError("chemin de modèle vocal invalide")
    return path


def _validated_piper_models(manifest: Mapping[str, Any], pack_dir: Path) -> list[Dict[str, str]]:
    if str(manifest.get("engine", "")).lower() != "piper":
        raise VoicePackError("moteur Voice Pack non supporté")
    models = manifest.get("models")
    if not isinstance(models, list) or not models:
        raise VoicePackError("modèles Piper absents du manifeste")
    declared = {str(item.get("path", "")) for item in manifest.get("files", []) if isinstance(item, dict)}
    validated: list[Dict[str, str]] = []
    seen_languages: set[str] = set()
    for model in models:
        if not isinstance(model, dict):
            raise VoicePackError("entrée modèle Piper invalide")
        language = str(model.get("language", "")).strip().replace("_", "-")
        model_name = str(model.get("model_name", "")).strip()
        model_path = _safe_pack_path(str(model.get("model_path", "")))
        config_path = _safe_pack_path(str(model.get("config_path", "")))
        if not _LANGUAGE.fullmatch(language) or not _MODEL_NAME.fullmatch(model_name):
            raise VoicePackError("identité du modèle Piper invalide")
        language_key = language.lower().split("-")[0]
        if language_key in seen_languages:
            raise VoicePackError("langue Piper dupliquée dans le pack")
        seen_languages.add(language_key)
        if model_path.suffix.lower() != ".onnx" or config_path.suffix.lower() != ".json":
            raise VoicePackError("fichiers Piper invalides")
        if str(model_path) not in declared or str(config_path) not in declared:
            raise VoicePackError("fichiers Piper non déclarés")
        resolved_model = pack_dir.joinpath(*model_path.parts)
        resolved_config = pack_dir.joinpath(*config_path.parts)
        if not resolved_model.is_file() or not resolved_config.is_file():
            raise VoicePackError("fichiers Piper installés absents")
        try:
            config = json.loads(resolved_config.read_text(encoding="utf-8"))
        except Exception as exc:
            raise VoicePackError("configuration Piper invalide") from exc
        if not isinstance(config, dict) or not config:
            raise VoicePackError("configuration Piper invalide")
        validated.append({
            "language": language_key,
            "model_name": model_name,
            "model_path": str(resolved_model),
            "config_path": str(resolved_config),
        })
    return validated


def _validated_qwen_identity(manifest: Mapping[str, Any], pack_dir: Path) -> Dict[str, Any]:
    if str(manifest.get("engine", "")).lower() != "qwen3":
        raise VoicePackError("moteur Voice Pack non supporté")
    identity = manifest.get("qwen")
    if not isinstance(identity, Mapping):
        raise VoicePackError("identité Qwen absente du manifeste")
    required = {
        "model_id", "model_revision", "reference_path", "transcript_path",
        "config_path", "provenance_path",
    }
    if set(identity) != required:
        raise VoicePackError("contrat identité Qwen incomplet")
    model_id = str(identity.get("model_id", ""))
    revision = str(identity.get("model_revision", "")).lower()
    if model_id != "Qwen/Qwen3-TTS-12Hz-0.6B-Base" \
            or not re.fullmatch(r"[0-9a-f]{40,64}", revision):
        raise VoicePackError("modèle Qwen non épinglé")
    paths = {
        "reference_path": _safe_pack_path(str(identity.get("reference_path", ""))),
        "transcript_path": _safe_pack_path(str(identity.get("transcript_path", ""))),
        "config_path": _safe_pack_path(str(identity.get("config_path", ""))),
        "provenance_path": _safe_pack_path(str(identity.get("provenance_path", ""))),
    }
    expected_suffixes = {
        "reference_path": ".wav", "transcript_path": ".txt",
        "config_path": ".json", "provenance_path": ".json",
    }
    declared = {
        str(item.get("path", "")) for item in manifest.get("files", [])
        if isinstance(item, Mapping)
    }
    resolved: Dict[str, Path] = {}
    for key, rel in paths.items():
        if rel.suffix.lower() != expected_suffixes[key] or str(rel) not in declared:
            raise VoicePackError("fichier identité Qwen invalide ou non déclaré")
        path = pack_dir.joinpath(*rel.parts)
        if not path.is_file():
            raise VoicePackError("fichier identité Qwen installé absent")
        resolved[key] = path
    try:
        transcript = resolved["transcript_path"].read_text(encoding="utf-8").strip()
        config = json.loads(resolved["config_path"].read_text(encoding="utf-8"))
        provenance = json.loads(resolved["provenance_path"].read_text(encoding="utf-8"))
    except Exception as exc:
        raise VoicePackError("métadonnées identité Qwen invalides") from exc
    if not transcript or len(transcript) > 10_000:
        raise VoicePackError("transcription identité Qwen invalide")
    if not isinstance(config, Mapping) or config.get("schema_version") != 1:
        raise VoicePackError("configuration identité Qwen invalide")
    if not isinstance(provenance, Mapping) \
            or provenance.get("distribution_rights_confirmed") is not True \
            or not str(provenance.get("creation_method", "")).strip():
        raise VoicePackError("provenance identité Qwen insuffisante")
    languages = manifest.get("languages", [])
    if not isinstance(languages, list) or not languages:
        raise VoicePackError("langues identité Qwen absentes")
    return {
        "model_id": model_id, "model_revision": revision,
        "reference_path": str(resolved["reference_path"]),
        "transcript_path": str(resolved["transcript_path"]),
        "reference_text": transcript,
        "config_path": str(resolved["config_path"]),
        "provenance_path": str(resolved["provenance_path"]),
        "languages": [str(item).lower().split("-")[0] for item in languages],
        "config": dict(config),
    }


def _validated_product_pack(manifest: Mapping[str, Any], pack_dir: Path) -> Dict[str, Any]:
    engine = str(manifest.get("engine", "")).lower()
    if engine == "piper":
        models = _validated_piper_models(manifest, pack_dir)
        return {"engine": "piper", "models": models, "languages": [item["language"] for item in models]}
    if engine == "qwen3":
        identity = _validated_qwen_identity(manifest, pack_dir)
        return {"engine": "qwen3", "qwen": identity, "models": [], "languages": identity["languages"]}
    raise VoicePackError("moteur Voice Pack non supporté")


class VoicePackService:
    def __init__(
        self,
        root: str | Path | None = None,
        *,
        trusted_keys: Optional[Mapping[str, bytes | str]] = None,
        root_dir: Path = ROOT_DIR,
        data_dir: Path = DATA_DIR,
    ) -> None:
        self.root = Path(root) if root is not None else data_dir / "voice"
        self.trusted_keys = dict(
            trusted_keys if trusted_keys is not None else load_trusted_voice_pack_keys(
                root_dir=root_dir, data_dir=data_dir,
            )
        )
        self.manager = VoicePackManager(self.root, trusted_keys=self.trusted_keys)
        self._active_cache_key: Optional[tuple[Any, ...]] = None
        self._active_cache: Optional[Dict[str, Any]] = None

    def _invalidate_active_cache(self) -> None:
        self._active_cache_key = None
        self._active_cache = None

    def _active_fingerprint(self) -> Optional[tuple[Any, ...]]:
        current = self.manager._active_payload()
        try:
            pack_id = _safe_identifier(current.get("pack_id", ""), field="pack_id")
            version = _safe_identifier(current.get("version", ""), field="version")
            target = self.manager.packs_dir / pack_id / version
            manifest_path = target / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            stats: list[tuple[str, int, int]] = []
            for item in manifest.get("files", []):
                rel = _safe_pack_path(str(item.get("path", "")))
                path = target.joinpath(*rel.parts)
                stat = path.stat()
                stats.append((str(rel), int(stat.st_size), int(stat.st_mtime_ns)))
            active_stat = self.manager.active_file.stat()
            manifest_stat = manifest_path.stat()
            return (
                pack_id, version,
                int(active_stat.st_size), int(active_stat.st_mtime_ns),
                int(manifest_stat.st_size), int(manifest_stat.st_mtime_ns),
                *stats,
            )
        except Exception:
            return None

    def status(self) -> Dict[str, Any]:
        installed = self.manager.list_installed()
        active_pack = self.active_pack()
        active = None
        if active_pack is not None:
            manifest = active_pack["manifest"]
            active = next((
                entry for entry in installed
                if entry.get("pack_id") == manifest.get("pack_id")
                and entry.get("version") == manifest.get("version")
            ), None)
        for entry in installed:
            entry["active"] = bool(active is entry)
        return {
            "schema_version": 1,
            "trusted_signers": len(self.trusted_keys),
            "can_import": bool(self.trusted_keys),
            "active": active,
            "installed": installed,
        }

    def install(self, archive_path: str | Path, *, activate: bool = False) -> Dict[str, Any]:
        if not self.trusted_keys:
            raise VoicePackError("aucune clé de signature Voice Pack approuvée")
        target = self.manager.install(archive_path, activate=False)
        try:
            manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
            product = _validated_product_pack(manifest, target)
        except Exception:
            # Never leave an unusable product pack installed after the archive
            # verifier succeeds but the provider contract fails.
            try:
                self.manager.uninstall(target.parent.name, target.name)
            except Exception:
                pass
            raise
        if activate:
            self.manager.activate(str(manifest["pack_id"]), str(manifest["version"]))
        self._invalidate_active_cache()
        result = {
            "pack_id": str(manifest["pack_id"]),
            "version": str(manifest["version"]),
            "languages": list(product["languages"]),
            "active": bool(activate),
        }
        if product["engine"] != "piper":
            result["engine"] = str(product["engine"])
        return result

    def activate(self, pack_id: str, version: str) -> Dict[str, Any]:
        pack_id = _safe_identifier(pack_id, field="pack_id")
        version = _safe_identifier(version, field="version")
        target = self.manager.packs_dir / pack_id / version
        try:
            manifest = self.manager.inspect_installed(pack_id, version)
        except Exception as exc:
            raise VoicePackError("Voice Pack non installé ou corrompu") from exc
        product = _validated_product_pack(manifest, target)
        self.manager.activate(pack_id, version)
        self._invalidate_active_cache()
        result = {
            "pack_id": pack_id,
            "version": version,
            "languages": list(product["languages"]),
            "active": True,
        }
        if product["engine"] != "piper":
            result["engine"] = str(product["engine"])
        return result

    def rollback(self) -> Dict[str, Any]:
        self.manager.rollback()
        self._invalidate_active_cache()
        active = self.active_pack()
        if active is None:
            raise VoicePackError("rollback Voice Pack invalide")
        return {
            "pack_id": str(active["manifest"]["pack_id"]),
            "version": str(active["manifest"]["version"]),
            "active": True,
        }

    def restore_selection(self, previous: Optional[Mapping[str, Any]]) -> None:
        """Restore a pre-mutation active selection after a runtime failure."""
        if previous and previous.get("pack_id") and previous.get("version"):
            self.manager.activate(
                str(previous["pack_id"]), str(previous["version"])
            )
        else:
            self.manager.clear_active()
        self._invalidate_active_cache()

    def uninstall(self, pack_id: str, version: str) -> None:
        self.manager.uninstall(pack_id, version)
        self._invalidate_active_cache()

    def active_pack(self) -> Optional[Dict[str, Any]]:
        fingerprint = self._active_fingerprint()
        if fingerprint is None:
            self._active_cache_key = None
            self._active_cache = None
            return None
        if fingerprint == self._active_cache_key:
            return self._active_cache
        manifest = self.manager.active()
        if manifest is None:
            self._active_cache_key = None
            self._active_cache = None
            return None
        target = self.manager.packs_dir / str(manifest["pack_id"]) / str(manifest["version"])
        try:
            product = _validated_product_pack(manifest, target)
        except VoicePackError:
            return None
        result = {"manifest": manifest, "path": target, **product}
        self._active_cache_key = fingerprint
        self._active_cache = result
        return result

    def apply_to_profile(self, profile: VoiceProfile) -> VoiceProfile:
        active = self.active_pack()
        if active is None:
            return profile
        projected = deepcopy(profile)
        if active["engine"] == "piper":
            for model in active["models"]:
                projected.local.piper_models[model["language"]] = model["model_name"]
                if model["language"] == projected.language.split("-")[0].lower():
                    projected.local.piper_model = model["model_name"]
        elif active["engine"] == "qwen3":
            identity = active["qwen"]
            projected.local.qwen_reference = identity["reference_path"]
            projected.local.qwen_reference_text = identity["reference_text"]
            projected.local.qwen_model_id = identity["model_id"]
            projected.local.qwen_model_revision = identity["model_revision"]
            projected.local.qwen_voice_version = str(active["manifest"].get("version", ""))
            # The signed pack was accepted only after its provenance explicitly
            # confirmed distribution rights. Project that proof into the runtime
            # profile so Qwen is usable without a second, contradictory consent.
            projected.reference_consent_confirmed = True
            projected.reference_rights_note = (
                "Voice Pack Lumena signé ; provenance et droits de distribution vérifiés"
            )
        return projected

    def resolve_piper_model(self, model_name: str) -> Optional[tuple[Path, Path]]:
        active = self.active_pack()
        if active is None:
            return None
        for model in active["models"]:
            if model["model_name"] == model_name:
                return Path(model["model_path"]), Path(model["config_path"])
        return None

    def resolve_qwen_identity(self) -> Optional[Dict[str, Any]]:
        active = self.active_pack()
        if active is None or active.get("engine") != "qwen3":
            return None
        return dict(active["qwen"])


_PRODUCT_SERVICE: Optional[VoicePackService] = None
_PRODUCT_SERVICE_KEY: Optional[tuple[Any, ...]] = None
_PRODUCT_SERVICE_LOCK = threading.Lock()


def _product_service_key() -> tuple[Any, ...]:
    values: list[Any] = [str(DATA_DIR), os.getenv("LUMENA_VOICE_PACK_TRUSTED_KEYS_JSON", "")]
    for path in (
        ROOT_DIR / "config" / "voice_pack_trusted_keys.json",
        DATA_DIR / "voice" / "trusted_keys.json",
    ):
        try:
            stat = path.stat()
            values.extend((str(path), int(stat.st_size), int(stat.st_mtime_ns)))
        except OSError:
            values.extend((str(path), None, None))
    return tuple(values)


def get_voice_pack_service() -> VoicePackService:
    """Return a process-local service, refreshed when its public trust changes."""
    global _PRODUCT_SERVICE, _PRODUCT_SERVICE_KEY
    key = _product_service_key()
    if _PRODUCT_SERVICE is None or key != _PRODUCT_SERVICE_KEY:
        with _PRODUCT_SERVICE_LOCK:
            if _PRODUCT_SERVICE is None or key != _PRODUCT_SERVICE_KEY:
                _PRODUCT_SERVICE = VoicePackService()
                _PRODUCT_SERVICE_KEY = key
    return _PRODUCT_SERVICE

