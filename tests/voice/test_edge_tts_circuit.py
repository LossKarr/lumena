from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.voice.edge_circuit import EdgeCircuitBreaker, classify_edge_failure
from src.voice.tts import LumenaTTS


def test_edge_failure_classification_is_bounded():
    assert classify_edge_failure(RuntimeError("403 private upstream body")) == "forbidden"
    assert classify_edge_failure(RuntimeError("HTTP 429")) == "rate_limited"
    assert classify_edge_failure(TimeoutError("secret URL")) == "timeout"
    assert classify_edge_failure(RuntimeError("HTTP 503")) == "upstream_5xx"


def test_forbidden_opens_immediately_then_allows_one_half_open_probe():
    now = [10.0]
    circuit = EdgeCircuitBreaker(cooldown_s=30, clock=lambda: now[0])

    assert circuit.allow_request() is True
    circuit.record_failure(RuntimeError("403 Forbidden"))
    assert circuit.snapshot()["state"] == "open"
    assert circuit.allow_request() is False

    now[0] = 41.0
    assert circuit.allow_request() is True
    assert circuit.allow_request() is False
    circuit.record_success()
    assert circuit.snapshot()["state"] == "closed"


def _engine(tmp_path):
    engine = object.__new__(LumenaTTS)
    engine._tts_mode = "fast"
    engine.cache_dir = tmp_path
    engine.enable_fallback = False
    engine.metrics = MagicMock()
    engine._stop_speaking = False
    engine.is_speaking = False
    engine.voice = "fr-FR-Test"
    engine.rate = "+0%"
    engine.volume = "+0%"
    engine.pitch = "+0Hz"
    engine.xtts = None
    engine._play_audio = AsyncMock()

    piper = MagicMock()
    piper.model_name = "fr_FR-test"
    piper.is_available.return_value = True

    async def generate(_text, path, **_kwargs):
        Path(path).write_bytes(b"RIFFfake")
        return True

    piper.generate = AsyncMock(side_effect=generate)
    engine.piper = piper
    return engine


@pytest.mark.asyncio
async def test_403_attempts_edge_once_then_falls_back_locally(monkeypatch, tmp_path):
    import src.voice.tts as tts_module

    circuit = EdgeCircuitBreaker(cooldown_s=900)
    monkeypatch.setattr(tts_module, "EDGE_TTS_AVAILABLE", True)
    monkeypatch.setattr(tts_module, "get_edge_tts_circuit", lambda: circuit)
    communicate = MagicMock()
    communicate.save = AsyncMock(side_effect=RuntimeError("403 Forbidden"))
    factory = MagicMock(return_value=communicate)
    monkeypatch.setattr(
        tts_module, "edge_tts", MagicMock(Communicate=factory), raising=False
    )
    engine = _engine(tmp_path)

    first = await engine._synthesize("Première phrase.")
    second = await engine._synthesize("Deuxième phrase.")

    assert first is not None and second is not None
    assert factory.call_count == 1
    assert engine._last_provider == "piper"
    assert circuit.snapshot()["reason"] == "forbidden"


@pytest.mark.asyncio
async def test_sentence_path_does_not_retry_edge_after_its_403(monkeypatch, tmp_path):
    import src.voice.tts as tts_module

    circuit = EdgeCircuitBreaker(cooldown_s=900)
    monkeypatch.setattr(tts_module, "EDGE_TTS_AVAILABLE", True)
    monkeypatch.setattr(tts_module, "get_edge_tts_circuit", lambda: circuit)
    communicate = MagicMock()
    communicate.save = AsyncMock(side_effect=RuntimeError("403 Forbidden"))
    factory = MagicMock(return_value=communicate)
    monkeypatch.setattr(
        tts_module, "edge_tts", MagicMock(Communicate=factory), raising=False
    )
    engine = _engine(tmp_path)

    await engine.speak(
        "Cette première phrase est assez longue. Cette deuxième phrase aussi.",
        wait=True,
    )

    assert factory.call_count == 1
    assert engine._last_provider == "piper"
    engine._play_audio.assert_awaited_once()


@pytest.mark.asyncio
async def test_live_edge_probe_bypasses_a_valid_cached_mp3(monkeypatch, tmp_path):
    import src.voice.tts as tts_module

    circuit = EdgeCircuitBreaker(cooldown_s=900)
    monkeypatch.setattr(tts_module, "EDGE_TTS_AVAILABLE", True)
    monkeypatch.setattr(tts_module, "get_edge_tts_circuit", lambda: circuit)

    async def save_valid_mp3(path):
        Path(path).write_bytes(b"\xff\xfb" + (b"\x00" * 510))

    communicate = MagicMock()
    communicate.save = AsyncMock(side_effect=save_valid_mp3)
    factory = MagicMock(return_value=communicate)
    monkeypatch.setattr(
        tts_module, "edge_tts", MagicMock(Communicate=factory), raising=False,
    )
    engine = _engine(tmp_path)

    assert await engine._synthesize("Bonjour.") is not None
    assert await engine._synthesize("Bonjour.") is not None
    assert factory.call_count == 1

    assert await engine._synthesize("Bonjour.", bypass_cache=True) is not None
    assert factory.call_count == 2
