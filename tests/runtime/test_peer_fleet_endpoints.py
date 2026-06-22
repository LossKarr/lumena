"""Tests d'intégration A1 — endpoints d'auto-jumelage de flotte (fleet-pair).

Simule l'initiateur (A) en process et vérifie que le répondeur (B = instance
locale) établit la confiance par preuve HMAC, avec tokens dérivés.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import peers as peers_module
from web.routes.peers import router as peers_router
from src.runtime import peer_fleet as pf

FK = "fleet-secret-integration-XYZ"
B_ID = "test-inst-B-0001"   # instance locale (répondeur)
A_ID = "peer-A-instance"    # initiateur simulé


@pytest.fixture()
def fleet_client(tmp_path, monkeypatch) -> Generator[tuple, None, None]:
    reg_file = tmp_path / "peer_registry.json"
    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg_file)
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", B_ID)
    monkeypatch.setattr(_paths, "INSTANCE_NAME", "Lumena B")
    monkeypatch.setenv("LUMENA_FLEET_KEY", FK)
    # registre pending isolé entre tests
    peers_module._fleet_pending.clear()
    app = FastAPI()
    app.include_router(peers_router)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c, reg_file


def _init(client, nonce_init="n-init-1"):
    return client.post("/api/peer/fleet-pair-init", json={
        "from_instance_id": A_ID, "from_instance_name": "Peer A",
        "from_host": "192.168.1.50", "from_port": 8081,
        "from_capabilities": ["chat"], "nonce_init": nonce_init,
    })


# ── Handshake complet ────────────────────────────────────────────────────────

def test_fleet_handshake_establishes_trust(fleet_client):
    client, reg_file = fleet_client
    nonce_init = "n-init-1"
    r1 = _init(client, nonce_init)
    assert r1.status_code == 200
    d = r1.json()
    assert d["instance_id"] == B_ID
    # B prouve la flotte
    assert pf.verify_proof(d["proof_b"], B_ID, A_ID, nonce_init, d["nonce_resp"], fleet_key=FK)

    proof_a = pf.compute_proof(A_ID, B_ID, nonce_init, d["nonce_resp"], fleet_key=FK)
    r2 = client.post("/api/peer/fleet-pair-confirm", json={
        "from_instance_id": A_ID, "nonce_init": nonce_init,
        "nonce_resp": d["nonce_resp"], "proof_a": proof_a,
    })
    assert r2.status_code == 200 and r2.json()["ok"] is True

    reg = json.loads(reg_file.read_text(encoding="utf-8"))
    assert A_ID in reg
    assert reg[A_ID]["trust"] == "trusted"
    assert reg[A_ID]["pairing_method"] == "fleet"
    assert reg[A_ID]["allowed_scopes"] == ["chat"]
    # tokens dérivés cohérents
    assert reg[A_ID]["peer_token_outbound"] == pf.derive_peer_token(B_ID, A_ID, fleet_key=FK)
    assert reg[A_ID]["peer_token_hash"]  # hash du token entrant de A


# ── Rejets ───────────────────────────────────────────────────────────────────

def test_confirm_wrong_key_refused(fleet_client):
    client, reg_file = fleet_client
    nonce_init = "n-init-2"
    d = _init(client, nonce_init).json()
    # preuve calculée avec une MAUVAISE clé
    bad_proof = pf.compute_proof(A_ID, B_ID, nonce_init, d["nonce_resp"], fleet_key="MAUVAISE-CLE")
    r2 = client.post("/api/peer/fleet-pair-confirm", json={
        "from_instance_id": A_ID, "nonce_init": nonce_init,
        "nonce_resp": d["nonce_resp"], "proof_a": bad_proof,
    })
    assert r2.status_code == 403
    assert not reg_file.exists() or A_ID not in json.loads(reg_file.read_text(encoding="utf-8"))


def test_confirm_without_init_refused(fleet_client):
    client, _ = fleet_client
    r = client.post("/api/peer/fleet-pair-confirm", json={
        "from_instance_id": A_ID, "nonce_init": "x", "nonce_resp": "y", "proof_a": "z",
    })
    assert r.status_code == 403


def test_init_disabled_without_fleet_key(tmp_path, monkeypatch):
    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "r.json")
    monkeypatch.delenv("LUMENA_FLEET_KEY", raising=False)
    app = FastAPI(); app.include_router(peers_router)
    with TestClient(app, raise_server_exceptions=True) as c:
        r = _init(c)
    assert r.status_code == 403


def test_init_rejects_non_rfc1918_host(fleet_client):
    client, _ = fleet_client
    r = client.post("/api/peer/fleet-pair-init", json={
        "from_instance_id": A_ID, "from_host": "8.8.8.8", "from_port": 80,
        "nonce_init": "n",
    })
    assert r.status_code == 422  # anti-SSRF
