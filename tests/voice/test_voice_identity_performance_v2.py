import asyncio

import pytest

from src.voice.v2 import (
    AudioResult, LocalAudioPlayer, TTSProvider, TurnManager, VoiceEvent,
    VoiceProfile, VoiceRuntime, apply_pronunciations, classify_dialogue_act,
)
from src.voice.v2.providers.local_tts import LocalTTSAdapter
from src.voice.v2.voice_profile import load_profile, save_profile


def _noop():
    async def _():
        return None
    return _()


def test_voice_profile_roundtrip_and_pronunciation(tmp_path):
    profile = VoiceProfile(reference_consent_confirmed=True, reference_rights_note="original")
    path = tmp_path / "profile.json"
    save_profile(profile, path)
    loaded = load_profile(path)
    assert loaded.reference_consent_confirmed is True
    assert loaded.reference_rights_note == "original"
    assert "Louména" in apply_pronunciations("Lumena utilise MCP", loaded)
    assert "M C P" in apply_pronunciations("Lumena utilise MCP", loaded)


def test_dialogue_act_is_deterministic():
    assert classify_dialogue_act("Bonjour Charles") == "greeting"
    assert classify_dialogue_act("C'est terminé") == "success"
    assert classify_dialogue_act("Attention au délai") == "warning"
    assert classify_dialogue_act("Une erreur est survenue") == "error"
    assert classify_dialogue_act("Tu veux continuer ?") == "question"


@pytest.mark.asyncio
async def test_runtime_always_passes_concrete_profile_to_provider():
    seen = []

    class _Capture(TTSProvider):
        def is_available(self): return True
        async def synthesize(self, text, voice, cancel=None):
            seen.append((text, voice))
            return AudioResult(ok=False, text=text, provider="piper")

    profile = VoiceProfile(id="lumena_test")
    rt = VoiceRuntime(
        TurnManager(), _Capture(), LocalAudioPlayer(play_fn=lambda x: _noop()),
        voice_profile=profile, enabled=True,
    )
    await rt.speak("Lumena est prete")
    assert seen and seen[0][1] is profile
    assert "Louména" in seen[0][0]
    assert rt.status_report()["voice_profile"] == "lumena_test"


@pytest.mark.asyncio
async def test_runtime_sends_nfc_french_without_typographic_noise_to_tts():
    seen = []

    class _Capture(TTSProvider):
        def is_available(self): return True
        async def synthesize(self, text, voice, cancel=None):
            seen.append(text)
            return AudioResult(ok=False, text=text, provider="piper")

    rt = VoiceRuntime(
        TurnManager(), _Capture(), LocalAudioPlayer(play_fn=lambda x: _noop()),
        enabled=True,
    )
    await rt.speak('« J\u2019ai déjà vérifié — c\u2019est prêt ! » 😊')
    assert seen == ["J'ai déjà vérifié, c'est prêt!"]
    import unicodedata
    assert unicodedata.normalize("NFC", seen[0]) == seen[0]


@pytest.mark.asyncio
async def test_xtts_is_forbidden_without_explicit_reference_consent():
    seen = []

    class _Engine:
        _last_provider = "piper"
        async def _synthesize(self, text, *, local_only=False, allow_xtts=True):
            seen.append(allow_xtts)
            return None

    adapter = LocalTTSAdapter(tts=_Engine())
    await adapter.synthesize("bonjour", VoiceProfile(reference_consent_confirmed=False))
    await adapter.synthesize("bonjour", VoiceProfile(reference_consent_confirmed=True))
    assert seen == [False, True]


@pytest.mark.asyncio
async def test_voice_profile_requests_its_piper_model():
    seen = []

    class _Engine:
        _last_provider = "piper"
        async def _synthesize(
            self, text, *, local_only=False, allow_xtts=True, piper_model=None,
        ):
            seen.append(piper_model)
            return None

    profile = VoiceProfile()
    adapter = LocalTTSAdapter(tts=_Engine())
    await adapter.synthesize("bonjour", profile)
    assert profile.local.piper_model == "fr_FR-siwis-medium"
    assert seen == ["fr_FR-siwis-medium"]


@pytest.mark.asyncio
async def test_latency_metrics_are_exposed_without_blocking_runtime():
    class _One(TTSProvider):
        def is_available(self): return True
        async def synthesize(self, text, voice, cancel=None):
            return AudioResult(ok=True, text=text, provider="piper", duration_ms=1)

    tm = TurnManager()
    player = LocalAudioPlayer(play_fn=lambda x: _noop(), stop_fn=lambda: None)
    rt = VoiceRuntime(tm, _One(), player, respond_fn=lambda _: "Bonjour.", enabled=True)
    tm._dispatcher = rt.dispatch
    actor = asyncio.create_task(tm.run())
    await tm.emit(VoiceEvent("vad.speech_started"))
    await tm.emit(VoiceEvent("stt.final", data={"text": "salut"}))
    await tm.emit(VoiceEvent("endpoint.decision", data={"state": "turn_complete"}))
    for _ in range(50):
        if player.played:
            break
        await asyncio.sleep(0.01)
    report = rt.status_report()
    assert report["llm_ms"] is not None and report["llm_ms"] >= 0
    assert report["first_audio_ms"] is not None and report["first_audio_ms"] >= 0
    assert report["queue_depth"] >= 0
    await tm.shutdown(); await asyncio.wait_for(actor, timeout=1.0)
    await rt.aclose()
