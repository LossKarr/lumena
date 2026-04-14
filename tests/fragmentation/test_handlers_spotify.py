"""
Tests unitaires pour handlers/spotify.py — 8 handlers.

Convention: chaque handler reçoit un HandlerContext et retourne HandlerResult.
Le hub est mocké via ctx._spotify_hub.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.spotify import (
    spotify_api_play_handler,
    spotify_pause_handler,
    spotify_resume_handler,
    spotify_next_handler,
    spotify_prev_handler,
    spotify_volume_handler,
    spotify_current_handler,
    spotify_queue_handler,
    get_spotify_handler_defs,
)


@pytest.fixture
def ctx(tmp_path):
    c = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=tmp_path / "workspace")
    c._spotify_hub = MagicMock()
    return c


# ─── spotify_api_play ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spotify_api_play_success(ctx):
    ctx._spotify_hub.search_and_play.return_value = {
        "success": True, "name": "Song", "artist": "Artist", "type": "track"
    }
    r = await spotify_api_play_handler(ctx, query="Song Artist")
    assert r.success
    assert "Song" in r.output


@pytest.mark.asyncio
async def test_spotify_api_play_hub_failure_with_fallback(ctx):
    """Quand le hub échoue avec RuntimeError, tente le fallback computer_use."""
    ctx._spotify_hub.search_and_play.side_effect = RuntimeError("API not configured")
    # Mock the import inside the except block
    mock_cu = MagicMock()
    mock_fb = AsyncMock(return_value=HandlerResult.ok("Fallback played via keyboard"))
    mock_cu.spotify_play_handler = mock_fb
    with patch.dict(sys.modules, {"src.reasoning.handlers.computer_use": mock_cu}):
        r = await spotify_api_play_handler(ctx, query="Song")
        assert r.success
        assert "Fallback" in r.output


@pytest.mark.asyncio
async def test_spotify_api_play_hub_failure_no_fallback(ctx):
    """Quand le hub retourne un dict avec success=False, pas de fallback."""
    ctx._spotify_hub.search_and_play.return_value = {"success": False, "error": "track not found"}
    r = await spotify_api_play_handler(ctx, query="Unknown")
    assert not r.success


# ─── spotify_pause ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spotify_pause_success(ctx):
    ctx._spotify_hub.pause.return_value = {"success": True}
    r = await spotify_pause_handler(ctx)
    assert r.success
    assert "Pause" in r.output or "pause" in r.output.lower()


@pytest.mark.asyncio
async def test_spotify_pause_failure(ctx):
    ctx._spotify_hub.pause.return_value = {"success": False, "error": "no active device"}
    r = await spotify_pause_handler(ctx)
    assert not r.success


# ─── spotify_resume ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spotify_resume_success(ctx):
    ctx._spotify_hub.resume.return_value = {"success": True}
    r = await spotify_resume_handler(ctx)
    assert r.success


@pytest.mark.asyncio
async def test_spotify_resume_failure(ctx):
    ctx._spotify_hub.resume.return_value = {"success": False, "error": "err"}
    r = await spotify_resume_handler(ctx)
    assert not r.success


# ─── spotify_next ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spotify_next_success(ctx):
    ctx._spotify_hub.next_track.return_value = {"success": True}
    r = await spotify_next_handler(ctx)
    assert r.success


# ─── spotify_prev ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spotify_prev_success(ctx):
    ctx._spotify_hub.prev_track.return_value = {"success": True}
    r = await spotify_prev_handler(ctx)
    assert r.success


# ─── spotify_volume ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spotify_volume_success(ctx):
    ctx._spotify_hub.set_volume.return_value = {
        "success": True, "volume": 50
    }
    r = await spotify_volume_handler(ctx, level=50)
    assert r.success
    assert "50" in r.output


@pytest.mark.asyncio
async def test_spotify_volume_failure(ctx):
    ctx._spotify_hub.set_volume.return_value = {
        "success": False, "error": "out of range"
    }
    r = await spotify_volume_handler(ctx, level=200)
    assert not r.success


# ─── spotify_current ───────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spotify_current_success(ctx):
    ctx._spotify_hub.current_track.return_value = {
        "success": True, "name": "Song", "artists": "A",
        "album": "B", "progress_pct": 33, "volume": 80,
        "playing": True, "device": "PC",
    }
    r = await spotify_current_handler(ctx)
    assert r.success
    assert "Song" in r.output


@pytest.mark.asyncio
async def test_spotify_current_nothing_playing(ctx):
    ctx._spotify_hub.current_track.return_value = {
        "success": True, "playing": False,
        "message": "Rien en cours de lecture",
    }
    r = await spotify_current_handler(ctx)
    assert r.success


# ─── spotify_queue ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_spotify_queue_success(ctx):
    ctx._spotify_hub.add_to_queue.return_value = {
        "success": True, "name": "Song", "artists": "A"
    }
    r = await spotify_queue_handler(ctx, query="Song A")
    assert r.success
    assert "Song" in r.output


@pytest.mark.asyncio
async def test_spotify_queue_failure(ctx):
    ctx._spotify_hub.add_to_queue.return_value = {
        "success": False, "error": "not found"
    }
    r = await spotify_queue_handler(ctx, query="Unknown")
    assert not r.success


# ─── HandlerDefs ───────────────────────────────────────────────────────────

def test_handler_defs_count():
    defs = get_spotify_handler_defs()
    assert len(defs) == 8


def test_handler_defs_names():
    defs = get_spotify_handler_defs()
    names = [d.name for d in defs]
    assert len(names) == len(set(names))


def test_handler_defs_expected_names():
    expected = {
        "spotify_api_play", "spotify_pause", "spotify_resume",
        "spotify_next", "spotify_prev", "spotify_volume",
        "spotify_current", "spotify_queue",
    }
    defs = get_spotify_handler_defs()
    actual = {d.name for d in defs}
    assert actual == expected


def test_handler_defs_have_handlers():
    for d in get_spotify_handler_defs():
        assert callable(d.handler), f"{d.name} handler not callable"
