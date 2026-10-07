"""VOICE3-9 — live integration of language policy and one bounded repair."""
import asyncio

import pytest

from src.voice.v2 import FakeTTSProvider, VoiceCommand, VoiceV2Live


class _VAD:
    async def stream(self, _audio=None):
        if False:
            yield None
    def stop(self): pass


class _STT:
    async def transcribe(self, *_args, **_kwargs): return ""


class _LLM:
    def __init__(self): self.calls = []
    async def chat(self, messages, **kwargs):
        self.calls.append((messages, kwargs))
        return "Bonjour, c'est corrigé. Je peux maintenant continuer avec la suite."


class _Core:
    def __init__(self):
        self.llm = _LLM()
        self.task_orchestrator = None
    async def chat(self, _text, source_channel="voice"):
        return "Hola, esto está hecho. Ahora puedo continuar con la siguiente tarea."


@pytest.mark.asyncio
async def test_wrong_generated_language_is_repaired_once_before_speech():
    core = _Core()
    live = VoiceV2Live(core, vad=_VAD(), stt=_STT(), tts=FakeTTSProvider())
    answer = await live._llm_respond("Où en es-tu ?", language="fr")
    assert answer.startswith("Bonjour")
    assert len(core.llm.calls) == 1
    await live.speech.aclose()
    await live.runtime.aclose()


@pytest.mark.asyncio
async def test_dispatch_carries_explicit_session_language_to_runtime():
    core = _Core()
    live = VoiceV2Live(core, vad=_VAD(), stt=_STT(), tts=FakeTTSProvider())
    captured = []

    class _Runtime:
        async def dispatch(self, commands): captured.extend(commands)

    class _Timer:
        async def dispatch(self, _commands): return None

    live.runtime = _Runtime()
    live.timer = _Timer()
    await live._dispatch([VoiceCommand("start_llm", {
        "text": "Parle maintenant uniquement en anglais",
        "generation_id": "g1", "turn_id": "t1",
        "language": "fr", "language_probability": 0.91,
    })])
    assert captured[0].data["language"] == "en"
    assert live.language_policy.state.session_language == "en"
    await live.speech.aclose()
