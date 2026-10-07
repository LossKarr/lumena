import asyncio

import pytest

from src.voice.v2 import RealVADProvider, TurnManager, VoiceEvent, decide_endpoint
from src.voice.v2.providers.silero_vad import SileroSpeechProbability


def _energy(frame: bytes) -> float:
    return {b"q": 0.0, b"n": 100.0, b"L": 1200.0}.get(frame, 0.0)


@pytest.mark.asyncio
async def test_neural_vad_detects_quiet_speech_without_energy_gate():
    frames = [b"n", b"n", b"n", b"q", b"q"]
    probabilities = iter([0.9, 0.9, 0.9, 0.01, 0.01])
    vad = RealVADProvider(
        energy_threshold=300, frame_ms=10, min_speech_ms=10,
        silence_hangover_ms=20, frames=frames, rms_fn=_energy,
        speech_probability_fn=lambda _frame: next(probabilities),
    )
    assert [event.kind async for event in vad.stream()] == [
        "speech_started", "speech_ended"
    ]
    assert vad.status()["engine"] == "silero_onnx"


@pytest.mark.asyncio
async def test_neural_vad_rejects_loud_non_speech_music_like_frames():
    vad = RealVADProvider(
        energy_threshold=300, frame_ms=10, min_speech_ms=10,
        silence_hangover_ms=20, frames=[b"L"] * 6, rms_fn=_energy,
        speech_probability_fn=lambda _frame: 0.08,
    )
    assert [event async for event in vad.stream()] == []


@pytest.mark.asyncio
async def test_neural_failure_falls_back_to_energy_for_current_and_future_frames():
    calls = 0

    def broken(_frame):
        nonlocal calls
        calls += 1
        raise RuntimeError("inference failed")

    vad = RealVADProvider(
        energy_threshold=300, frame_ms=10, min_speech_ms=10,
        silence_hangover_ms=20, frames=[b"L", b"L", b"q", b"q"],
        rms_fn=_energy, speech_probability_fn=broken,
    )
    assert [event.kind async for event in vad.stream()] == [
        "speech_started", "speech_ended"
    ]
    assert calls == 1
    assert vad.status()["engine"] == "energy"
    assert vad.status()["fallback_reason"] == "RuntimeError"


@pytest.mark.asyncio
async def test_neural_self_voice_guard_still_requires_barge_in_energy():
    vad = RealVADProvider(
        energy_threshold=300, speaking_threshold=800, frame_ms=10,
        min_speech_ms=10, silence_hangover_ms=20,
        frames=[b"n"] * 4, rms_fn=_energy,
        speech_probability_fn=lambda _frame: 0.99,
        is_speaking_fn=lambda: True,
    )
    assert [event async for event in vad.stream()] == []


def test_silero_probe_is_local_and_reports_missing_components(monkeypatch):
    queried = []

    def find_spec(name):
        queried.append(name)
        return None

    monkeypatch.setattr("importlib.util.find_spec", find_spec)
    status = SileroSpeechProbability.probe()
    assert status.available is False
    assert status.reason == "silero_vad_not_installed"
    assert queried == ["silero_vad"]


@pytest.mark.parametrize("text", [
    "euh", "je veux dire...", "non, plutôt", "because", "quiero decir…",
    "je voudrais", "wait",
])
def test_endpointing_waits_for_hesitations_and_corrections(text):
    decision = decide_endpoint(text, is_final=True, pause_ms=700)
    assert decision.state == "continue_expected"
    assert decision.reason == "suspended_or_continuation"


@pytest.mark.parametrize("text", [
    "Tu peux ouvrir le fichier ?", "How do I open this?", "¿Cómo abro esto?",
])
def test_endpointing_recognizes_complete_multilingual_questions(text):
    decision = decide_endpoint(text, is_final=True, pause_ms=200)
    assert decision.state == "turn_complete"


def test_endpointing_adapts_explicitly_to_observed_pause():
    tm = TurnManager()
    tm.feed(VoiceEvent("vad.speech_started", t=0))
    tm.feed(VoiceEvent("stt.partial", t=50, data={"text": "je réfléchis"}))
    tm.feed(VoiceEvent("vad.speech_ended", t=100))
    tm.feed(VoiceEvent("vad.speech_started", t=1000))
    assert tm.state.observed_pause_count == 1
    assert tm.state.user_avg_pause_ms == 660  # 80 % de 600 + 20 % de 900


def test_uncertain_final_uses_two_stage_timer_without_double_send():
    tm = TurnManager()
    tm.feed(VoiceEvent("vad.speech_started", t=0))
    tm.feed(VoiceEvent("stt.final", t=100, data={"text": "le ciel est bleu"}))
    arm = tm.feed(VoiceEvent("vad.speech_ended", t=200))[0]
    first = tm.feed(VoiceEvent(
        "timer.endpoint", t=200 + arm.data["wait_ms"],
        data={"turn_id": tm.state.current_turn_id, "pause_ms": arm.data["wait_ms"]},
    ))
    assert [command.name for command in first] == ["arm_endpoint_timer"]
    second = tm.feed(VoiceEvent(
        "timer.endpoint", t=3000,
        data={"turn_id": tm.state.current_turn_id, "pause_ms": 1800},
    ))
    assert [command.name for command in second] == ["start_llm"]
    stale = tm.feed(VoiceEvent(
        "timer.endpoint", t=3100,
        data={"turn_id": tm.state.current_turn_id, "pause_ms": 1900},
    ))
    assert stale == []

