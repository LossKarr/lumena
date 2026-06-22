"""Cran 1 (UI live) — Tests de l'endpoint missions actives.

Couvre :
- GET /api/peer/missions/active : renvoie les missions non terminales
- exclut les statuts terminaux (completed/failed/...)
- sanitizé : aucun token/payload brut ne ressort
- auth admin requise

Aucun email réel — noms de pairs fictifs uniquement.
"""
from __future__ import annotations

from typing import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import peers as peers_module
from web.routes.peers import router as peers_router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(peers_router)
    return app


AUTH = {"Authorization": "Bearer tok"}


@pytest.fixture()
def authed(tmp_path, monkeypatch) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
    monkeypatch.setattr(_paths, "INSTANCE_NAME", "Lumena Principal")
    monkeypatch.setattr(_paths, "INSTANCE_ROLE", "standalone")
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    monkeypatch.setenv("LUMENA_PORT", "8080")
    # Tracker isolé dans tmp
    import src.runtime.peer_mission_tracker as tracker
    monkeypatch.setattr(tracker, "_TRACKER_FILE", tmp_path / "missions.json")
    with TestClient(_make_app(), raise_server_exceptions=True) as c:
        yield c


def _register(task_id, status="queued", peer_name="Lumena Salon"):
    from src.runtime import peer_mission_tracker as tracker
    tracker.register_outbound_mission(
        task_id=task_id, peer_id=f"peer-{task_id}", peer_name=peer_name,
        host="192.168.1.50", port=8081, objective="Rédiger un rapport",
    )
    if status != "queued":
        tracker.update_status(task_id, status, result="x" * 100)


class TestActiveMissions:

    def test_empty_when_no_missions(self, authed):
        r = authed.get("/api/peer/missions/active", headers=AUTH)
        assert r.status_code == 200
        assert r.json() == {"missions": [], "count": 0}

    def test_returns_pending_mission(self, authed):
        _register("ta-1", status="running")
        r = authed.get("/api/peer/missions/active", headers=AUTH)
        assert r.status_code == 200
        d = r.json()
        assert d["count"] == 1
        m = d["missions"][0]
        assert m["task_id"] == "ta-1"
        assert m["status"] == "running"
        assert m["peer_name"] == "Lumena Salon"
        assert m["objective"] == "Rédiger un rapport"

    def test_excludes_terminal_missions(self, authed):
        _register("ta-done", status="completed")
        _register("ta-fail", status="failed")
        _register("ta-live", status="running")
        r = authed.get("/api/peer/missions/active", headers=AUTH)
        ids = {m["task_id"] for m in r.json()["missions"]}
        assert ids == {"ta-live"}

    def test_no_token_or_raw_payload_leaked(self, authed):
        _register("ta-1", status="running")
        r = authed.get("/api/peer/missions/active", headers=AUTH)
        m = r.json()["missions"][0]
        # Champs internes/sensibles absents
        for forbidden in ("result", "web_ack", "notified", "dest_id", "channel", "host", "port"):
            assert forbidden not in m

    def test_requires_admin_token(self, authed):
        r = authed.get("/api/peer/missions/active")
        assert r.status_code == 401
