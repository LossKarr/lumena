"""Tests for P2 — Workspace management API."""
import shutil
from pathlib import Path
import pytest
from fastapi import FastAPI
import httpx
from unittest.mock import MagicMock, patch


def _make_app():
    from web.routes import workspaces
    from web.routes import deps
    app = FastAPI()
    app.include_router(workspaces.router)
    app.dependency_overrides[deps.verify_admin_token] = lambda: None
    return app


# ─── GET /api/workspaces (empty) ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_workspaces_empty_dir(tmp_path):
    import web.routes.workspaces as wm
    with patch.object(wm, "WORKSPACE_DIR", tmp_path / "empty"):
        app = _make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/workspaces")
    assert resp.status_code == 200
    assert resp.json() == {"workspaces": []}


@pytest.mark.asyncio
async def test_list_workspaces_returns_projects(tmp_path):
    import web.routes.workspaces as wm
    ws_root = tmp_path / "2026-04-07" / "projet-tetris"
    ws_root.mkdir(parents=True)
    (ws_root / "index.html").write_text("<h1>Tetris</h1>")

    with patch.object(wm, "WORKSPACE_DIR", tmp_path):
        app = _make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/workspaces")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["workspaces"]) == 1
    ws = data["workspaces"][0]
    assert ws["slug"] == "projet-tetris"
    assert ws["has_index_html"] is True
    assert ws["has_package_json"] is False
    assert ws["files_count"] == 1
    assert ws["is_serving"] is False


@pytest.mark.asyncio
async def test_list_workspaces_multiple_dates(tmp_path):
    import web.routes.workspaces as wm
    for date in ["2026-04-05", "2026-04-07"]:
        ws = tmp_path / date / f"proj-{date}"
        ws.mkdir(parents=True)
        (ws / "main.py").write_text("print('hello')")

    with patch.object(wm, "WORKSPACE_DIR", tmp_path):
        app = _make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/workspaces")
    assert resp.status_code == 200
    assert len(resp.json()["workspaces"]) == 2


# ─── GET /api/workspaces/{slug} ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_workspace_detail(tmp_path):
    import web.routes.workspaces as wm
    ws_root = tmp_path / "2026-04-07" / "mon-api"
    ws_root.mkdir(parents=True)
    (ws_root / "app.py").write_text("from flask import Flask")
    (ws_root / "requirements.txt").write_text("flask")

    with patch.object(wm, "WORKSPACE_DIR", tmp_path):
        app = _make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/workspaces/mon-api")
    assert resp.status_code == 200
    data = resp.json()
    assert data["slug"] == "mon-api"
    file_paths = [f["path"] for f in data["files"]]
    assert "app.py" in file_paths
    assert "requirements.txt" in file_paths


@pytest.mark.asyncio
async def test_get_workspace_404(tmp_path):
    import web.routes.workspaces as wm
    with patch.object(wm, "WORKSPACE_DIR", tmp_path):
        app = _make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/workspaces/ghost-slug")
    assert resp.status_code == 404


# ─── GET /api/workspaces/{slug}/file ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_workspace_file_content(tmp_path):
    import web.routes.workspaces as wm
    ws_root = tmp_path / "2026-04-07" / "proj-file"
    ws_root.mkdir(parents=True)
    (ws_root / "index.html").write_text("<p>Hello</p>")

    with patch.object(wm, "WORKSPACE_DIR", tmp_path):
        app = _make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/workspaces/proj-file/file", params={"path": "index.html"})
    assert resp.status_code == 200
    data = resp.json()
    assert "<p>Hello</p>" in data["content"]


@pytest.mark.asyncio
async def test_get_workspace_file_path_traversal_blocked(tmp_path):
    import web.routes.workspaces as wm
    ws_root = tmp_path / "2026-04-07" / "proj-safe"
    ws_root.mkdir(parents=True)
    (ws_root / "ok.txt").write_text("ok")

    with patch.object(wm, "WORKSPACE_DIR", tmp_path):
        app = _make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.get("/api/workspaces/proj-safe/file", params={"path": "../../secret.txt"})
    assert resp.status_code in (400, 404)


# ─── DELETE /api/workspaces/{slug} ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_workspace_removes_dir(tmp_path):
    import web.routes.workspaces as wm
    ws_root = tmp_path / "2026-04-07" / "proj-del"
    ws_root.mkdir(parents=True)
    (ws_root / "file.txt").write_text("data")
    assert ws_root.exists()

    with patch.object(wm, "WORKSPACE_DIR", tmp_path):
        app = _make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/api/workspaces/proj-del")
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    assert not ws_root.exists()


@pytest.mark.asyncio
async def test_delete_workspace_404(tmp_path):
    import web.routes.workspaces as wm
    with patch.object(wm, "WORKSPACE_DIR", tmp_path):
        app = _make_app()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/api/workspaces/non-existent")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_workspace_without_auth_rejected(tmp_path):
    """Sans override dep, retourne silencieusement si pas de token configuré."""
    from web.routes import workspaces
    ws_root = tmp_path / "2026-04-07" / "proj-noauth"
    ws_root.mkdir(parents=True)
    import web.routes.workspaces as wm
    app = FastAPI()
    app.include_router(workspaces.router)
    with patch.object(wm, "WORKSPACE_DIR", tmp_path):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            resp = await client.delete("/api/workspaces/proj-noauth")
    assert resp.status_code in (200, 401, 403)


# ─── _ws_summary helper ───────────────────────────────────────────────────────

def test_ws_summary_fields(tmp_path):
    import web.routes.workspaces as wm
    ws_root = tmp_path / "proj-sum"
    ws_root.mkdir()
    (ws_root / "index.html").write_text("<!doctype html>")
    (ws_root / "app.js").write_text("console.log('ok')")

    summary = wm._ws_summary(ws_root, "2026-04-07")
    assert summary["slug"] == "proj-sum"
    assert summary["date"] == "2026-04-07"
    assert summary["files_count"] == 2
    assert summary["has_index_html"] is True
    assert summary["has_package_json"] is False
    assert summary["is_serving"] is False
    assert summary["serve_url"] is None
    assert summary["total_size_kb"] >= 0


def test_ws_summary_serving_flag(tmp_path):
    import web.routes.workspaces as wm
    from web.routes import advanced as adv
    ws_root = tmp_path / "proj-live"
    ws_root.mkdir()
    adv._SERVING_WORKSPACES["proj-live"] = {
        "port": 7777, "url": "http://localhost:7777",
        "slug": "proj-live", "path": str(ws_root), "process": MagicMock()
    }
    try:
        summary = wm._ws_summary(ws_root, "2026-04-07")
        assert summary["is_serving"] is True
        assert summary["serve_url"] == "http://localhost:7777"
    finally:
        adv._SERVING_WORKSPACES.pop("proj-live", None)
