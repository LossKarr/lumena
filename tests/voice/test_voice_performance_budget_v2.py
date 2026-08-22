import asyncio
import statistics
import time

import pytest

from src.voice.v2 import (
    FakeTTSProvider, LocalAudioPlayer, TurnManager, VoiceCommand, VoiceEvent,
    VoiceRuntime,
)
from src.voice.v2.providers.real_vad import RealVADProvider


def _noop():
    async def _():
        return None
    return _()


def test_barge_in_reducer_p95_is_far_below_150ms():
    samples = []
    for _ in range(200):
        tm = TurnManager(barge_in_on_vad=True)
        tm.state.set_mode("speaking")
        start = time.perf_counter()
        commands = tm.feed(VoiceEvent("vad.speech_started"))
        samples.append((time.perf_counter() - start) * 1000.0)
        assert any(c.name == "stop_playback" for c in commands)
    p95 = statistics.quantiles(samples, n=20)[18]
    assert p95 < 150.0


@pytest.mark.asyncio
async def test_cancelled_llm_stress_leaves_no_runtime_tasks():
    gate = asyncio.Event()

    async def slow(_):
        await gate.wait()
        return "late"

    rt = VoiceRuntime(
        TurnManager(), FakeTTSProvider(), LocalAudioPlayer(play_fn=lambda x: _noop()),
        respond_fn=slow, enabled=True,
    )
    for i in range(50):
        gen = f"g_{i}"
        await rt._handle(VoiceCommand("start_llm", {
            "generation_id": gen, "turn_id": f"u_{i}", "text": "x",
        }))
        await asyncio.sleep(0)
        await rt._handle(VoiceCommand("cancel_llm", {"generation_id": gen}))
    await asyncio.sleep(0)
    await rt.aclose()
    assert rt._llm_tasks == {}


def test_voice_off_runtime_does_not_resolve_hardware():
    class _Never:
        def __getattr__(self, name):
            raise AssertionError(f"hardware resolved while OFF: {name}")

    rt = VoiceRuntime(
        TurnManager(), _Never(),
        LocalAudioPlayer(play_fn=lambda x: _noop(), stop_fn=lambda: None),
        enabled=False,
    )
    assert rt.enabled is False


def test_input_device_selection_is_stored_without_opening_micro():
    vad = RealVADProvider(frames=[], rms_fn=lambda _: 0, input_device_index=7)
    assert vad.input_device_index == 7
    assert vad.is_available() is True
