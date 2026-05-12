"""Tests Phase 4 — API locale d'instance (hello, capabilities, health, peers)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Generator
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import peers as peers_module
from web.routes.peers import router as peers_router, _PEER_REGISTRY_FILE


# ── App de test isolée ────────────────────────────────────────────────────────

def _make_test_app() -> FastAPI:
    """Mini FastAPI avec uniquement le router peers — sans lifespan Lumena."""
    from fastapi import FastAPI
    from web.routes import deps

    app = FastAPI()

    # Injecter verify_admin_token patchable
    app.include_router(peers_router)
    return app


@pytest.fixture()
def client(tmp_path, monkeypatch) -> Generator[TestClient, None, None]:
    """Client de test avec registre pair isolé dans tmp_path."""
    # Isoler le fichier registre pair
    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")
    # Patcher INSTANCE_ID, INSTANCE_NAME, INSTANCE_ROLE pour contrôle
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", "test-inst-0001")
    monkeypatch.setattr(_paths, "INSTANCE_NAME", "Lumena Test")
    monkeypatch.setattr(_paths, "INSTANCE_ROLE", "standalone")
    app = _make_test_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def authed_client(tmp_path, monkeypatch) -> Generator[TestClient, None, None]:
    """Client avec token admin configuré."""
    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", "test-inst-0001")
    monkeypatch.setattr(_paths, "INSTANCE_NAME", "Lumena Test")
    monkeypatch.setattr(_paths, "INSTANCE_ROLE", "standalone")
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "secret-test-token")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app = _make_test_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ── GET /api/instance/hello ───────────────────────────────────────────────────

def test_hello_returns_200_without_auth(client):
    r = client.get("/api/instance/hello")
    assert r.status_code == 200


def test_hello_contains_required_fields(client):
    data = client.get("/api/instance/hello").json()
    for field in ("instance_id", "instance_name", "version", "role", "capabilities", "requires_pairing"):
        assert field in data, f"Champ manquant: {field}"


def test_hello_instance_id_matches(client):
    data = client.get("/api/instance/hello").json()
    assert data["instance_id"] == "test-inst-0001"


def test_hello_role_is_valid(client):
    data = client.get("/api/instance/hello").json()
    assert data["role"] in {"primary", "worker", "standalone"}


def test_hello_capabilities_is_list(client):
    data = client.get("/api/instance/hello").json()
    assert isinstance(data["capabilities"], list)
    assert "chat" in data["capabilities"]


def test_hello_no_secrets_leaked(client, monkeypatch):
    """hello ne doit exposer aucune clé sensible."""
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "super-secret-token")
    data = client.get("/api/instance/hello").json()
    payload_str = json.dumps(data)
    assert "super-secret-token" not in payload_str
    assert "LUMENA_ADMIN_TOKEN" not in payload_str


def test_hello_requires_pairing_true_when_token_set(client, monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
    data = client.get("/api/instance/hello").json()
    assert data["requires_pairing"] is True


def test_hello_requires_pairing_false_when_no_token(client, monkeypatch):
    monkeypatch.delenv("LUMENA_ADMIN_TOKEN", raising=False)
    data = client.get("/api/instance/hello").json()
    assert data["requires_pairing"] is False


# ── GET /api/instance/capabilities ───────────────────────────────────────────

def test_capabilities_returns_200(client):
    r = client.get("/api/instance/capabilities")
    assert r.status_code == 200


def test_capabilities_has_chat(client):
    data = client.get("/api/instance/capabilities").json()
    assert "chat" in data["capabilities"]


def test_capabilities_is_list(client):
    data = client.get("/api/instance/capabilities").json()
    assert isinstance(data["capabilities"], list)


# ── _compute_capabilities — documents dynamique ───────────────────────────────

def test_documents_absent_when_received_docs_dir_missing(tmp_path, monkeypatch):
    """documents ne doit pas apparaître si RECEIVED_DOCS_DIR n'existe pas."""
    import src.utils.paths as _paths
    missing = tmp_path / "received_documents_nonexistent"
    monkeypatch.setattr(_paths, "RECEIVED_DOCS_DIR", missing)
    from web.routes.peers import _compute_capabilities
    caps = _compute_capabilities()
    assert "documents" not in caps


def test_documents_present_when_received_docs_dir_exists(tmp_path, monkeypatch):
    """documents apparaît seulement quand RECEIVED_DOCS_DIR existe réellement."""
    import src.utils.paths as _paths
    existing = tmp_path / "received_documents"
    existing.mkdir()
    monkeypatch.setattr(_paths, "RECEIVED_DOCS_DIR", existing)
    from web.routes.peers import _compute_capabilities
    caps = _compute_capabilities()
    assert "documents" in caps


def test_documents_not_always_true(tmp_path, monkeypatch):
    """Vérification directe : or True ne peut pas bypasser le check du dossier."""
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "RECEIVED_DOCS_DIR", tmp_path / "definitely_absent")
    from web.routes.peers import _compute_capabilities
    caps = _compute_capabilities()
    # Si or True était encore présent, documents serait toujours dans caps
    assert "documents" not in caps


# ── GET /api/instance/health ─────────────────────────────────────────────────

def test_health_returns_200(client):
    r = client.get("/api/instance/health")
    assert r.status_code == 200


