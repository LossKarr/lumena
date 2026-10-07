"""VOICE3-8 — a language request must select a compatible voice or fail closed."""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.voice.v2 import LocalAudioPlayer, SpeechCoordinator, VoiceRuntime
from src.voice.v2.providers.local_tts import LocalTTSAdapter
from src.voice.v2.voice_profile import VoiceLocalEngines, VoiceProfile


def test_profile_resolves_only_installed_language_models():
    local = VoiceLocalEngines(piper_models={
        "fr": "fr_FR-siwis-medium", "en": "en_GB-alba-medium",
    })
    assert local.piper_model_for("fr-FR") == "fr_FR-siwis-medium"
    assert local.piper_model_for("en") == "en_GB-alba-medium"
    assert local.piper_model_for("es") is None


@pytest.mark.asyncio
async def test_adapter_never_falls_back_to_french_piper_for_english():
    class _Engine:
        _last_provider = ""
        def __init__(self): self.seen = None
        async def _synthesize(self, text, *, local_only=False, piper_model=None):
            self.seen = piper_model
            return None

    engine = _Engine()
    profile = VoiceProfile(language="en")
    adapter = LocalTTSAdapter(tts=engine)
    await adapter.synthesize("Hello.", profile)
    assert engine.seen == "unsupported_language"


@pytest.mark.asyncio
async def test_runtime_passes_requested_language_as_per_generation_profile():
    seen = []

    class _TTS:
        supports_streaming = False

        async def synthesize(self, text, voice):
            seen.append(voice.language)
            from src.voice.v2.providers.base import AudioResult
            return AudioResult(ok=False, text=text)

    class _TM:
        async def emit(self, _event):
            return None

    runtime = VoiceRuntime(
        _TM(), _TTS(), LocalAudioPlayer(play_fn=lambda _: asyncio.sleep(0)), enabled=True
    )
    await runtime.speak("Hello.", language="en")
    assert seen == ["en"]
    assert runtime.voice_profile.language == "fr"
    await runtime.aclose()


@pytest.mark.asyncio
async def test_coordinator_forwards_language_without_breaking_default_requests():
    class _Runtime:
        def __init__(self): self.calls = []
        async def speak(self, text, *, turn, language=None):
            self.calls.append((text, language))
            return f"g-{len(self.calls)}"
        async def wait_finished(self, _generation, timeout_s=1): return True

    runtime = _Runtime()
    coordinator = SpeechCoordinator(runtime)
    await coordinator.say("Hola.", language="es")
    await coordinator.say("Bonjour.")
    assert await coordinator.wait_idle(timeout_s=1)
    assert runtime.calls == [("Hola.", "es"), ("Bonjour.", None)]
    await coordinator.aclose()
