"""Locally generated, auditable Qwen voice identity derived from bundled Piper."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import os
from pathlib import Path
import time
from typing import Any, Callable, Mapping
import wave

from src.runtime.component_installer import sha256_file
from src.utils.persistence import atomic_write_json, safe_read_json

from .voice_profile import VoiceProfile


MODEL_ID = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
MODEL_REVISION = "5d83992436eae1d760afd27aff78a71d676296fc"
REFERENCE_TEXT = (
    "Bonjour, je suis Lumena. Je vous accompagne avec clarté, calme et précision."
)
IDENTITY_SCHEMA = 1


class QwenIdentityError(RuntimeError):
    pass


def _identity_dir(voice_dir: Path) -> Path:
    return Path(voice_dir).resolve() / "qwen-identity"


def load_local_qwen_identity(voice_dir: Path) -> dict[str, Any] | None:
    """Return the identity only when its fixed local payload still matches its proof."""
    target = _identity_dir(voice_dir)
    state = safe_read_json(target / "identity.json", default={})
    if not isinstance(state, Mapping) or state.get("schema_version") != IDENTITY_SCHEMA:
        return None
    if state.get("source") != "bundled_piper_siwis_medium" \
            or state.get("model_id") != MODEL_ID \
            or state.get("model_revision") != MODEL_REVISION \
            or state.get("reference_text") != REFERENCE_TEXT:
        return None
    reference = target / "reference.wav"
    expected = str(state.get("reference_sha256", "")).lower()
    if len(expected) != 64 or not reference.is_file() or sha256_file(reference) != expected:
        return None
    try:
        with wave.open(str(reference), "rb") as audio:
            duration = audio.getnframes() / max(1, audio.getframerate())
            valid_audio = (
                audio.getnchannels() == 1 and audio.getsampwidth() == 2
                and 1.0 <= duration <= 20.0
            )
    except (OSError, wave.Error):
        return None
    if not valid_audio:
        return None
    return {
        "reference_path": str(reference), "reference_text": REFERENCE_TEXT,
        "model_id": MODEL_ID, "model_revision": MODEL_REVISION,
        "version": str(state.get("identity_version", "1.0.0")),
        "rights_note": str(state.get("rights_note", "")),
    }


def local_qwen_identity_ready(voice_dir: Path) -> bool:
    return load_local_qwen_identity(voice_dir) is not None


def _synthesize_with_bundled_piper(app_root: Path, text: str, output: Path) -> bool:
    from src.voice.providers.piper_provider import PiperProvider

    provider = PiperProvider(data_dir=Path(app_root), model_name="fr_FR-siwis-medium")
    if not provider.is_available("fr_FR-siwis-medium"):
        raise QwenIdentityError("bundled_piper_unavailable")
    return bool(asyncio.run(provider.generate(
        text, output, model_name="fr_FR-siwis-medium", length_scale=0.96,
    )))


def ensure_local_qwen_identity(
    *, app_root: Path, voice_dir: Path,
    synthesizer: Callable[[Path, str, Path], bool] = _synthesize_with_bundled_piper,
) -> dict[str, Any]:
    """Create the reference locally; no recorded voice or generated audio is shipped."""
    current = load_local_qwen_identity(voice_dir)
    if current is not None:
        return current
    target = _identity_dir(voice_dir)
    target.mkdir(parents=True, exist_ok=True)
    temporary = target / "reference.tmp.wav"
    reference = target / "reference.wav"
    temporary.unlink(missing_ok=True)
    try:
        if not synthesizer(Path(app_root).resolve(), REFERENCE_TEXT, temporary):
            raise QwenIdentityError("piper_reference_generation_failed")
        try:
            with wave.open(str(temporary), "rb") as audio:
                duration = audio.getnframes() / max(1, audio.getframerate())
                if audio.getnchannels() != 1 or audio.getsampwidth() != 2 \
                        or not 1.0 <= duration <= 20.0:
                    raise QwenIdentityError("piper_reference_audio_invalid")
        except (OSError, wave.Error) as exc:
            raise QwenIdentityError("piper_reference_audio_invalid") from exc
        os.replace(temporary, reference)
        payload = {
            "schema_version": IDENTITY_SCHEMA,
            "identity_version": "1.0.0",
            "source": "bundled_piper_siwis_medium",
            "source_model": "fr_FR-siwis-medium",
            "source_license": "Piper voice weights MIT; SIWIS corpus CC-BY 4.0",
            "creation_method": "local deterministic TTS synthesis",
            "model_id": MODEL_ID,
            "model_revision": MODEL_REVISION,
            "reference_text": REFERENCE_TEXT,
            "reference_sha256": sha256_file(reference),
            "rights_note": (
                "Référence synthétique générée localement depuis le modèle Piper SIWIS "
                "livré avec sa provenance et sa licence."
            ),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        atomic_write_json(target / "identity.json", payload)
    finally:
        temporary.unlink(missing_ok=True)
    result = load_local_qwen_identity(voice_dir)
    if result is None:
        raise QwenIdentityError("local_identity_verification_failed")
    return result


def apply_local_qwen_identity(profile: VoiceProfile, voice_dir: Path) -> VoiceProfile:
    identity = load_local_qwen_identity(voice_dir)
    if identity is None:
        return profile
    projected = deepcopy(profile)
    projected.local.qwen_reference = identity["reference_path"]
    projected.local.qwen_reference_text = identity["reference_text"]
    projected.local.qwen_model_id = identity["model_id"]
    projected.local.qwen_model_revision = identity["model_revision"]
    projected.local.qwen_voice_version = identity["version"]
    projected.reference_consent_confirmed = True
    projected.reference_rights_note = identity["rights_note"]
    return projected