def test_health_ok_field(client):
    data = client.get("/api/instance/health").json()
    assert data["ok"] is True


def test_health_has_instance_id(client):
    data = client.get("/api/instance/health").json()
    assert data["instance_id"] == "test-inst-0001"


def test_health_has_timestamp(client):
    data = client.get("/api/instance/health").json()
    assert "timestamp" in data


# ── GET /api/peers — protection auth ─────────────────────────────────────────

def test_peers_without_auth_returns_401_or_403(authed_client):
    """Sans Authorization header, les pairs sont refusés."""
    r = authed_client.get("/api/peers")
    assert r.status_code in {401, 403}


def test_peers_with_bad_token_returns_403(authed_client):
    r = authed_client.get("/api/peers", headers={"Authorization": "Bearer wrong-token"})
    assert r.status_code == 403


def test_peers_with_valid_token_returns_200(authed_client):
    r = authed_client.get("/api/peers", headers={"Authorization": "Bearer secret-test-token"})
    assert r.status_code == 200


def test_peers_empty_registry(authed_client):
    data = authed_client.get("/api/peers", headers={"Authorization": "Bearer secret-test-token"}).json()
    assert data["count"] == 0
    assert data["peers"] == []


# ── POST /api/peers/pair ──────────────────────────────────────────────────────

_PEER_PAYLOAD = {
    "instance_id": "peer-abc-123",
    "instance_name": "Lumena Bureau",
    "host": "192.168.1.10",
    "port": 8081,
    "version": "1.0.28",
    "role": "primary",
    "capabilities": ["chat", "browser"],
}


def test_pair_without_auth_rejected(authed_client):
    r = authed_client.post("/api/peers/pair", json=_PEER_PAYLOAD)
    assert r.status_code in {401, 403}


def test_pair_with_auth_adds_peer(authed_client):
    r = authed_client.post(
        "/api/peers/pair",
        json=_PEER_PAYLOAD,
        headers={"Authorization": "Bearer secret-test-token"},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["instance_id"] == "peer-abc-123"
    assert data["trust"] == "trusted"


def test_pair_peer_appears_in_list(authed_client):
    authed_client.post(
        "/api/peers/pair",
        json=_PEER_PAYLOAD,
        headers={"Authorization": "Bearer secret-test-token"},
    )
    data = authed_client.get("/api/peers", headers={"Authorization": "Bearer secret-test-token"}).json()
    assert data["count"] == 1
    assert data["peers"][0]["instance_id"] == "peer-abc-123"
    assert data["peers"][0]["trust"] == "trusted"


def test_pair_idempotent(authed_client):
    """Appeler pair deux fois ne crée pas deux entrées."""
    headers = {"Authorization": "Bearer secret-test-token"}
    authed_client.post("/api/peers/pair", json=_PEER_PAYLOAD, headers=headers)
    authed_client.post("/api/peers/pair", json=_PEER_PAYLOAD, headers=headers)
    data = authed_client.get("/api/peers", headers=headers).json()
    assert data["count"] == 1


# ── POST /api/peers/block ─────────────────────────────────────────────────────

def test_block_without_auth_rejected(authed_client):
    r = authed_client.post("/api/peers/block", json={"instance_id": "peer-abc-123"})
    assert r.status_code in {401, 403}


def test_block_unknown_peer_returns_404(authed_client):
    r = authed_client.post(
        "/api/peers/block",
        json={"instance_id": "does-not-exist"},
        headers={"Authorization": "Bearer secret-test-token"},
    )
    assert r.status_code == 404


def test_block_known_peer_sets_blocked(authed_client):
    headers = {"Authorization": "Bearer secret-test-token"}
    authed_client.post("/api/peers/pair", json=_PEER_PAYLOAD, headers=headers)
    r = authed_client.post("/api/peers/block", json={"instance_id": "peer-abc-123"}, headers=headers)
    assert r.status_code == 200
    assert r.json()["trust"] == "blocked"


def test_block_peer_trust_persisted(authed_client):
    headers = {"Authorization": "Bearer secret-test-token"}
    authed_client.post("/api/peers/pair", json=_PEER_PAYLOAD, headers=headers)
    authed_client.post("/api/peers/block", json={"instance_id": "peer-abc-123"}, headers=headers)
    data = authed_client.get("/api/peers", headers=headers).json()
    peer = next(p for p in data["peers"] if p["instance_id"] == "peer-abc-123")
    assert peer["trust"] == "blocked"


def test_pair_does_not_unblock_blocked_peer(authed_client):
    """Appeler pair sur un pair bloqué ne change pas son statut."""
    headers = {"Authorization": "Bearer secret-test-token"}
    authed_client.post("/api/peers/pair", json=_PEER_PAYLOAD, headers=headers)
    authed_client.post("/api/peers/block", json={"instance_id": "peer-abc-123"}, headers=headers)
    # Re-pair ne doit pas dé-bloquer
    authed_client.post("/api/peers/pair", json=_PEER_PAYLOAD, headers=headers)
    data = authed_client.get("/api/peers", headers=headers).json()
    peer = next(p for p in data["peers"] if p["instance_id"] == "peer-abc-123")
    assert peer["trust"] == "blocked"
