from dataclasses import dataclass

import pytest

from src.voice.v2 import (
    LocalWakeWordProvider, VoiceActivationGate, VoiceSessionIdentity,
)
from src.voice.v2.input_sources import MicConversationSource
from src.voice.v2.providers.base import VADEvent


@pytest.mark.asyncio
async def test_packaged_wake_word_threshold_and_speaker_signal_are_separate():
    provider = LocalWakeWordProvider(
        lambda _audio: {"score": 0.91, "speaker_match": True},
        engine="fixture_kws", threshold=0.7,
    )
    result = await provider.detect(b"pcm")
    assert result.detected is True
    assert result.speaker_match is True
    assert provider.status()["speaker_identity_is_authorization"] is False


@pytest.mark.asyncio
async def test_wake_word_provider_failure_is_closed_and_observable():
    provider = LocalWakeWordProvider(
        lambda _audio: (_ for _ in ()).throw(RuntimeError("broken")),
        engine="fixture_kws",
    )
    result = await provider.detect(b"pcm")
    assert result.detected is False
    assert result.reason == "detector_error:RuntimeError"


def test_speaker_signal_never_changes_voice_identity_or_role():
    identity = VoiceSessionIdentity("voice:guest", "local:owner", "guest", None, False)
    gate = VoiceActivationGate(wake_phrase="Lumena")
    decision = gate.evaluate(
        "ouvre le calendrier", wake_detected=True, speaker_match=True
    )
    assert decision.accepted is True
    assert decision.reason == "wake_word_speaker_signal"
    assert identity.user_role == "guest"
    assert identity.trusted is False


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
async def test_acoustic_wake_word_can_open_gate_without_granting_permissions():
    collector = _Collector()
    provider = LocalWakeWordProvider(lambda _audio: 0.95, engine="fixture_kws")
    source = MicConversationSource(
        _VAD(), _STT("ouvre le calendrier"), collector,
        min_utterance_ms=0,
        activation_gate=VoiceActivationGate(wake_phrase="Lumena"),
        wake_word_provider=provider,
    )
    await source.run()
    final = next(event for event in collector.events if event.type == "stt.final")
    assert final.data["text"] == "ouvre le calendrier"
    assert final.data["activation"] == "wake_word"
    assert final.data["terminal_reason"] == "text"


@pytest.mark.asyncio
async def test_missing_wake_model_keeps_transcript_phrase_fallback(monkeypatch):
    monkeypatch.delenv("LUMENA_VOICE_WAKE_MODEL", raising=False)
    provider = LocalWakeWordProvider.from_env()
    assert provider.is_available() is False
    assert provider.status()["reason"] == "model_not_configured"
    gate = VoiceActivationGate(wake_phrase="Lumena")
    assert gate.evaluate("Lumena réponds-moi").accepted is True

