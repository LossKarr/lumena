"""Voice V2 — VoiceProfile + contrat TTSProvider (fake + adaptateur local lazy).

Aucun audio réel : l'adaptateur local est testé avec un `tts` injecté (mock async),
jamais le moteur réel. Prouve aussi que l'import de l'adaptateur reste LÉGER
(import paresseux de la stack audio).
"""
import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.voice.v2 import (
    VoiceProfile, LUMENA_DEFAULT, load_profile, save_profile,
    get_voice_profile_status,
    FakeTTSProvider, LocalTTSAdapter, CancelToken, AudioResult,
)


# ── VoiceProfile ──────────────────────────────────────────────────────────────
def test_lumena_default_profile():
    assert LUMENA_DEFAULT.id == "lumena_default"
    assert LUMENA_DEFAULT.language == "fr"
    assert LUMENA_DEFAULT.local.xtts_reference.endswith("lumena_voice.wav")


def test_profile_roundtrip(tmp_path):
    p = tmp_path / "profile.json"
    prof = VoiceProfile()
    prof.persona.tone = "posé"
    save_profile(prof, p)
    loaded = load_profile(p)
    assert loaded.persona.tone == "posé"
    assert loaded.id == "lumena_default"


def test_load_profile_absent_falls_back_to_default(tmp_path):
    loaded = load_profile(tmp_path / "__absent__.json")
    assert loaded is LUMENA_DEFAULT


