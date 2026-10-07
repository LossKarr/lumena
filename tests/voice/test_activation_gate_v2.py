from __future__ import annotations

from dataclasses import dataclass

import pytest

from src.voice.v2.activation import VoiceActivationGate, activation_gate_from_env
from src.voice.v2.events import VoiceEvent
from src.voice.v2.input_sources import MicConversationSource
from src.voice.v2.providers.base import VADEvent
from src.voice.v2.turn_manager import TurnManager


def test_wake_phrase_rejects_ambient_then_opens_bounded_window():
    now = [100.0]
    gate = VoiceActivationGate(
        wake_phrase="Lumena", conversation_window_s=10, clock=lambda: now[0]
    )
    rejected = gate.evaluate("une conversation dans la pièce")
    assert rejected.accepted is False
    assert rejected.reason == "wake_phrase_missing"

    activated = gate.evaluate("Lumena, quelle heure est-il ?")
    assert activated.accepted is True
    assert activated.activated is True
    assert activated.text == "quelle heure est-il"

    now[0] = 109.0
    assert gate.evaluate("et demain ?").text == "et demain ?"
    now[0] = 120.0
    assert gate.evaluate("toujours là ?").accepted is False


def test_push_to_talk_is_one_utterance_only():
    gate = VoiceActivationGate(mode="push_to_talk")
    assert gate.evaluate("ignoré").accepted is False
    gate.arm()
    assert gate.evaluate("accepté").accepted is True
    assert gate.evaluate("ignoré ensuite").accepted is False


def test_invalid_activation_mode_is_rejected():
    with pytest.raises(ValueError):
        VoiceActivationGate(mode="imaginary")


def test_activation_factory_is_bounded_and_defaults_secure(monkeypatch):
    monkeypatch.delenv("LUMENA_VOICE_ACTIVATION_MODE", raising=False)
    monkeypatch.setenv("LUMENA_VOICE_CONVERSATION_WINDOW_S", "9999")
    gate = activation_gate_from_env()
    assert gate.mode == "wake_phrase"
    assert gate.conversation_window_s == 300.0


def test_turn_manager_closes_rejected_provisional_turn_without_llm():
    tm = TurnManager()
    tm.feed(VoiceEvent("vad.speech_started"))
    tm.feed(VoiceEvent("vad.speech_ended"))
    commands = tm.feed(VoiceEvent("activation.rejected", data={"reason": "wake_phrase_missing"}))
    assert tm.state.mode == "wake_listening"
    assert tm.state.current_turn_id is None
    assert [command.name for command in commands] == ["cancel_endpoint_timer"]
    assert "start_llm" not in [command.name for command in tm.emitted]


class _VAD:
    SAMPLE_RATE = 16000
    SAMPLE_WIDTH = 2
    last_utterance = b"\0" * 12000

    async def stream(self, _audio=None):
        yield VADEvent(kind="speech_started", t=1)
        yield VADEvent(kind="speech_ended", t=2)

    def stop(self):
        return None


@dataclass
class _STT:
    text: str

    async def transcribe(self, _audio, **_kwargs):
        return self.text


class _Collector:
    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


@pytest.mark.asyncio
async def test_mic_source_never_emits_ambient_transcript_to_turn_manager():
    collector = _Collector()
    source = MicConversationSource(
        _VAD(), _STT("discussion ambiante"), collector,
        min_utterance_ms=0,
        activation_gate=VoiceActivationGate(wake_phrase="Lumena"),
    )
    await source.run()
    types = [event.type for event in collector.events]
    assert types == [
        "vad.speech_started", "vad.speech_ended", "stt.started",
        "activation.rejected",
    ]
    assert not any(event.type == "stt.final" for event in collector.events)


@pytest.mark.asyncio
async def test_mic_source_only_forwards_text_after_local_activation():
    collector = _Collector()
    source = MicConversationSource(
        _VAD(), _STT("Lumena ouvre le calendrier"), collector,
        min_utterance_ms=0,
        activation_gate=VoiceActivationGate(wake_phrase="Lumena"),
    )
    await source.run()
    final = next(event for event in collector.events if event.type == "stt.final")
    assert final.data["text"] == "ouvre le calendrier"
    assert final.data["activation"] == "wake_phrase"
    assert final.data["terminal_reason"] == "text"
