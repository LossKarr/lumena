"""Brique 2 — tests d'intégration des endpoints de niveau de capacité par pair."""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import deps
from web.routes import peers as peers_module
from web.routes.peers import router as peers_router

PEER = "inst-peer-B"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    reg = tmp_path / "peer_registry.json"
    reg.write_text(json.dumps({
        PEER: {"instance_id": PEER, "instance_name": "B", "trust": "trusted",
               "pairing_method": "fleet", "allowed_scopes": ["chat"]},
    }), encoding="utf-8")
    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg)
    app = FastAPI()
    app.include_router(peers_router)
    app.dependency_overrides[deps.verify_admin_token] = lambda: True
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c, reg


def test_default_capability_is_chat(client):
    c, _ = client
    r = c.get(f"/api/peers/{PEER}/capability")
    assert r.status_code == 200
    assert r.json()["level"] == "chat"


def test_grant_mission_then_revoke(client):
    c, reg = client
    r = c.put(f"/api/peers/{PEER}/capability", json={"level": "mission"})
    assert r.status_code == 200 and r.json()["level"] == "mission"
    assert json.loads(reg.read_text(encoding="utf-8"))[PEER]["capability_level"] == "mission"
    # GET reflète
    assert c.get(f"/api/peers/{PEER}/capability").json()["level"] == "mission"
    # retour à chat
    r = c.put(f"/api/peers/{PEER}/capability", json={"level": "chat"})
    assert r.status_code == 200 and r.json()["level"] == "chat"


def test_mission_auto_grants_task_scopes(client):
    c, reg = client
    r = c.put(f"/api/peers/{PEER}/capability", json={"level": "mission"})
    assert r.status_code == 200
    granted = r.json()["auto_granted_scopes"]
    assert "task.delegate" in granted and "artifact.share" in granted
    scopes = json.loads(reg.read_text(encoding="utf-8"))[PEER]["allowed_scopes"]
    assert "task.delegate" in scopes and "artifact.share" in scopes and "chat" in scopes


def test_chat_level_does_not_grant_task(client):
    c, reg = client
    c.put(f"/api/peers/{PEER}/capability", json={"level": "mission"})
    r = c.put(f"/api/peers/{PEER}/capability", json={"level": "chat"})
    assert r.json()["auto_granted_scopes"] == []  # repasser en chat n'ajoute rien


def test_invalid_level_rejected(client):
    c, _ = client
    r = c.put(f"/api/peers/{PEER}/capability", json={"level": "admin"})
    assert r.status_code == 422


def test_unknown_peer_404(client):
    c, _ = client
    assert c.put("/api/peers/nope/capability", json={"level": "mission"}).status_code == 404
    assert c.get("/api/peers/nope/capability").status_code == 404


def test_list_peers_exposes_capability_level(client):
    c, _ = client
    r = c.get("/api/peers")
    assert r.status_code == 200
    peer = next(p for p in r.json()["peers"] if p["instance_id"] == PEER)
    assert peer["capability_level"] == "chat"
    # jamais de token brut
    assert "peer_token_outbound" not in peer and "peer_token_hash" not in peer
