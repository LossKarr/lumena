"""Bloc C-1.b — Quarantaine automatique des pairs.

Couvre :
- seuil d'échecs consécutifs → quarantaine ; succès remet le compteur à zéro ;
- un succès NE lève PAS une quarantaine déjà posée (levée = acte explicite) ;
- gate : un pair en quarantaine refuse une NOUVELLE délégation ;
- 24/7 : la quarantaine ne coupe PAS la boucle d'autonomie (poll des missions
  en cours) — elle n'est consultée qu'à l'amorce d'une nouvelle délégation ;
- endpoints GET liste / POST levée.
"""
from __future__ import annotations

from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.runtime.peer_quarantine as q
from web.routes import peers as peers_module
from web.routes.peers import router as peers_router


AUTH = {"Authorization": "Bearer tok"}


@pytest.fixture(autouse=True)
def _isolate_quarantine(tmp_path, monkeypatch):
    """État quarantaine dans un fichier tmp + seuil par défaut."""
    monkeypatch.setattr(q, "_FILE", tmp_path / "peer_quarantine.json")
    monkeypatch.delenv("LUMENA_PEER_QUARANTINE_THRESHOLD", raising=False)
    q.clear_for_tests()
    yield


# ── compteur + seuil ──────────────────────────────────────────────────────────

class TestThreshold:
    def test_below_threshold_not_quarantined(self):
        for _ in range(4):                       # seuil défaut = 5
            q.record_anomaly("peer-x")
        assert q.is_quarantined("peer-x") is False

    def test_reaches_threshold_quarantines(self):
        for _ in range(5):
            q.record_anomaly("peer-x")
        assert q.is_quarantined("peer-x") is True

    def test_success_resets_counter(self):
        for _ in range(4):
            q.record_anomaly("peer-x")
        q.record_success("peer-x")               # reset
        q.record_anomaly("peer-x")               # 1 seul → pas de quarantaine
        assert q.is_quarantined("peer-x") is False

    def test_custom_threshold_env(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_QUARANTINE_THRESHOLD", "2")
        q.record_anomaly("peer-y")
        assert q.is_quarantined("peer-y") is False
        q.record_anomaly("peer-y")
        assert q.is_quarantined("peer-y") is True


# ── levée ─────────────────────────────────────────────────────────────────────

class TestRelease:
    def test_success_does_not_lift_existing_quarantine(self):
        for _ in range(5):
            q.record_anomaly("peer-x")
        assert q.is_quarantined("peer-x") is True
        q.record_success("peer-x")               # un succès ne lève PAS
        assert q.is_quarantined("peer-x") is True

    def test_explicit_release_lifts(self):
        for _ in range(5):
            q.record_anomaly("peer-x")
        assert q.release("peer-x") is True
        assert q.is_quarantined("peer-x") is False

    def test_list_quarantined(self):
        for _ in range(5):
            q.record_anomaly("peer-a")
        items = q.list_quarantined()
        assert any(i["peer_id"] == "peer-a" for i in items)


# ── 24/7 : la quarantaine ne coupe PAS la boucle d'autonomie ─────────────────

def test_quarantine_does_not_gate_autonomy(monkeypatch):
    """Un pair en quarantaine ne doit PAS désactiver la boucle poll/health :
    les missions en cours continuent à être drainées."""
    from src.runtime.peer_network_autonomy import is_peer_network_autonomy_enabled
    monkeypatch.setenv("LUMENA_PEER_ENABLED", "1")
    for _ in range(5):
        q.record_anomaly("peer-x")
    assert q.is_quarantined("peer-x") is True
    # l'autonomie (drain) reste active malgré la quarantaine d'un pair
    assert is_peer_network_autonomy_enabled() is True


# ── gate : refus d'une NOUVELLE délégation vers un pair en quarantaine ────────

@pytest.mark.asyncio
async def test_handler_refuses_quarantined_peer(tmp_path, monkeypatch):
    import src.reasoning.handlers.peer_tasks as pt
    reg = tmp_path / "peer_registry.json"
    import json as _json
    reg.write_text(_json.dumps({"peer-q": {
        "instance_id": "peer-q", "instance_name": "Lumena Salon",
        "host": "192.168.1.50", "port": 8081, "trust": "trusted",
        "peer_token_outbound": "tok", "allowed_scopes": ["chat", "task.delegate"],
    }}), encoding="utf-8")
    monkeypatch.setattr(pt, "_PEER_REGISTRY_FILE", reg)
    monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")
    # mettre le pair en quarantaine
    for _ in range(5):
        q.record_anomaly("peer-q")

    res = await pt.run_peer_task_sync_handler(MagicMock(), "peer-q", "Coder un script")
    assert res.success is False
    assert "quarantaine" in (res.error or res.output or "").lower()


# ── endpoints ─────────────────────────────────────────────────────────────────

def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(peers_router)
    return app


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
    with TestClient(_make_app(), raise_server_exceptions=True) as c:
        yield c


class TestEndpoints:
    def test_list_and_release(self, authed):
        for _ in range(5):
            q.record_anomaly("peer-z")
        r = authed.get("/api/peer/quarantine", headers=AUTH)
        assert r.status_code == 200
        assert any(i["peer_id"] == "peer-z" for i in r.json()["quarantined"])
        rel = authed.post("/api/peer/quarantine/release/peer-z", headers=AUTH)
        assert rel.status_code == 200 and rel.json()["ok"] is True
        assert q.is_quarantined("peer-z") is False

    def test_requires_admin(self, authed):
        assert authed.get("/api/peer/quarantine").status_code == 401
