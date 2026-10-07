"""Voice V2 — extraction `_synthesize` dans LumenaTTS (préservante, sans audio réel).

Instance construite via object.__new__ (pas de __init__ lourd) ; providers mockés,
`_play_audio` mocké. Prouve :
- `_synthesize` ne joue JAMAIS ;
- `speak(wait=True)` joue toujours ;
- `speak(wait=False)` ne bloque pas (playback en tâche de fond) ;
- `local_only=True` interdit Edge-TTS (cloud).
"""
import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.voice.tts import LumenaTTS, _audio_cache_key, _valid_mp3_file


_VALID_MP3 = b"\xff\xf3\x64\xc4" + (b"\x00" * 1024)


def _make_tts(tmp_path, mode="offline", xtts_ok=True):
    t = object.__new__(LumenaTTS)            # bypass __init__ (pas de chargement lourd)
    t._tts_mode = mode
    t.cache_dir = tmp_path
    t.enable_fallback = False                 # pas de pyttsx3
    t._stop_speaking = False
    t.metrics = MagicMock()
    piper = MagicMock()
    piper.model_name = "fr_FR-test"
    piper.is_available.return_value = True

    async def _piper_gen(text, path, *, model_name=None):
        Path(path).write_bytes(b"FAKEWAV")
        return True

    piper.generate = AsyncMock(side_effect=_piper_gen)
    t.piper = piper
    t.voice = "x"; t.rate = "+0%"; t.volume = "+0%"; t.pitch = "+0Hz"
    xtts = MagicMock()
    xtts.is_available.return_value = xtts_ok

    async def _gen(text, path):
        Path(path).write_bytes(b"FAKEWAV")
        return True
    xtts.generate = AsyncMock(side_effect=_gen)
    t.xtts = xtts
    t._play_audio = AsyncMock()
    return t


@pytest.mark.asyncio
async def test_synthesize_never_plays(tmp_path):
    t = _make_tts(tmp_path)
    path = await t._synthesize("Bonjour Lumena")
    assert path is not None and Path(path).exists()
    t._play_audio.assert_not_called()        # SYNTHÈSE seule, jamais de playback


@pytest.mark.asyncio
async def test_speak_wait_true_always_plays(tmp_path):
    t = _make_tts(tmp_path)
    path = await t.speak("Bonjour Lumena", wait=True)
    assert path is not None
    t._play_audio.assert_awaited_once()      # speak joue toujours


@pytest.mark.asyncio
async def test_speak_wait_false_non_blocking(tmp_path):
    t = _make_tts(tmp_path)
    path = await t.speak("Bonjour Lumena", wait=False)
    assert path is not None                  # rend la main sans attendre la lecture
    await asyncio.sleep(0.05)                 # laisse la tâche de fond se planifier
    t._play_audio.assert_awaited()            # joué en arrière-plan


@pytest.mark.asyncio
async def test_synthesize_local_only_forbids_edge(tmp_path, monkeypatch):
    import src.voice.tts as ttsmod
    monkeypatch.setattr(ttsmod, "EDGE_TTS_AVAILABLE", True, raising=False)
    comm = MagicMock()
    monkeypatch.setattr(ttsmod, "edge_tts", MagicMock(Communicate=comm), raising=False)

    # xtts indispo + piper None + pyttsx3 off => seule Edge pourrait répondre
    t = _make_tts(tmp_path, mode="premium", xtts_ok=False)
    t.piper = None

    # local_only=True : Edge interdit -> aucun provider -> None, Edge jamais tenté
    path = await t._synthesize("Bonjour", local_only=True)
    assert path is None
    comm.assert_not_called()

    # cloud autorisé (local_only=False) : Edge est bien tenté
    await t._synthesize("Bonjour", local_only=False)
    comm.assert_called()


