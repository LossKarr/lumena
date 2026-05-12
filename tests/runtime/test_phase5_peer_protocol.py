"""Tests Phase 5/6 — Découverte LAN + protocole délégation inter-instances."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Generator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import peers as peers_module
from web.routes.peers import router as peers_router


# ── App de test ───────────────────────────────────────────────────────────────

def _make_app(monkeypatch, tmp_path) -> FastAPI:
    import src.utils.paths as _paths
    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")
    monkeypatch.setattr(_paths, "INSTANCE_ID", "local-instance-001")
    monkeypatch.setattr(_paths, "INSTANCE_NAME", "Lumena Local")
    monkeypatch.setattr(_paths, "INSTANCE_ROLE", "standalone")
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app = FastAPI()
    app.include_router(peers_router)
    return app


@pytest.fixture()
def client(tmp_path, monkeypatch) -> Generator[TestClient, None, None]:
    app = _make_app(monkeypatch, tmp_path)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


AUTH = {"Authorization": "Bearer admin-secret"}

# Phase 8.5 — peer token pour les appels entrants sur /api/peer/delegate
_PEER_TOKEN_RAW = "test-peer-token-phase5-abcdef0123456789"
PEER_AUTH = {"Authorization": f"Bearer {_PEER_TOKEN_RAW}"}


def _peer_token_hash() -> str:
    from src.runtime.peer_tokens import hash_peer_token
    return hash_peer_token(_PEER_TOKEN_RAW)


# ── Helpers pour peupler le registre ─────────────────────────────────────────

def _add_peer(client, instance_id: str, trust_after_block: bool = False) -> None:
    client.post("/api/peers/pair", json={
        "instance_id": instance_id,
        "instance_name": f"Peer {instance_id}",
        "host": "192.168.1.10",
        "port": 8081,
        "version": "1.0.0",
        "role": "standalone",
        "capabilities": ["chat"],
    }, headers=AUTH)
    if trust_after_block:
        client.post("/api/peers/block", json={"instance_id": instance_id}, headers=AUTH)


def _add_peer_with_token(reg_file: Path, instance_id: str, trust: str = "trusted") -> None:
    """Injecte un pair avec peer_token_hash dans le registre (pour tester /api/peer/delegate)."""
    try:
        import json as _json
        data = _json.loads(reg_file.read_text(encoding="utf-8")) if reg_file.exists() else {}
    except Exception:
        data = {}
    data[instance_id] = {
        "instance_id": instance_id,
        "instance_name": f"Peer {instance_id}",
        "host": "192.168.1.10",
        "port": 8081,
        "trust": trust,
        "peer_token_hash": _peer_token_hash(),
        "allowed_scopes": ["chat"],
    }
    reg_file.write_text(
        __import__("json").dumps(data, ensure_ascii=False),
        encoding="utf-8",
    )


# ── peer_auth — tests unitaires ───────────────────────────────────────────────

def test_get_peer_trust_unknown_returns_unknown(tmp_path, monkeypatch):
    import src.runtime.peer_auth as _auth
    monkeypatch.setattr(_auth, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")
    assert _auth.get_peer_trust("nonexistent") == "unknown"


def test_get_peer_trust_trusted(tmp_path, monkeypatch):
    import src.runtime.peer_auth as _auth
    reg = tmp_path / "peer_registry.json"
    reg.write_text(json.dumps({"peer-x": {"trust": "trusted"}}), encoding="utf-8")
    monkeypatch.setattr(_auth, "_PEER_REGISTRY_FILE", reg)
    assert _auth.get_peer_trust("peer-x") == "trusted"


def test_get_peer_trust_blocked(tmp_path, monkeypatch):
    import src.runtime.peer_auth as _auth
    reg = tmp_path / "peer_registry.json"
    reg.write_text(json.dumps({"peer-y": {"trust": "blocked"}}), encoding="utf-8")
    monkeypatch.setattr(_auth, "_PEER_REGISTRY_FILE", reg)
    assert _auth.get_peer_trust("peer-y") == "blocked"


def test_require_trusted_peer_raises_for_unknown(tmp_path, monkeypatch):
    import src.runtime.peer_auth as _auth
    monkeypatch.setattr(_auth, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")
    with pytest.raises(PermissionError, match="n'est pas jumelée"):
        _auth.require_trusted_peer("ghost-instance")


def test_require_trusted_peer_raises_for_blocked(tmp_path, monkeypatch):
    import src.runtime.peer_auth as _auth
    reg = tmp_path / "peer_registry.json"
    reg.write_text(json.dumps({"peer-blocked": {"trust": "blocked"}}), encoding="utf-8")
    monkeypatch.setattr(_auth, "_PEER_REGISTRY_FILE", reg)
    with pytest.raises(PermissionError, match="bloquée"):
        _auth.require_trusted_peer("peer-blocked")


def test_require_trusted_peer_passes_for_trusted(tmp_path, monkeypatch):
    import src.runtime.peer_auth as _auth
    reg = tmp_path / "peer_registry.json"
    reg.write_text(json.dumps({"peer-ok": {"trust": "trusted"}}), encoding="utf-8")
    monkeypatch.setattr(_auth, "_PEER_REGISTRY_FILE", reg)
    _auth.require_trusted_peer("peer-ok")  # ne doit pas lever


def test_validate_scope_chat_ok():
    from src.runtime.peer_auth import validate_scope
    validate_scope("chat")  # ne doit pas lever


def test_validate_scope_unknown_raises():
    from src.runtime.peer_auth import validate_scope
    with pytest.raises(ValueError, match="non autorisé"):
        validate_scope("browser")


def test_validate_scope_files_raises():
    from src.runtime.peer_auth import validate_scope
    with pytest.raises(ValueError):
        validate_scope("files_write")


# ── peer_protocol — audit log ─────────────────────────────────────────────────

def test_write_audit_log_creates_file(tmp_path, monkeypatch):
    import src.runtime.peer_protocol as _proto
    audit_file = tmp_path / "peer_audit.jsonl"
    monkeypatch.setattr(_proto, "PEER_AUDIT_LOG", audit_file)
    _proto.write_audit_log(
        event="delegate_accepted",
        from_instance_id="peer-abc",
        task_id="task-001",
        scope="chat",
        status="running",
    )
    assert audit_file.exists()
    line = json.loads(audit_file.read_text().strip())
    assert line["event"] == "delegate_accepted"
    assert line["from_instance_id"] == "peer-abc"


def test_write_audit_log_appends(tmp_path, monkeypatch):
    import src.runtime.peer_protocol as _proto
    audit_file = tmp_path / "peer_audit.jsonl"
    monkeypatch.setattr(_proto, "PEER_AUDIT_LOG", audit_file)
    _proto.write_audit_log(event="e1", from_instance_id="p1", task_id="t1", scope="chat", status="ok")
    _proto.write_audit_log(event="e2", from_instance_id="p2", task_id="t2", scope="chat", status="ok")
    lines = audit_file.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "e1"
    assert json.loads(lines[1])["event"] == "e2"


def test_read_audit_log_empty(tmp_path, monkeypatch):
    import src.runtime.peer_protocol as _proto
    monkeypatch.setattr(_proto, "PEER_AUDIT_LOG", tmp_path / "absent.jsonl")
    assert _proto.read_audit_log() == []


def test_read_audit_log_returns_entries(tmp_path, monkeypatch):
    import src.runtime.peer_protocol as _proto
    audit_file = tmp_path / "peer_audit.jsonl"
    monkeypatch.setattr(_proto, "PEER_AUDIT_LOG", audit_file)
    _proto.write_audit_log(event="ev", from_instance_id="p", task_id="t", scope="chat", status="ok")
    entries = _proto.read_audit_log()
    assert len(entries) == 1
    assert entries[0]["event"] == "ev"


# ── peer_discovery — feature flag ────────────────────────────────────────────

def test_scan_disabled_by_default():
    """Sans LUMENA_PEER_DISCOVERY=1, scan retourne une liste vide."""
    from src.runtime import peer_discovery as _disc
    import src.runtime.peer_discovery as _disc_mod
    # Le flag est lu à l'import — on le teste via la valeur exportée
    if _disc_mod.PEER_DISCOVERY_ENABLED:
        pytest.skip("LUMENA_PEER_DISCOVERY=1 défini dans l'environnement")
    result = asyncio.run(_disc.scan_lan_for_peers())
    assert result == []


def test_scan_returns_empty_when_disabled(monkeypatch):
    import src.runtime.peer_discovery as _disc
    monkeypatch.setattr(_disc, "PEER_DISCOVERY_ENABLED", False)
    result = asyncio.run(_disc.scan_lan_for_peers())
    assert result == []


def test_scan_rejects_public_network_directly(monkeypatch):
    """scan_lan_for_peers() refuse un réseau public même sans passer par la route."""
    import src.runtime.peer_discovery as _disc
    monkeypatch.setattr(_disc, "PEER_DISCOVERY_ENABLED", True)
    result = asyncio.run(_disc.scan_lan_for_peers(network="8.8.8.0/24"))
    assert result == []


def test_scan_accepts_private_network_directly(monkeypatch):
    """scan_lan_for_peers() accepte un réseau privé (valide mais aucun hôte ne répond)."""
    import src.runtime.peer_discovery as _disc
    monkeypatch.setattr(_disc, "PEER_DISCOVERY_ENABLED", True)
    # Le scan ne trouvera rien (timeout très court), mais ne doit pas retourner [] pour
    # raison de sécurité — uniquement [] parce qu'aucun hôte ne répond.
    # On vérifie juste que la fonction ne lève pas d'exception.
    result = asyncio.run(_disc.scan_lan_for_peers(
        network="127.0.0.0/30",   # loopback — 2 hosts max, timeout ultra-court
        ports=[19998, 19999],
        timeout=0.1,
    ))
    assert isinstance(result, list)


def test_probe_single_returns_none_on_connection_error(monkeypatch):
    """probe_single_peer retourne None si le host est inaccessible."""
    import src.runtime.peer_discovery as _disc
    monkeypatch.setattr(_disc, "PEER_DISCOVERY_ENABLED", True)
    result = asyncio.run(_disc.probe_single_peer("127.0.0.1", 19999, timeout=0.2))
    assert result is None


# ── Route POST /api/peer/discover — feature flag ──────────────────────────────

def test_discover_route_disabled_returns_403(client, monkeypatch):
    import src.runtime.peer_discovery as _disc
    monkeypatch.setattr(_disc, "PEER_DISCOVERY_ENABLED", False)
    r = client.post("/api/peer/discover", json={}, headers=AUTH)
    assert r.status_code == 403
    assert "désactivée" in r.json()["detail"]


def test_discover_route_without_auth_rejected(client, monkeypatch):
    import src.runtime.peer_discovery as _disc
    monkeypatch.setattr(_disc, "PEER_DISCOVERY_ENABLED", True)
    r = client.post("/api/peer/discover", json={})
    assert r.status_code in {401, 403}


def test_discover_route_enabled_calls_scan(client, monkeypatch):
    """Avec PEER_DISCOVERY=1 et scan mocké, la route retourne les pairs trouvés."""
    import src.runtime.peer_discovery as _disc
    monkeypatch.setattr(_disc, "PEER_DISCOVERY_ENABLED", True)

    async def _fake_scan(**kwargs):
        return [{
            "instance_id": "peer-found-123",
            "instance_name": "Remote Lumena",
            "host": "192.168.1.42",
            "port": 8080,
            "version": "1.0.0",
            "role": "standalone",
            "capabilities": ["chat"],
            "requires_pairing": True,
            "trust": "unknown",
        }]

    monkeypatch.setattr(_disc, "scan_lan_for_peers", _fake_scan)
    r = client.post("/api/peer/discover", json={}, headers=AUTH)
    assert r.status_code == 200
    data = r.json()
    assert data["discovered"] == 1
    assert data["peers"][0]["instance_id"] == "peer-found-123"


def test_discover_adds_found_peers_as_unknown(client, monkeypatch):
    """Les pairs découverts sont ajoutés au registre avec trust='unknown'."""
    import src.runtime.peer_discovery as _disc
    monkeypatch.setattr(_disc, "PEER_DISCOVERY_ENABLED", True)

    async def _fake_scan(**kwargs):
        return [{"instance_id": "disc-peer", "instance_name": "X", "host": "10.0.0.1",
                 "port": 8080, "version": "", "role": "standalone",
                 "capabilities": [], "requires_pairing": True, "trust": "unknown"}]

    monkeypatch.setattr(_disc, "scan_lan_for_peers", _fake_scan)
    client.post("/api/peer/discover", json={}, headers=AUTH)
    peers = client.get("/api/peers", headers=AUTH).json()["peers"]
    found = next((p for p in peers if p["instance_id"] == "disc-peer"), None)
    assert found is not None
    assert found["trust"] == "unknown"


# ── Route POST /api/peer/delegate ─────────────────────────────────────────────

_DELEGATE_PAYLOAD = {
    "task_id": "task-xyz-001",
    "from_instance_id": "trusted-peer-id",
    "from_user_id": "local:owner",
    "actor_id": "instance:trusted-peer-id",
    "scope": "chat",
    "prompt": "Bonjour depuis un autre Lumena",
    "context": {},
}


def test_delegate_without_auth_rejected(client):
    r = client.post("/api/peer/delegate", json=_DELEGATE_PAYLOAD)
    assert r.status_code in {401, 403}


def test_delegate_unknown_peer_refused(client):
    """Aucune entrée dans le registre avec ce peer_token_hash → 401."""
    r = client.post("/api/peer/delegate", json=_DELEGATE_PAYLOAD, headers=PEER_AUTH)
    assert r.status_code == 401


def test_delegate_blocked_peer_refused(client, tmp_path, monkeypatch):
    """Un pair bloqué n'a pas de peer_token_hash reconnu → 401 (fail-closed)."""
    _add_peer_with_token(peers_module._PEER_REGISTRY_FILE, "trusted-peer-id", trust="blocked")
    r = client.post("/api/peer/delegate", json=_DELEGATE_PAYLOAD, headers=PEER_AUTH)
    assert r.status_code in {401, 403}


