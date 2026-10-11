from __future__ import annotations

from pathlib import Path
import wave

from src.voice.v2.qwen_identity import (
    apply_local_qwen_identity,
    ensure_local_qwen_identity,
    load_local_qwen_identity,
)
from src.voice.v2.voice_profile import VoiceProfile


def _synthesizer(_root: Path, _text: str, output: Path) -> bool:
    with wave.open(str(output), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(24000)
        audio.writeframes(b"\0\1" * 72000)
    return True


def test_local_identity_is_generated_verified_and_projected_without_recorded_voice(tmp_path):
    voice_dir = tmp_path / "data" / "voice"
    identity = ensure_local_qwen_identity(
        app_root=tmp_path, voice_dir=voice_dir, synthesizer=_synthesizer,
    )
    assert Path(identity["reference_path"]).is_file()
    assert "Piper SIWIS" in identity["rights_note"]
    profile = apply_local_qwen_identity(VoiceProfile(), voice_dir)
    assert profile.reference_consent_confirmed is True
    assert profile.local.qwen_reference == identity["reference_path"]
    assert profile.local.qwen_reference_text.startswith("Bonjour, je suis Lumena")

    # The reference is fail-closed: a modified WAV is no longer trusted.
    Path(identity["reference_path"]).write_bytes(b"tampered")
    assert load_local_qwen_identity(voice_dir) is None
