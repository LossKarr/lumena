"""Tests for P1 — Workspace live serve endpoints."""
import asyncio
import socket
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pathlib import Path
import httpx


def _make_app_adv():
    from web.routes import advanced as adv
    from web.routes import deps
    from fastapi import FastAPI
    app = FastAPI()
    app.include_router(adv.router)
    app.dependency_overrides[deps.verify_admin_token] = lambda: None
    return app


# ─── Unit: _find_free_port ────────────────────────────────────────────────────

def test_find_free_port_returns_int():
    from web.routes.advanced import _find_free_port
    port = _find_free_port()
    assert isinstance(port, int)
    assert 1024 <= port <= 65535


def test_find_free_port_is_actually_free():
    from web.routes.advanced import _find_free_port
    port = _find_free_port()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))


# ─── Unit: _serving_workspaces dict ──────────────────────────────────────────

def test_serving_workspaces_starts_as_dict():
    from web.routes.advanced import _SERVING_WORKSPACES
    assert isinstance(_SERVING_WORKSPACES, dict)


def test_serving_workspaces_dict_manipulation():
    from web.routes import advanced
    original = dict(advanced._SERVING_WORKSPACES)
    try:
        advanced._SERVING_WORKSPACES["test-slug"] = {
            "port": 9999, "url": "http://localhost:9999",
            "slug": "test-slug", "path": "/tmp/test",
            "process": MagicMock(),
        }
        assert "test-slug" in advanced._SERVING_WORKSPACES
        advanced._SERVING_WORKSPACES.pop("test-slug")
        assert "test-slug" not in advanced._SERVING_WORKSPACES
    finally:
        advanced._SERVING_WORKSPACES.clear()
        advanced._SERVING_WORKSPACES.update(original)


# ─── Unit: _serve_workspace_dir (mocked subprocess) ──────────────────────────

@pytest.mark.asyncio
async def test_serve_workspace_dir_creates_entry(tmp_path):
    from web.routes import advanced

    mock_proc = MagicMock()
    mock_proc.returncode = None

    with patch("web.routes.advanced._find_free_port", return_value=19876), \
         patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_proc):
        info = await advanced._serve_workspace_dir(str(tmp_path), "test-ws")

    assert info["port"] == 19876
    assert info["url"] == "http://localhost:19876"
    assert info["slug"] == "test-ws"
    assert "test-ws" in advanced._SERVING_WORKSPACES
    advanced._SERVING_WORKSPACES.pop("test-ws", None)


@pytest.mark.asyncio
async def test_serve_workspace_dir_idempotent(tmp_path):
    from web.routes import advanced

    existing = {
        "port": 11111, "url": "http://localhost:11111",
        "slug": "dup", "path": str(tmp_path), "process": MagicMock()
    }
    advanced._SERVING_WORKSPACES["dup"] = existing
    try:
        with patch("asyncio.create_subprocess_exec") as m:
            info = await advanced._serve_workspace_dir(str(tmp_path), "dup")
        m.assert_not_called()
        assert info["port"] == 11111
    finally:
        advanced._SERVING_WORKSPACES.pop("dup", None)


# ─── FastAPI route: POST /api/workspaces/{slug}/serve ────────────────────────

@pytest.mark.asyncio
async def test_serve_workspace_route_404_when_not_found():
    app = _make_app_adv()
    with patch("web.routes.advanced.WORKSPACE_DIR", Path("/nonexistent/__bogus__")):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/workspaces/does-not-exist/serve")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_serve_workspace_route_already_serving():
    from web.routes import advanced as adv

    adv._SERVING_WORKSPACES["already-live"] = {
        "port": 12345, "url": "http://localhost:12345",
        "slug": "already-live", "path": "/tmp/x", "process": MagicMock()
    }
    try:
        app = _make_app_adv()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.post("/api/workspaces/already-live/serve")
        assert resp.status_code == 200
        data = resp.json()
        assert data["port"] == 12345
        assert data["url"] == "http://localhost:12345"
    finally:
        adv._SERVING_WORKSPACES.pop("already-live", None)


# ─── FastAPI route: DELETE /api/workspaces/{slug}/serve ─────────────────────

@pytest.mark.asyncio
async def test_stop_serve_404_when_not_serving():
    app = _make_app_adv()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.delete("/api/workspaces/not-serving/serve")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_stop_serve_kills_process():
    from web.routes import advanced as adv

    mock_proc = AsyncMock()
    mock_proc.terminate = MagicMock()
    mock_proc.wait = AsyncMock()

    adv._SERVING_WORKSPACES["to-kill"] = {
        "port": 55555, "url": "http://localhost:55555",
        "slug": "to-kill", "path": "/tmp/k", "process": mock_proc
    }
    try:
        app = _make_app_adv()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/api/workspaces/to-kill/serve")
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert "to-kill" not in adv._SERVING_WORKSPACES
    finally:
        adv._SERVING_WORKSPACES.pop("to-kill", None)


# ─── FastAPI route: GET /api/workspaces/serving ──────────────────────────────

@pytest.mark.asyncio
async def test_list_serving_workspaces_empty():
    from web.routes import advanced as adv
    original = dict(adv._SERVING_WORKSPACES)
    adv._SERVING_WORKSPACES.clear()
    try:
        app = _make_app_adv()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/workspaces/serving")
        assert resp.status_code == 200
        assert resp.json() == {"serving": []}
    finally:
        adv._SERVING_WORKSPACES.update(original)


@pytest.mark.asyncio
async def test_list_serving_workspaces_has_entries():
    from web.routes import advanced as adv
    adv._SERVING_WORKSPACES["proj-a"] = {
        "port": 8001, "url": "http://localhost:8001",
        "slug": "proj-a", "path": "/tmp/a", "process": MagicMock()
    }
    try:
        app = _make_app_adv()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/workspaces/serving")
        assert resp.status_code == 200
        slugs = [s["slug"] for s in resp.json()["serving"]]
        assert "proj-a" in slugs
    finally:
        adv._SERVING_WORKSPACES.pop("proj-a", None)