def test_delegate_invalid_scope_refused(client, tmp_path):
    """Un scope inconnu ou absent de allowed_scopes est refusé avec peer token valide.

    Statut 403 (autorisation refusée) — validate_peer_scope vérifie VALID_SCOPES
    ET allowed_scopes du pair, les deux conditions sont des contrôles d'autorisation.
    """
    _add_peer_with_token(peers_module._PEER_REGISTRY_FILE, "trusted-peer-id")
    payload = {**_DELEGATE_PAYLOAD, "scope": "browser"}
    r = client.post("/api/peer/delegate", json=payload, headers=PEER_AUTH)
    assert r.status_code == 403


def test_delegate_trusted_peer_chat_accepted(client, monkeypatch):
    """Un pair trusted avec scope=chat et peer token valide est accepté."""
    _add_peer_with_token(peers_module._PEER_REGISTRY_FILE, "trusted-peer-id")

    from web.routes import deps as _deps
    mock_lumena = AsyncMock()
    mock_lumena.chat = AsyncMock(return_value="Bonjour depuis instance locale !")
    monkeypatch.setattr(_deps, "lumena", mock_lumena)

    r = client.post("/api/peer/delegate", json=_DELEGATE_PAYLOAD, headers=PEER_AUTH)
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "completed"
    assert data["task_id"] == "task-xyz-001"
    assert data["response"] != ""


