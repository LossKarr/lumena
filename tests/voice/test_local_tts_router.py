from __future__ import annotations

from pathlib import Path

import pytest

from src.voice.v2.providers.base import AudioResult, CancelToken, TTSProvider
from src.voice.v2.providers.local_router import LocalTTSRouter
from src.voice.v2.voice_profile import VoiceProfile


class Provider(TTSProvider):
    def __init__(self, name: str, *, ok: bool = True, available: bool = True):
        self.name = name
        self.ok = ok
        self.available = available
        self.calls = []

    def is_available(self):
        return self.available

    async def synthesize(self, text, voice, cancel=None):
        self.calls.append(text)
        return AudioResult(
            ok=self.ok, text=text, audio_path=f"{self.name}.wav" if self.ok else None,
            provider=self.name, audio_format="pcm16", sample_rate=24000,
            channels=1, duration_ms=100,
        )


def _voice(tmp_path: Path):
    reference = tmp_path / "voice.wav"
    reference.write_bytes(b"fixture")
    voice = VoiceProfile(reference_consent_confirmed=True)
    voice.local.qwen_reference = str(reference)
    voice.local.qwen_reference_text = "Bonjour."
    return voice


@pytest.mark.asyncio
async def test_auto_uses_qwen_only_after_local_performance_qualification(tmp_path):
    qwen, fallback = Provider("qwen3-tts"), Provider("piper")
    unqualified = LocalTTSRouter(
        qwen=qwen, fallback=fallback, engine="auto", qwen_qualified=False,
    )
    assert (await unqualified.synthesize("Bonjour", _voice(tmp_path))).provider == "piper"
    assert qwen.calls == []

    qualified = LocalTTSRouter(
        qwen=qwen, fallback=fallback, engine="auto", qwen_qualified=True,
    )
    assert (await qualified.synthesize("Bonjour", _voice(tmp_path))).provider == "qwen3-tts"


@pytest.mark.asyncio
async def test_forced_qwen_falls_back_on_failure_and_records_truth(tmp_path):
    router = LocalTTSRouter(
        qwen=Provider("qwen3-tts", ok=False), fallback=Provider("piper"),
        engine="qwen3", qwen_qualified=False,
    )
    result = await router.synthesize("Bonjour", _voice(tmp_path))
    assert result.provider == "piper"
    status = router.runtime_status()
    assert status["tts_fallback_count"] == 1
    assert status["tts_fallback_reason"] == "qwen_synthesis_failed"


@pytest.mark.asyncio
async def test_stream_falls_back_per_segment_without_losing_text(tmp_path):
    qwen = Provider("qwen3-tts", ok=False)
    fallback = Provider("piper")
    router = LocalTTSRouter(
        qwen=qwen, fallback=fallback, engine="qwen3", qwen_qualified=True,
    )
    text = "Première phrase. Deuxième phrase."
    chunks = [item async for item in router.stream(text, _voice(tmp_path))]
    assert chunks
    assert " ".join(item.text for item in chunks) == text
    assert all(item.provider == "piper" for item in chunks)
    assert qwen.calls and fallback.calls


@pytest.mark.asyncio
async def test_cancelled_qwen_failure_never_speaks_fallback(tmp_path):
    token = CancelToken()
    token.cancel()
    qwen, fallback = Provider("qwen3-tts", ok=False), Provider("piper")
    router = LocalTTSRouter(
        qwen=qwen, fallback=fallback, engine="qwen3", qwen_qualified=True,
    )
    await router.synthesize("Bonjour", _voice(tmp_path), token)
    assert fallback.calls == []
