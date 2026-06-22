"""A2 — Tests d'intégration : exigence de signature côté entrant (verify_peer_token).

On monte une route sonde `/probe` qui dépend de `verify_peer_token` afin de
tester précisément la couche signature (sans exécuter l'agent du /delegate).
"""
from __future__ import annotations

import json
from typing import Generator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from web.routes import peers as peers_module
from web.routes.peers import verify_peer_token
from src.runtime import peer_fleet as pf
from src.runtime import peer_tokens as pt
from src.runtime.peer_signing import (
    build_signed_request, canonical_payload, sign_envelope, now_ts,
    generate_signature_nonce, reset_nonce_cache,
    SIG_HEADER, TS_HEADER, NONCE_HEADER,
)

FK = "fleet-secret-sign-XYZ"
B_ID = "inst-B-local"     # nous (récepteur)
A_ID = "inst-A-fleet"     # pair de flotte (émetteur signé)
C_ID = "inst-C-code"      # pair jumelé par code (Bearer seul)

_PAYLOAD = {"task_id": "t1", "from_instance_id": A_ID, "scope": "chat", "prompt": "salut"}


@pytest.fixture()
def probe_client(tmp_path, monkeypatch) -> Generator[TestClient, None, None]:
    reg_file = tmp_path / "peer_registry.json"
    a_token = pf.derive_peer_token(A_ID, B_ID, fleet_key=FK)   # A→B (présenté par A)
    c_token = "code-raw-token-c"
    reg = {
        A_ID: {
            "instance_id": A_ID, "instance_name": "A", "host": "192.168.1.50",
            "port": 8081, "trust": "trusted", "pairing_method": "fleet",
            "peer_token_hash": pt.hash_peer_token(a_token),
            "allowed_scopes": ["chat"],
        },
        C_ID: {
            "instance_id": C_ID, "instance_name": "C", "host": "192.168.1.60",
            "port": 8082, "trust": "trusted", "pairing_method": "code",
            "peer_token_hash": pt.hash_peer_token(c_token),
            "allowed_scopes": ["chat"],
        },
    }
    reg_file.write_text(json.dumps(reg), encoding="utf-8")
    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg_file)
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", B_ID)
    monkeypatch.setenv("LUMENA_FLEET_KEY", FK)
    monkeypatch.setenv("LUMENA_PEER_SIGNING", "1")
    reset_nonce_cache()

    app = FastAPI()

    @app.post("/probe")
    async def _probe(peer: dict = Depends(verify_peer_token)):
        return {"peer": peer["instance_id"]}

    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    reset_nonce_cache()


def _signed(payload=_PAYLOAD):
    token = pf.derive_peer_token(A_ID, B_ID, fleet_key=FK)
    content, headers = build_signed_request(
        payload, from_id=A_ID, to_id=B_ID, peer_token=token,
        pairing_method="fleet", fleet_key=FK,
    )
    return content, headers


# ── Chemin nominal ───────────────────────────────────────────────────────────

def test_fleet_peer_valid_signature_accepted(probe_client):
    content, headers = _signed()
    r = probe_client.post("/probe", content=content, headers=headers)
    assert r.status_code == 200
    assert r.json()["peer"] == A_ID


# ── Rejets de signature ──────────────────────────────────────────────────────

def test_fleet_peer_missing_signature_rejected(probe_client):
    # Bearer valide mais AUCUN en-tête de signature → refus (pair fleet).
    token = pf.derive_peer_token(A_ID, B_ID, fleet_key=FK)
    r = probe_client.post(
        "/probe", content=canonical_payload(_PAYLOAD).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    assert r.status_code == 401
    assert "ignature" in r.json()["detail"]


def test_fleet_peer_tampered_body_rejected(probe_client):
    content, headers = _signed()
    # On falsifie le corps après signature.
    tampered = canonical_payload({**_PAYLOAD, "prompt": "MALVEILLANT"}).encode()
    r = probe_client.post("/probe", content=tampered, headers=headers)
    assert r.status_code == 401
    assert "invalide" in r.json()["detail"].lower()


def test_fleet_peer_replay_rejected(probe_client):
    content, headers = _signed()
    r1 = probe_client.post("/probe", content=content, headers=headers)
    assert r1.status_code == 200
    # Rejeu EXACT (même nonce) → refus.
    r2 = probe_client.post("/probe", content=content, headers=headers)
    assert r2.status_code == 401
    assert "rejeu" in r2.json()["detail"].lower()


def test_fleet_peer_stale_timestamp_rejected(probe_client):
    token = pf.derive_peer_token(A_ID, B_ID, fleet_key=FK)
    canonical = canonical_payload(_PAYLOAD)
    old_ts = str(int(now_ts()) - 10_000)
    nonce = generate_signature_nonce()
    sig = sign_envelope(canonical, from_id=A_ID, to_id=B_ID, ts=old_ts, nonce=nonce, fleet_key=FK)
    headers = {
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        SIG_HEADER: sig, TS_HEADER: old_ts, NONCE_HEADER: nonce,
    }
    r = probe_client.post("/probe", content=canonical.encode(), headers=headers)
    assert r.status_code == 401


def test_signature_with_wrong_key_rejected(probe_client):
    token = pf.derive_peer_token(A_ID, B_ID, fleet_key=FK)  # Bearer correct
    canonical = canonical_payload(_PAYLOAD)
    ts, nonce = now_ts(), generate_signature_nonce()
    # Signature calculée avec une MAUVAISE clé de flotte.
    bad_sig = sign_envelope(canonical, from_id=A_ID, to_id=B_ID, ts=ts, nonce=nonce, fleet_key="MAUVAISE")
    headers = {
        "Authorization": f"Bearer {token}", "Content-Type": "application/json",
        SIG_HEADER: bad_sig, TS_HEADER: ts, NONCE_HEADER: nonce,
    }
    r = probe_client.post("/probe", content=canonical.encode(), headers=headers)
    assert r.status_code == 401


# ── Compatibilité ascendante ─────────────────────────────────────────────────

def test_code_paired_peer_no_signature_required(probe_client):
    # Pair jumelé par code (pas de clé flotte associée) → Bearer seul suffit.
    r = probe_client.post(
        "/probe", content=canonical_payload({"x": 1}).encode(),
        headers={"Authorization": "Bearer code-raw-token-c", "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json()["peer"] == C_ID


def test_signing_disabled_allows_unsigned_fleet_peer(probe_client, monkeypatch):
    # Signing désactivé globalement → un pair fleet sans signature passe (Bearer seul).
    monkeypatch.setenv("LUMENA_PEER_SIGNING", "0")
    token = pf.derive_peer_token(A_ID, B_ID, fleet_key=FK)
    r = probe_client.post(
        "/probe", content=canonical_payload(_PAYLOAD).encode(),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    assert r.status_code == 200
    assert r.json()["peer"] == A_ID


def test_bad_bearer_still_rejected(probe_client):
    content, headers = _signed()
    headers["Authorization"] = "Bearer totalement-faux"
    r = probe_client.post("/probe", content=content, headers=headers)
    assert r.status_code == 401
