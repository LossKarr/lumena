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


def test_local_segments_drop_non_speakable_punctuation():
    from src.voice.v2.providers.local_tts import _segments
    assert _segments("Bonjour ! ... 👀\nOK.") == ["Bonjour !", "OK."]


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
