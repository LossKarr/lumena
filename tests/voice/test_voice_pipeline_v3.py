"""VOICE3-4 — speech synthesis look-ahead and exclusive playback."""
import asyncio

import pytest

from src.voice.v2 import LocalAudioPlayer, VoiceCommand, VoiceRuntime
from src.voice.v2.providers.base import TTSAudioChunk


class _DispatchingTurnManager:
    def __init__(self):
        self.runtime = None
        self.events = []

    async def emit(self, event):
        self.events.append(event.type)
        if event.type == "tts.chunk_ready":
            await self.runtime._handle(VoiceCommand("play_audio", dict(event.data)))


@pytest.mark.asyncio
async def test_next_segment_synthesis_starts_while_current_segment_is_playing():
    first_playing = asyncio.Event()
    allow_first_to_finish = asyncio.Event()
    second_synthesis_started = asyncio.Event()
    overlap = {"proved": False}

    async def play(target):
        if str(target) == "segment-0.wav":
            first_playing.set()
            await allow_first_to_finish.wait()

    class _StreamingTTS:
        supports_streaming = True

        async def stream(self, _text, voice=None):
            yield TTSAudioChunk(
                sequence=0, text="Première phrase.", audio_path="segment-0.wav",
                duration_ms=100, provider="fake-local",
            )
            overlap["proved"] = first_playing.is_set() and not allow_first_to_finish.is_set()
            second_synthesis_started.set()
            yield TTSAudioChunk(
                sequence=1, text="Deuxième phrase.", audio_path="segment-1.wav",
                duration_ms=100, provider="fake-local",
            )

    tm = _DispatchingTurnManager()
    player = LocalAudioPlayer(play_fn=play, stop_fn=lambda: None)
    runtime = VoiceRuntime(tm, _StreamingTTS(), player, enabled=True)
    tm.runtime = runtime

    generation = await runtime.speak("Première phrase. Deuxième phrase.")
    await asyncio.wait_for(second_synthesis_started.wait(), timeout=1)
    assert overlap["proved"] is True
    allow_first_to_finish.set()
    assert await runtime.wait_finished(generation, timeout_s=1) is True
    assert [item["sequence"] for item in player.played] == [0, 1]
    await runtime.aclose()


@pytest.mark.asyncio
async def test_stale_queued_segment_never_plays_after_generation_change():
    player = LocalAudioPlayer(play_fn=lambda _target: asyncio.sleep(0), stop_fn=lambda: None)
    player.set_generation("current")
    result = await player.play(
        generation_id="old", sequence=1, text="périmé", path="old.wav"
    )
    assert result == "dropped_stale"
    assert player.played == []
