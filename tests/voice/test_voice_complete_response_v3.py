"""Regression proofs for complete, audible Voice V3 answers."""
from __future__ import annotations

import pytest

from src.core_services.agent_service import (
    _VOICE_CONVERSATION_PROMPT,
    _should_emit_mood_narration,
)
from src.voice.v2 import AudioFrontend, FakeTTSProvider, VoiceV2Live
from src.voice.v2.live import resolve_barge_in_speaking_threshold
from src.voice.v2.observability import get_voice_telemetry


class _VAD:
    async def stream(self, _audio=None):
        if False:
            yield None

    def stop(self):
        return None


class _STT:
    async def transcribe(self, *_args, **_kwargs):
        return ""


class _Core:
    task_orchestrator = None

    async def chat(self, _text, source_channel="voice"):
        assert source_channel == "voice"
        return " ".join(f"Phrase {index}." for index in range(1, 7))


def test_voice_chat_prompt_requires_a_short_answer_with_a_real_ending():
    prompt = " ".join(_VOICE_CONVERSATION_PROMPT.lower().split())
    assert "deux à quatre phrases" in prompt
    assert "termine réellement l'idée" in prompt
    assert "aucune phrase coupée" in prompt
    assert "aucune liste" in prompt and "markdown" in prompt
    assert "n'annonce pas ton humeur" in prompt


def test_voice_never_emits_mood_change_banner_but_other_channels_keep_it():
    assert _should_emit_mood_narration("voice") is False
    assert _should_emit_mood_narration(" Voice ") is False
    assert _should_emit_mood_narration("web") is True
    assert _should_emit_mood_narration("telegram") is True


@pytest.mark.asyncio
async def test_voice_projection_keeps_six_short_complete_sentences():
    live = VoiceV2Live(
        _Core(), vad=_VAD(), stt=_STT(), tts=FakeTTSProvider()
    )
    answer = await live._llm_respond("Réponds complètement", language="fr")
    assert answer.startswith("Phrase 1.")
    assert answer.endswith("Phrase 6.")
    await live.speech.aclose()
    await live.runtime.aclose()


@pytest.mark.asyncio
async def test_agent_result_keeps_six_short_complete_sentences():
    live = VoiceV2Live(
        _Core(), vad=_VAD(), stt=_STT(), tts=FakeTTSProvider()
    )
    spoken = []

    async def capture(text, **_kwargs):
        spoken.append(text)

    live.speech.say = capture
    await live._speak_agent_result(
        " ".join(f"Résultat {index}." for index in range(1, 7)), language="fr"
    )
    assert spoken[0].startswith("Résultat 1.")
    assert spoken[0].endswith("Résultat 6.")
    await live.runtime.aclose()


def test_hands_free_without_aec_keeps_guarded_barge_in_available():
    get_voice_telemetry().set_dictation_active(False)
    live = VoiceV2Live(
        _Core(), vad=_VAD(), stt=_STT(), tts=FakeTTSProvider(),
        audio_frontend=AudioFrontend(profile="hands_free"),
    )
    live.tm.state.set_mode("speaking")
    assert live._barge_in_mode() == "guarded_no_aec"
    assert live._suppress_micro_input() is False


def test_browser_dictation_still_owns_microphone_exclusively():
    get_voice_telemetry().set_dictation_active(True)
    try:
        live = VoiceV2Live(
            _Core(), vad=_VAD(), stt=_STT(), tts=FakeTTSProvider(),
            audio_frontend=AudioFrontend(profile="hands_free"),
        )
        assert live._suppress_micro_input() is True
    finally:
        get_voice_telemetry().set_dictation_active(False)


def test_headset_keeps_full_duplex_barge_in_without_server_aec():
    get_voice_telemetry().set_dictation_active(False)
    live = VoiceV2Live(
        _Core(), vad=_VAD(), stt=_STT(), tts=FakeTTSProvider(),
        audio_frontend=AudioFrontend(profile="headset"),
    )
    live.tm.state.set_mode("speaking")
    assert live._barge_in_mode() == "full_duplex"
    assert live._suppress_micro_input() is False


def test_headset_does_not_raise_vad_threshold_during_playback():
    frontend = AudioFrontend(profile="headset")
    assert resolve_barge_in_speaking_threshold(
        frontend, energy_threshold=180
    ) is None


def test_hands_free_without_aec_keeps_echo_guard_during_playback():
    frontend = AudioFrontend(profile="hands_free")
    assert resolve_barge_in_speaking_threshold(
        frontend, energy_threshold=180
    ) == 486
