"""VOICE3-10 — acoustic front-end contracts without claiming untested AEC."""
import wave

import pytest

from src.voice.v2 import AudioFrontend, LocalAudioPlayer
from src.voice.v2.audio_frontend import WebRTCLocalAudioProcessor


def test_hands_free_without_native_component_is_honestly_degraded(monkeypatch):
    monkeypatch.setattr(
        "src.voice.v2.audio_frontend.server_aec_component_available", lambda: False
    )
    frontend = AudioFrontend(profile="hands_free")
    report = frontend.report()
    assert report["aec_available"] is False
    assert report["aec_active"] is False
    assert report["degraded_reason"] == "server_aec_unavailable"
    assert frontend.process_capture(b"pcm") == b"pcm"


def test_headset_profile_is_valid_passthrough_not_false_error():
    frontend = AudioFrontend(profile="headset")
    assert frontend.report()["profile"] == "headset"
    assert frontend.report()["aec_active"] is False
    assert frontend.report()["degraded_reason"] == ""


def test_injected_processor_receives_capture_and_playback_reference():
    class _Processor:
        name = "test-webrtc-apm"
        def __init__(self): self.reference = None
        def process_capture(self, pcm, *, sample_rate): return pcm + b"-clean"
        def process_playback_reference(self, audio, *, sample_rate=None):
            self.reference = audio

    processor = _Processor()
    frontend = AudioFrontend(processor=processor, profile="hands_free")
    assert frontend.process_capture(b"voice", sample_rate=16000) == b"voice-clean"
    frontend.register_playback_reference("answer.wav")
    assert processor.reference == "answer.wav"
    assert frontend.report()["aec_active"] is True


@pytest.mark.asyncio
async def test_player_registers_reference_before_audio_starts():
    order = []

    class _Frontend:
        def register_playback_reference(self, target, sample_rate=None):
            order.append(("reference", str(target)))

    async def play(target):
        order.append(("play", str(target)))

    player = LocalAudioPlayer(play_fn=play, audio_frontend=_Frontend())
    player.set_generation("g")
    assert await player.play(generation_id="g", path="answer.wav") == "played"
    assert [item[0] for item in order] == ["reference", "play"]


@pytest.mark.asyncio
async def test_player_closes_reference_when_playback_fails():
    order = []

    class _Frontend:
        def register_playback_reference(self, _target, sample_rate=None):
            order.append("reference")

        def end_playback_reference(self):
            order.append("end")

    async def fail(_target):
        order.append("play")
        raise RuntimeError("speaker unavailable")

    player = LocalAudioPlayer(play_fn=fail, audio_frontend=_Frontend())
    player.set_generation("g")
    with pytest.raises(RuntimeError, match="speaker unavailable"):
        await player.play(generation_id="g", path="answer.wav")
    assert order == ["reference", "play", "end"]


def test_webrtc_processor_aligns_reference_without_persisting_audio(tmp_path):
    clock = [10.0]
    path = tmp_path / "reference.wav"
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(b"\x01\x00" * 1600)
    processor = WebRTCLocalAudioProcessor(clock=lambda: clock[0])
    processor.process_playback_reference(path)
    clock[0] += 0.02
    processor.begin_capture()
    cleaned = processor.process_capture(b"\x02\x00" * 800, sample_rate=16000)
    assert len(cleaned) == 1600
    assert list(tmp_path.iterdir()) == [path]
    assert not processor._references


def test_from_env_activates_local_webrtc_only_for_hands_free(monkeypatch):
    sentinel = object()
    monkeypatch.setenv("LUMENA_VOICE_ACOUSTIC_PROFILE", "hands_free")
    monkeypatch.setattr(
        "src.voice.v2.audio_frontend.create_local_audio_processor",
        lambda *, profile: sentinel,
    )
    assert AudioFrontend.from_env().processor is sentinel
