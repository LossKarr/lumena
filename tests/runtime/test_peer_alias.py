"""Bloc A — Tests de l'alias (nom personnalisé) par pair + révocation token.

Couvre :
- PUT /api/peers/{id}/alias : set, reset (vide), borne 60, peer inconnu, auth, audit
- alias exposé par GET /api/peers (via _sanitize_peer, défaut "")
- POST /api/peer/revoke-token/{id} : trust → unknown, token retiré

Aucun email réel — noms de pairs fictifs uniquement.
"""
from __future__ import annotations

import json
from typing import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from unittest.mock import patch

from web.routes import peers as peers_module
from web.routes.peers import router as peers_router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(peers_router)
    return app


def _write_registry(path, peers: dict) -> None:
    path.write_text(json.dumps(peers, ensure_ascii=False, indent=2), encoding="utf-8")


TRUSTED_PEER = {
    "instance_id": "peer-aaa",
    "instance_name": "Lumena Salon",
    "host": "192.168.1.100",
    "port": 8081,
    "capabilities": ["chat", "browser"],
    "trust": "trusted",
    "pairing_method": "code",
    "paired_at": "2026-05-07T00:00:00+00:00",
    "last_seen": "2026-05-07T00:00:00+00:00",
    "peer_token_hash": "deadbeef",
    "peer_token_outbound": "tok-out",
    "allowed_scopes": ["chat"],
}

AUTH = {"Authorization": "Bearer tok"}


@pytest.fixture()
def authed_with_peer(tmp_path, monkeypatch) -> Generator[tuple[TestClient, "Path"], None, None]:
    reg_file = tmp_path / "peer_registry.json"
    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg_file)
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
    monkeypatch.setattr(_paths, "INSTANCE_NAME", "Lumena Principal")
    monkeypatch.setattr(_paths, "INSTANCE_ROLE", "standalone")
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    monkeypatch.setenv("LUMENA_PORT", "8080")
    _write_registry(reg_file, {"peer-aaa": dict(TRUSTED_PEER)})
    with TestClient(_make_app(), raise_server_exceptions=True) as c:
        yield c, reg_file


# ── PUT /api/peers/{id}/alias ────────────────────────────────────────────────

class TestUpdatePeerAlias:

    def test_set_alias_ok(self, authed_with_peer):
        client, reg_file = authed_with_peer
        r = client.put("/api/peers/peer-aaa/alias", json={"alias": "Lumena-Bureau"}, headers=AUTH)
        assert r.status_code == 200
        assert r.json()["alias"] == "Lumena-Bureau"
        data = json.loads(reg_file.read_text(encoding="utf-8"))
        assert data["peer-aaa"]["alias"] == "Lumena-Bureau"

    def test_alias_trimmed(self, authed_with_peer):
        client, reg_file = authed_with_peer
        r = client.put("/api/peers/peer-aaa/alias", json={"alias": "   Salon   "}, headers=AUTH)
        assert r.status_code == 200
        assert r.json()["alias"] == "Salon"

    def test_alias_capped_at_60(self, authed_with_peer):
        client, _ = authed_with_peer
        r = client.put("/api/peers/peer-aaa/alias", json={"alias": "x" * 200}, headers=AUTH)
        assert r.status_code == 200
        assert len(r.json()["alias"]) == 60

    def test_empty_alias_resets(self, authed_with_peer):
        client, reg_file = authed_with_peer
        client.put("/api/peers/peer-aaa/alias", json={"alias": "Temp"}, headers=AUTH)
        r = client.put("/api/peers/peer-aaa/alias", json={"alias": ""}, headers=AUTH)
        assert r.status_code == 200
        assert r.json()["alias"] == ""
        data = json.loads(reg_file.read_text(encoding="utf-8"))
        assert "alias" not in data["peer-aaa"]  # retiré du registre

    def test_unknown_peer_404(self, authed_with_peer):
        client, _ = authed_with_peer
        r = client.put("/api/peers/unknown-xyz/alias", json={"alias": "X"}, headers=AUTH)
        assert r.status_code == 404

    def test_requires_admin_token(self, authed_with_peer):
        client, _ = authed_with_peer
        r = client.put("/api/peers/peer-aaa/alias", json={"alias": "X"})
        assert r.status_code == 401

    def test_alias_does_not_touch_scopes_or_token(self, authed_with_peer):
        client, reg_file = authed_with_peer
        client.put("/api/peers/peer-aaa/alias", json={"alias": "Renommé"}, headers=AUTH)
        data = json.loads(reg_file.read_text(encoding="utf-8"))["peer-aaa"]
        assert data["allowed_scopes"] == ["chat"]
        assert data["peer_token_hash"] == "deadbeef"
        assert data["trust"] == "trusted"

    def test_audit_written(self, authed_with_peer, tmp_path):
        client, _ = authed_with_peer
        with patch("src.runtime.peer_protocol.PEER_AUDIT_LOG", tmp_path / "audit.jsonl"):
            r = client.put("/api/peers/peer-aaa/alias", json={"alias": "Audité"}, headers=AUTH)
        assert r.status_code == 200
        lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        assert any("alias_updated" in l for l in lines)


# ── alias exposé par GET /api/peers ──────────────────────────────────────────

class TestAliasInListing:

    def test_alias_default_empty(self, authed_with_peer):
        client, _ = authed_with_peer
        r = client.get("/api/peers", headers=AUTH)
        assert r.status_code == 200
        peer = next(p for p in r.json()["peers"] if p["instance_id"] == "peer-aaa")
        assert peer["alias"] == ""

    def test_alias_listed_after_set(self, authed_with_peer):
        client, _ = authed_with_peer
        client.put("/api/peers/peer-aaa/alias", json={"alias": "Mon-Lumena"}, headers=AUTH)
        r = client.get("/api/peers", headers=AUTH)
        peer = next(p for p in r.json()["peers"] if p["instance_id"] == "peer-aaa")
        assert peer["alias"] == "Mon-Lumena"

    def test_no_raw_token_leaked(self, authed_with_peer):
        client, _ = authed_with_peer
        r = client.get("/api/peers", headers=AUTH)
        peer = next(p for p in r.json()["peers"] if p["instance_id"] == "peer-aaa")
        assert "peer_token_hash" not in peer
        assert "peer_token_outbound" not in peer
        assert peer["has_peer_token"] is True


# ── POST /api/peer/revoke-token/{id} ─────────────────────────────────────────

class TestRevokePeerToken:

    def test_revoke_sets_trust_unknown(self, authed_with_peer):
        client, reg_file = authed_with_peer
        r = client.post("/api/peer/revoke-token/peer-aaa", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["trust"] == "unknown"
        data = json.loads(reg_file.read_text(encoding="utf-8"))["peer-aaa"]
        assert data["trust"] == "unknown"
        assert "peer_token_hash" not in data
        assert "peer_token_outbound" not in data

    def test_revoke_unknown_peer_404(self, authed_with_peer):
        client, _ = authed_with_peer
        r = client.post("/api/peer/revoke-token/unknown-xyz", headers=AUTH)
        assert r.status_code == 404

    def test_revoke_requires_admin_token(self, authed_with_peer):
        client, _ = authed_with_peer
        r = client.post("/api/peer/revoke-token/peer-aaa")
        assert r.status_code == 401