@pytest.mark.asyncio
async def test_piper_cache_and_generation_are_scoped_to_requested_model(tmp_path):
    seen = []

    class _Piper:
        model_name = "fr_FR-siwis-low"
        def is_available(self, model_name=None):
            return True
        async def generate(self, text, path, *, model_name=None):
            seen.append(model_name)
            Path(path).write_bytes(b"FAKEWAV")
            return True

    t = _make_tts(tmp_path, mode="offline", xtts_ok=False)
    t.piper = _Piper()
    path = await t._synthesize("Déjà prête", piper_model="fr_FR-siwis-medium")
    assert path is not None
    assert "fr_FR-siwis-medium" in Path(path).name
    assert "utf8_v3" in Path(path).name
    assert seen == ["fr_FR-siwis-medium"]


@pytest.mark.asyncio
async def test_unsupported_language_never_uses_default_french_piper(tmp_path):
    t = _make_tts(tmp_path, mode="offline", xtts_ok=False)
    result = await t._synthesize(
        "Hello, this must not use a French voice.",
        local_only=True,
        piper_model="unsupported_language",
    )
    assert result is None
    t.piper.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_cache_identity_changes_with_voice_model_and_prosody(tmp_path, monkeypatch):
    import src.voice.tts as ttsmod
    monkeypatch.setattr(ttsmod, "EDGE_TTS_AVAILABLE", True, raising=False)

    async def save(path):
        Path(path).write_bytes(_VALID_MP3)

    communicate = MagicMock()
    communicate.save = AsyncMock(side_effect=save)
    factory = MagicMock(return_value=communicate)
    monkeypatch.setattr(ttsmod, "edge_tts", MagicMock(Communicate=factory), raising=False)

    t = _make_tts(tmp_path, mode="fast", xtts_ok=False)
    t.piper = None
    first = await t._synthesize("Même texte")
    t.voice = "fr-FR-Other"
    second = await t._synthesize("Même texte")
    t.rate = "+10%"
    third = await t._synthesize("Même texte")

    assert len({first.name, second.name, third.name}) == 3
    assert factory.call_count == 3


@pytest.mark.asyncio
async def test_zero_byte_edge_cache_is_regenerated_atomically(tmp_path, monkeypatch):
    import src.voice.tts as ttsmod
    monkeypatch.setattr(ttsmod, "EDGE_TTS_AVAILABLE", True, raising=False)

    async def save(path):
        Path(path).write_bytes(_VALID_MP3)

    communicate = MagicMock()
    communicate.save = AsyncMock(side_effect=save)
    factory = MagicMock(return_value=communicate)
    monkeypatch.setattr(
        ttsmod, "edge_tts", MagicMock(Communicate=factory), raising=False
    )
    t = _make_tts(tmp_path, mode="fast", xtts_ok=False)
    t.piper = None
    text = "Je termine vraiment cette phrase."
    key = _audio_cache_key(
        text, "edge-tts", voice=t.voice, rate=t.rate,
        volume=t.volume, pitch=t.pitch,
    )
    cached = tmp_path / f"lumena_edge_v3_{key}.mp3"
    cached.write_bytes(b"")

    result = await t._synthesize(text)

    assert result == cached
    assert _valid_mp3_file(cached)
    assert communicate.save.await_count == 1
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.asyncio
async def test_incomplete_edge_generation_falls_back_to_piper(tmp_path, monkeypatch):
    import src.voice.tts as ttsmod
    monkeypatch.setattr(ttsmod, "EDGE_TTS_AVAILABLE", True, raising=False)

    async def save_empty(path):
        Path(path).write_bytes(b"")

    communicate = MagicMock()
    communicate.save = AsyncMock(side_effect=save_empty)
    monkeypatch.setattr(
        ttsmod,
        "edge_tts",
        MagicMock(Communicate=MagicMock(return_value=communicate)),
        raising=False,
    )
    t = _make_tts(tmp_path, mode="fast", xtts_ok=False)

    result = await t._synthesize("La réponse doit rester audible jusqu'au bout.")

    assert result is not None and result.suffix == ".wav"
    assert t._last_provider == "piper"
    assert not list(tmp_path.glob("*.part"))
