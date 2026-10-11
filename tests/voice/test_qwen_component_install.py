from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from src.runtime.component_service import ComponentServiceError
from src.voice.v2 import component_install
from src.voice.v2.providers.base import AudioResult


def test_voice_component_activation_fails_closed_without_signed_component(tmp_path):
    with pytest.raises(ComponentServiceError, match="not active"):
        component_install.activate_installed_voice_pack(root=tmp_path)


def test_real_synthesis_result_controls_automatic_qualification(monkeypatch, tmp_path):
    output = tmp_path / "canary.wav"
    recorded = {}

    class FakeProvider:
        async def synthesize(self, _text, _profile):
            output.write_bytes(b"RIFF")
            return AudioResult(
                ok=True, text="canary", audio_path=str(output),
                duration_ms=1000, provider="qwen3-tts",
            )

        async def aclose(self):
            return None

    class FakeService:
        def apply_to_profile(self, profile):
            return profile

    monkeypatch.setattr(
        "src.voice.v2.qwen_runtime.probe_qwen_runtime",
        lambda **_kwargs: {"ready": True},
    )
    monkeypatch.setattr(
        "src.voice.v2.qwen_runtime.record_qwen_performance",
        lambda **kwargs: recorded.update(kwargs),
    )
    monkeypatch.setattr(
        "src.voice.v2.providers.qwen3_tts.Qwen3TTSProvider", FakeProvider,
    )
    monkeypatch.setattr(
        "src.voice.v2.voice_pack_service.get_voice_pack_service",
        lambda: FakeService(),
    )
    monkeypatch.setattr(
        "src.voice.v2.voice_profile.load_profile", lambda: object(),
    )
    times = iter((10.0, 10.5))
    monkeypatch.setattr(component_install.time, "perf_counter", lambda: next(times))
    monkeypatch.setenv("LUMENA_QWEN_AUTO_MAX_FIRST_AUDIO_MS", "600")
    monkeypatch.setenv("LUMENA_QWEN_AUTO_MAX_RTF", "0.6")

    result = asyncio.run(component_install._qwen_canary_async())
    assert result == {
        "ok": True, "qualified": True, "first_audio_ms": 500,
        "rtf": 0.5, "provider": "qwen3-tts",
    }
    assert recorded["qualified"] is True
    assert not output.exists()
