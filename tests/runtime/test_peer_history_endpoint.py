"""Refonte UI — test d'intégration de GET /api/peer/history (read-only, admin)."""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import deps
from web.routes import peers as peers_module
from web.routes.peers import router as peers_router

OWN = "inst-self"
PEER = "inst-peer-B"


@pytest.fixture()
def client(monkeypatch):
    # Auth admin neutralisée pour le test (on teste l'agrégation, pas l'auth).
    app = FastAPI()
    app.include_router(peers_router)
    app.dependency_overrides[deps.verify_admin_token] = lambda: True

    import src.runtime.peer_protocol as pp
    monkeypatch.setattr(pp, "read_audit_log", lambda limit=200: [
        {"ts": "2026-06-17T10:00:00Z", "event": "delegate_accepted", "from_instance_id": PEER,
         "task_id": "t1", "scope": "chat", "status": "running", "detail": ""},
        {"ts": "2026-06-17T10:00:05Z", "event": "delegate_completed", "from_instance_id": PEER,
         "task_id": "t1", "scope": "chat", "status": "completed", "detail": "duration_ms=120"},
        {"ts": "2026-06-17T10:01:00Z", "event": "fleet_pair_completed", "from_instance_id": PEER,
         "task_id": "", "scope": "", "status": "completed", "detail": "ignored"},
    ])
    monkeypatch.setattr(peers_module, "_read_task_events", lambda task_id=None, limit=500: [])
    monkeypatch.setattr(peers_module, "_load_peers", lambda: {PEER: {"instance_name": "Lumena-B"}})
    import src.runtime.shared_knowledge as sk
    monkeypatch.setattr(sk, "load_shared_knowledge", lambda *a, **k: {})
    import src.runtime.peer_mission_tracker as mt
    monkeypatch.setattr(mt, "list_all_missions", lambda: [])
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", OWN)

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def test_history_shape_and_aggregation(client):
    r = client.get("/api/peer/history")
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"exchanges", "count", "stats"}
    # Le jumelage (fleet_pair_completed) est exclu ; il reste 1 fil de délégation.
    assert data["count"] == 1
    ex = data["exchanges"][0]
    assert ex["id"] == "task:t1"
    assert ex["type"] == "delegation"
    assert ex["peer_name"] == "Lumena-B"
    assert ex["status"] == "completed"
    assert data["stats"]["completed"] == 1


def test_history_is_admin_protected():
    # Sans override d'auth → l'endpoint exige le token admin.
    app = FastAPI()
    app.include_router(peers_router)
    with TestClient(app, raise_server_exceptions=True) as c:
        r = c.get("/api/peer/history")
    assert r.status_code in (401, 403)