def test_load_profile_corrupt_falls_back(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{ not json", encoding="utf-8")
    assert load_profile(p) is LUMENA_DEFAULT
    status = get_voice_profile_status()
    assert status["ok"] is False
    assert status["source"] == "default_corrupt"
    assert "json" in str(status["error"]).lower() or "expecting" in str(status["error"]).lower()


def test_profile_recovers_previous_durable_generation(tmp_path):
    path = tmp_path / "profile.json"
    first = VoiceProfile(label="Lumena stable")
    second = VoiceProfile(label="Lumena nouvelle")
    save_profile(first, path)
    save_profile(second, path)
    path.write_text("{corrompu", encoding="utf-8")

    recovered = load_profile(path)

    assert recovered.label == "Lumena stable"
    assert get_voice_profile_status()["source"] == "backup"
    assert get_voice_profile_status()["recovered"] is True


def test_failed_profile_save_preserves_live_file(tmp_path, monkeypatch):
    path = tmp_path / "profile.json"
    save_profile(VoiceProfile(label="stable"), path)
    original = path.read_bytes()

    def fail_replace(_src, _dst):
        raise OSError("disk full")

    monkeypatch.setattr("src.voice.v2.voice_profile.os.replace", fail_replace)
    with pytest.raises(OSError, match="disk full"):
        save_profile(VoiceProfile(label="new"), path)
    assert path.read_bytes() == original
    assert not path.with_suffix(".json.tmp").exists()


# ── FakeTTSProvider (contrat) ─────────────────────────────────────────────────
@pytest.mark.asyncio
async def test_fake_tts_synthesize_and_stream():
    tts = FakeTTSProvider(chunk_ms=100)
    assert tts.is_available() and tts.locality == "local"
    res = await tts.synthesize("Un. Deux. Trois.", LUMENA_DEFAULT)
    assert res.ok and res.chunk_count == 3 and res.duration_ms == 300
    chunks = [c async for c in tts.stream("Un. Deux. Trois.", LUMENA_DEFAULT)]
    assert [c.text for c in chunks] == ["Un", "Deux", "Trois"]
    assert [c.sequence for c in chunks] == [0, 1, 2]


@pytest.mark.asyncio
async def test_fake_tts_respects_cancel():
    tts = FakeTTSProvider()
    tok = CancelToken(); tok.cancel()
    res = await tts.synthesize("Un. Deux.", LUMENA_DEFAULT, cancel=tok)
    assert res.ok is False
    chunks = [c async for c in tts.stream("Un. Deux.", LUMENA_DEFAULT, cancel=tok)]
    assert chunks == []


def test_fake_tts_unavailable():
    assert FakeTTSProvider(available=False).is_available() is False


@pytest.mark.asyncio
async def test_default_provider_stream_preserves_named_audio_metadata():
    from src.voice.v2.providers.base import TTSProvider

    class _One(TTSProvider):
        def is_available(self): return True
        async def synthesize(self, text, voice, cancel=None):
            return AudioResult(
                ok=True, text=text, audio_path="voice.wav", duration_ms=250,
                audio_format="pcm16", sample_rate=24000, channels=1,
                provider="local-test",
            )

    chunks = [chunk async for chunk in _One().stream("bonjour", LUMENA_DEFAULT)]
    assert chunks[0].audio_path == "voice.wav"
    assert chunks[0].duration_ms == 250
    assert chunks[0].sample_rate == 24000
    assert chunks[0].provider == "local-test"


def test_local_segments_drop_non_speakable_punctuation():
    from src.voice.v2.providers.local_tts import _segments
    assert _segments("Bonjour ! ... 👀\nOK.") == ["Bonjour ! OK."]


def test_local_segments_bundle_short_sentences_to_avoid_synthesis_holes():
    from src.voice.v2.providers.local_tts import _segments
    text = "Oui, je regarde. Le fichier est valide. La suite est prête."
    chunks = _segments(text)
    assert chunks == [text]
    assert all(len(chunk) <= 180 for chunk in chunks)


def test_local_segments_keep_long_answer_in_bounded_interruption_units():
    from src.voice.v2.providers.local_tts import _segments
    parts = [f"Phrase {index} " + ("utile " * 15) + "." for index in range(4)]
    chunks = _segments(" ".join(parts))
    assert len(chunks) == 4
    assert chunks == parts


# ── LocalTTSAdapter (tts injecté, jamais le moteur réel) ──────────────────────
@pytest.mark.asyncio
async def test_local_adapter_synthesizes_without_playing(monkeypatch):
    monkeypatch.delenv("LUMENA_VOICE_CLOUD_ALLOWED", raising=False)  # cloud OFF par défaut
    fake_tts = MagicMock()
    fake_tts._synthesize = AsyncMock(return_value=Path("audio_out.wav"))
    fake_tts.speak = AsyncMock(return_value=Path("PLAYED.wav"))      # ne doit PAS être appelé
    adapter = LocalTTSAdapter(tts=fake_tts)
    assert adapter.is_available() is True
    res = await adapter.synthesize("Bonjour", LUMENA_DEFAULT)
    assert isinstance(res, AudioResult) and res.ok is True
    assert res.audio_path == "audio_out.wav"
    fake_tts._synthesize.assert_awaited_once()
    fake_tts.speak.assert_not_awaited()     # l'adaptateur ne joue plus


@pytest.mark.asyncio
async def test_local_adapter_local_only_when_cloud_disallowed(monkeypatch):
    monkeypatch.delenv("LUMENA_VOICE_CLOUD_ALLOWED", raising=False)
    fake_tts = MagicMock(); fake_tts._synthesize = AsyncMock(return_value=Path("x.wav"))
    adapter = LocalTTSAdapter(tts=fake_tts)
    await adapter.synthesize("Bonjour", LUMENA_DEFAULT)
    # cloud interdit -> local_only=True passé à _synthesize (donc pas d'Edge)
    _, kwargs = fake_tts._synthesize.call_args
    assert kwargs.get("local_only") is True


@pytest.mark.asyncio
async def test_local_adapter_allows_cloud_when_enabled(monkeypatch):
    monkeypatch.setenv("LUMENA_VOICE_CLOUD_ALLOWED", "1")
    fake_tts = MagicMock(); fake_tts._synthesize = AsyncMock(return_value=Path("x.wav"))
    adapter = LocalTTSAdapter(tts=fake_tts)
    await adapter.synthesize("Bonjour", LUMENA_DEFAULT)
    _, kwargs = fake_tts._synthesize.call_args
    assert kwargs.get("local_only") is False


@pytest.mark.asyncio
async def test_local_adapter_cancel_before_synth():
    fake_tts = MagicMock(); fake_tts._synthesize = AsyncMock(return_value=Path("x.wav"))
    adapter = LocalTTSAdapter(tts=fake_tts)
    tok = CancelToken(); tok.cancel()
    res = await adapter.synthesize("Bonjour", LUMENA_DEFAULT, cancel=tok)
    assert res.ok is False
    fake_tts._synthesize.assert_not_awaited()   # annulé avant tout appel


@pytest.mark.asyncio
async def test_local_adapter_refuses_non_speakable_text():
    fake_tts = MagicMock(); fake_tts._synthesize = AsyncMock(return_value=Path("x.wav"))
    adapter = LocalTTSAdapter(tts=fake_tts)
    res = await adapter.synthesize("...", LUMENA_DEFAULT)
    assert res.ok is False
    fake_tts._synthesize.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_adapter_stop_passthrough():
    fake_tts = MagicMock()
    adapter = LocalTTSAdapter(tts=fake_tts)
    await adapter.stop()
    fake_tts.stop_speaking.assert_called_once()


# ── L'adaptateur local n'importe PAS la stack audio au niveau module ──────────
def test_local_adapter_module_has_no_top_level_audio_import():
    """import paresseux : `src.voice.tts` n'apparaît PAS dans les imports module-level."""
    import src.voice.v2.providers.local_tts as mod
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    top_imports = []
    for node in tree.body:  # uniquement le niveau module (pas dans les fonctions)
        if isinstance(node, ast.Import):
            top_imports += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            top_imports.append(node.module or "")
    assert not any("voice.tts" in (m or "") or m == "src.voice.tts" for m in top_imports), top_imports