def test_delegate_result_stored_for_task_query(client, monkeypatch):
    """La tâche déléguée est accessible via GET /api/peer/tasks/{task_id}."""
    _add_peer_with_token(peers_module._PEER_REGISTRY_FILE, "trusted-peer-id")

    from web.routes import deps as _deps
    mock_lumena = AsyncMock()
    mock_lumena.chat = AsyncMock(return_value="Réponse de délégation")
    monkeypatch.setattr(_deps, "lumena", mock_lumena)

    client.post("/api/peer/delegate", json=_DELEGATE_PAYLOAD, headers=PEER_AUTH)
    r = client.get("/api/peer/tasks/task-xyz-001", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["task_id"] == "task-xyz-001"


def test_delegate_task_unknown_returns_404(client):
    r = client.get("/api/peer/tasks/nonexistent-task", headers=AUTH)
    assert r.status_code == 404


# ── Route GET /api/peer/audit ─────────────────────────────────────────────────

def test_audit_log_endpoint_protected(client):
    r = client.get("/api/peer/audit")
    assert r.status_code in {401, 403}


def test_audit_log_empty_initially(client, monkeypatch):
    import src.runtime.peer_protocol as _proto
    monkeypatch.setattr(_proto, "PEER_AUDIT_LOG", Path("/nonexistent/audit.jsonl"))
    r = client.get("/api/peer/audit", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["count"] == 0


# ── Fix sécurité : trust != "trusted" fail-closed ────────────────────────────

def test_delegate_corrupted_trust_refused(client, monkeypatch):
    """Trust corrompu (ex: 'admin') → peer_token_hash non reconnu par verify_peer_token → 401."""
    reg_file = peers_module._PEER_REGISTRY_FILE
    reg_file.write_text(
        f'{{"trusted-peer-id": {{"trust": "admin", "instance_id": "trusted-peer-id",'
        f'"peer_token_hash": "{_peer_token_hash()}"}}}}',
        encoding="utf-8",
    )
    r = client.post("/api/peer/delegate", json=_DELEGATE_PAYLOAD, headers=PEER_AUTH)
    assert r.status_code == 401


def test_delegate_trust_with_trailing_space_refused(client, monkeypatch):
    """'trusted ' (avec espace) → non reconnu par verify_peer_token → 401."""
    reg_file = peers_module._PEER_REGISTRY_FILE
    reg_file.write_text(
        f'{{"trusted-peer-id": {{"trust": "trusted ", "instance_id": "trusted-peer-id",'
        f'"peer_token_hash": "{_peer_token_hash()}"}}}}',
        encoding="utf-8",
    )
    r = client.post("/api/peer/delegate", json=_DELEGATE_PAYLOAD, headers=PEER_AUTH)
    assert r.status_code == 401


# ── Fix hardening discover ────────────────────────────────────────────────────

def test_discover_public_network_rejected(client, monkeypatch):
    """Un réseau public (ex: 8.8.8.0/24) doit être refusé."""
    import src.runtime.peer_discovery as _disc
    monkeypatch.setattr(_disc, "PEER_DISCOVERY_ENABLED", True)
    r = client.post(
        "/api/peer/discover",
        json={"network": "8.8.8.0/24"},
        headers=AUTH,
    )
    assert r.status_code == 422
    assert "privés" in r.json()["detail"] or "RFC1918" in r.json()["detail"]


def test_discover_too_many_ports_rejected(client, monkeypatch):
    """Plus de 20 ports → refus 422."""
    import src.runtime.peer_discovery as _disc
    monkeypatch.setattr(_disc, "PEER_DISCOVERY_ENABLED", True)
    r = client.post(
        "/api/peer/discover",
        json={"ports": list(range(8080, 8110))},  # 30 ports
        headers=AUTH,
    )
    assert r.status_code == 422
    assert "Trop de ports" in r.json()["detail"]


def test_discover_privileged_port_rejected(client, monkeypatch):
    """Port < 1024 → refus 422."""
    import src.runtime.peer_discovery as _disc
    monkeypatch.setattr(_disc, "PEER_DISCOVERY_ENABLED", True)
    r = client.post(
        "/api/peer/discover",
        json={"ports": [80, 443]},
        headers=AUTH,
    )
    assert r.status_code == 422
    assert "hors plage" in r.json()["detail"]


def test_discover_timeout_clamped(client, monkeypatch):
    """Un timeout hors plage est silencieusement borné (pas d'erreur, scan limité)."""
    import src.runtime.peer_discovery as _disc
    monkeypatch.setattr(_disc, "PEER_DISCOVERY_ENABLED", True)

    captured = {}

    async def _fake_scan(*, network=None, ports=None, timeout=1.5, **kw):
        captured["timeout"] = timeout
        return []

    monkeypatch.setattr(_disc, "scan_lan_for_peers", _fake_scan)
    client.post("/api/peer/discover", json={"timeout": 999.0}, headers=AUTH)
    assert captured.get("timeout", 999) <= 5.0


def test_discover_private_network_allowed(client, monkeypatch):
    """192.168.x.x est autorisé."""
    import src.runtime.peer_discovery as _disc
    monkeypatch.setattr(_disc, "PEER_DISCOVERY_ENABLED", True)

    async def _fake_scan(**kw):
        return []

    monkeypatch.setattr(_disc, "scan_lan_for_peers", _fake_scan)
    r = client.post(
        "/api/peer/discover",
        json={"network": "192.168.1.0/24"},
        headers=AUTH,
    )
    assert r.status_code == 200


def test_audit_log_records_refused_delegation(client, monkeypatch):
    """Une délégation refusée est tracée dans l'audit log.

    Phase 8.5 + P1 : verify_peer_token passe (token-holder trusted), mais
    from_instance_id ≠ token owner → usurpation refusée ET auditée.
    """
    import src.runtime.peer_protocol as _proto
    audit_file = Path(peers_module._PEER_REGISTRY_FILE).parent / "peer_audit.jsonl"
    monkeypatch.setattr(_proto, "PEER_AUDIT_LOG", audit_file)

    # token-holder = pair avec token valide, from_instance_id du payload = autre pair
    _add_peer_with_token(peers_module._PEER_REGISTRY_FILE, "token-holder", trust="trusted")

    # Usurpation : token de token-holder, prétend être "trusted-peer-id"
    payload = {**_DELEGATE_PAYLOAD, "from_instance_id": "trusted-peer-id"}
    client.post("/api/peer/delegate", json=payload, headers=PEER_AUTH)

    r = client.get("/api/peer/audit", headers=AUTH)
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert any(e["status"] == "refused" for e in entries)
