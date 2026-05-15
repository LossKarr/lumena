"""Lot 0 Phase 10 — Tests scopes inter-instances Lumena.

Couvre :
- validate_peer_scope (unité)
- receive_delegation : scope absent du peer → 403, scope présent → 200
- GET /api/peers/{id}/scopes
- PUT /api/peers/{id}/scopes : mise à jour, scope inconnu, peer inconnu, audit
- pair_peer : allowed_scopes défini par défaut + préservé
"""
from __future__ import annotations

import json
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import peers as peers_module
from web.routes.peers import router as peers_router


# ── Helpers ───────────────────────────────────────────────────────────────────

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


@pytest.fixture()
def authed_with_peer(tmp_path, monkeypatch) -> Generator[tuple[TestClient, "Path"], None, None]:
    """Client avec un peer trusted pré-chargé dans le registre."""
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


# ─────────────────────────────────────────────────────────────────────────────
# validate_peer_scope — tests unitaires (pas d'HTTP)
# ─────────────────────────────────────────────────────────────────────────────

class TestValidatePeerScope:

    def test_valid_scope_in_allowed_ok(self):
        from src.runtime.peer_scopes import validate_peer_scope
        peer = {"instance_id": "p1", "allowed_scopes": ["chat", "knowledge.query"]}
        validate_peer_scope(peer, "chat")           # ne lève pas
        validate_peer_scope(peer, "knowledge.query")  # ne lève pas

    def test_valid_scope_not_in_allowed_raises(self):
        from src.runtime.peer_scopes import validate_peer_scope
        peer = {"instance_id": "p1", "allowed_scopes": ["chat"]}
        with pytest.raises(PermissionError, match="task.delegate"):
            validate_peer_scope(peer, "task.delegate")

    def test_unknown_scope_raises(self):
        from src.runtime.peer_scopes import validate_peer_scope
        peer = {"instance_id": "p1", "allowed_scopes": ["chat", "evil.scope"]}
        with pytest.raises(PermissionError, match="inconnu"):
            validate_peer_scope(peer, "evil.scope")

    def test_peer_no_allowed_scopes_raises(self):
        from src.runtime.peer_scopes import validate_peer_scope
        peer = {"instance_id": "p1"}  # pas de allowed_scopes
        with pytest.raises(PermissionError, match="chat"):
            validate_peer_scope(peer, "chat")

    def test_empty_allowed_scopes_raises(self):
        from src.runtime.peer_scopes import validate_peer_scope
        peer = {"instance_id": "p1", "allowed_scopes": []}
        with pytest.raises(PermissionError):
            validate_peer_scope(peer, "chat")

    def test_all_valid_scopes_recognized(self):
        from src.runtime.peer_scopes import VALID_SCOPES, validate_peer_scope
        peer = {"instance_id": "p1", "allowed_scopes": list(VALID_SCOPES)}
        for scope in VALID_SCOPES:
            validate_peer_scope(peer, scope)  # aucun ne doit lever

    def test_default_scopes_contains_chat(self):
        from src.runtime.peer_scopes import DEFAULT_SCOPES
        assert "chat" in DEFAULT_SCOPES

    def test_valid_scopes_v1_complete(self):
        from src.runtime.peer_scopes import VALID_SCOPES
        expected = {
            "chat", "knowledge.query", "knowledge.share",
            "task.delegate", "task.status", "task.cancel", "artifact.share",
        }
        assert expected == set(VALID_SCOPES)


# ─────────────────────────────────────────────────────────────────────────────
# GET /api/peers/{id}/scopes
# ─────────────────────────────────────────────────────────────────────────────

class TestGetPeerScopes:

    def test_returns_scopes_for_known_peer(self, authed_with_peer):
        client, _ = authed_with_peer
        r = client.get("/api/peers/peer-aaa/scopes", headers=AUTH)
        assert r.status_code == 200
        d = r.json()
        assert d["instance_id"] == "peer-aaa"
        assert "chat" in d["allowed_scopes"]
        assert "valid_scopes" in d

    def test_unknown_peer_404(self, authed_with_peer):
        client, _ = authed_with_peer
        r = client.get("/api/peers/unknown-xyz/scopes", headers=AUTH)
        assert r.status_code == 404

    def test_requires_admin_token(self, authed_with_peer):
        client, _ = authed_with_peer
        r = client.get("/api/peers/peer-aaa/scopes")
        assert r.status_code == 401

    def test_valid_scopes_list_complete(self, authed_with_peer):
        from src.runtime.peer_scopes import VALID_SCOPES
        client, _ = authed_with_peer
        r = client.get("/api/peers/peer-aaa/scopes", headers=AUTH)
        assert r.status_code == 200
        returned = set(r.json()["valid_scopes"])
        assert returned == set(VALID_SCOPES)


# ─────────────────────────────────────────────────────────────────────────────
# PUT /api/peers/{id}/scopes
# ─────────────────────────────────────────────────────────────────────────────

class TestUpdatePeerScopes:

    def test_valid_update_ok(self, authed_with_peer):
        client, reg_file = authed_with_peer
        r = client.put(
            "/api/peers/peer-aaa/scopes",
            json={"allowed_scopes": ["chat", "task.delegate"]},
            headers=AUTH,
        )
        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert sorted(d["allowed_scopes"]) == ["chat", "task.delegate"]

    def test_persisted_to_registry(self, authed_with_peer):
        client, reg_file = authed_with_peer
        client.put(
            "/api/peers/peer-aaa/scopes",
            json={"allowed_scopes": ["chat", "knowledge.query"]},
            headers=AUTH,
        )
        data = json.loads(reg_file.read_text(encoding="utf-8"))
        assert sorted(data["peer-aaa"]["allowed_scopes"]) == ["chat", "knowledge.query"]

    def test_unknown_scope_422(self, authed_with_peer):
        client, _ = authed_with_peer
        r = client.put(
            "/api/peers/peer-aaa/scopes",
            json={"allowed_scopes": ["chat", "admin.backdoor"]},
            headers=AUTH,
        )
        assert r.status_code == 422
        assert "admin.backdoor" in r.text

    def test_unknown_peer_404(self, authed_with_peer):
        client, _ = authed_with_peer
        r = client.put(
            "/api/peers/unknown-xyz/scopes",
            json={"allowed_scopes": ["chat"]},
            headers=AUTH,
        )
        assert r.status_code == 404

    def test_requires_admin_token(self, authed_with_peer):
        client, _ = authed_with_peer
        r = client.put(
            "/api/peers/peer-aaa/scopes",
            json={"allowed_scopes": ["chat"]},
        )
        assert r.status_code == 401

    def test_deduplicates_scopes(self, authed_with_peer):
        client, reg_file = authed_with_peer
        r = client.put(
            "/api/peers/peer-aaa/scopes",
            json={"allowed_scopes": ["chat", "chat", "task.delegate"]},
            headers=AUTH,
        )
        assert r.status_code == 200
        assert r.json()["allowed_scopes"].count("chat") == 1

    def test_audit_written(self, authed_with_peer, tmp_path):
        from src.runtime.peer_protocol import PEER_AUDIT_LOG
        client, _ = authed_with_peer
        with patch("src.runtime.peer_protocol.PEER_AUDIT_LOG", tmp_path / "audit.jsonl"):
            r = client.put(
                "/api/peers/peer-aaa/scopes",
                json={"allowed_scopes": ["chat", "task.delegate"]},
                headers=AUTH,
            )
        assert r.status_code == 200
        lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        assert any("scope_updated" in l for l in lines)

    def test_empty_scopes_allowed(self, authed_with_peer):
        client, reg_file = authed_with_peer
        r = client.put(
            "/api/peers/peer-aaa/scopes",
            json={"allowed_scopes": []},
            headers=AUTH,
        )
        assert r.status_code == 200
        assert r.json()["allowed_scopes"] == []


# ─────────────────────────────────────────────────────────────────────────────
# pair_peer — allowed_scopes par défaut et préservation
# ─────────────────────────────────────────────────────────────────────────────

class TestPairPeerScopes:

    def test_new_peer_gets_default_scopes(self, authed):
        import src.utils.paths as _paths
        r = authed.post("/api/peers/pair", headers=AUTH, json={
            "instance_id": "peer-new",
            "instance_name": "Lumena Bureau",
            "host": "192.168.1.10",
            "port": 8081,
        })
        assert r.status_code == 200
        # Lire le registre directement
        reg = json.loads(peers_module._PEER_REGISTRY_FILE.read_text(encoding="utf-8"))
        assert reg["peer-new"]["allowed_scopes"] == ["chat"]

    def test_existing_scopes_preserved_on_repair(self, authed_with_peer):
        client, reg_file = authed_with_peer
        # D'abord étendre les scopes
        client.put(
            "/api/peers/peer-aaa/scopes",
            json={"allowed_scopes": ["chat", "task.delegate"]},
            headers=AUTH,
        )
        # Re-paire le même peer (ex: mise à jour adresse)
        client.post("/api/peers/pair", headers=AUTH, json={
            "instance_id": "peer-aaa",
            "instance_name": "Lumena Salon",
            "host": "192.168.1.58",  # nouvelle IP
            "port": 8081,
        })
        data = json.loads(reg_file.read_text(encoding="utf-8"))
        assert sorted(data["peer-aaa"]["allowed_scopes"]) == ["chat", "task.delegate"]


# ─────────────────────────────────────────────────────────────────────────────
# /api/peer/delegate — validation scope par peer
# ─────────────────────────────────────────────────────────────────────────────

class TestDelegateScopeValidation:
    """Vérifie que receive_delegation applique validate_peer_scope (par peer, pas global)."""

    def _client_with_peer_and_mocked_auth(self, tmp_path, monkeypatch, peer_scopes: list[str]):
        """Prépare un TestClient avec peer trusted, allowed_scopes configurés, auth mockée."""
        reg_file = tmp_path / "peer_registry.json"
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg_file)
        import src.utils.paths as _paths
        monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
        monkeypatch.setattr(_paths, "INSTANCE_NAME", "Lumena Principal")
        monkeypatch.setattr(_paths, "INSTANCE_ROLE", "standalone")
        monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
        monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
        monkeypatch.setenv("LUMENA_PORT", "8080")

        peer = {**TRUSTED_PEER, "allowed_scopes": peer_scopes}
        _write_registry(reg_file, {"peer-aaa": peer})

        app = _make_app()
        # Override verify_peer_token pour retourner directement le peer sans vérifier le token
        app.dependency_overrides[peers_module.verify_peer_token] = lambda: peer
        return TestClient(app, raise_server_exceptions=True)

    def test_scope_in_allowed_scopes_ok(self, tmp_path, monkeypatch):
        client = self._client_with_peer_and_mocked_auth(tmp_path, monkeypatch, ["chat"])

        lumena_mock = MagicMock()
        lumena_mock.chat = AsyncMock(return_value="Délégation OK.")
        with patch.object(peers_module.deps, "lumena", lumena_mock):
            r = client.post("/api/peer/delegate", json={
                "task_id": "t001",
                "from_instance_id": "peer-aaa",
                "from_user_id": "user1",
                "actor_id": "peer-aaa",
                "scope": "chat",
                "prompt": "Test",
            })
        assert r.status_code == 200
        assert r.json()["status"] == "completed"

    def test_scope_not_in_allowed_scopes_403(self, tmp_path, monkeypatch):
        client = self._client_with_peer_and_mocked_auth(tmp_path, monkeypatch, ["chat"])

        r = client.post("/api/peer/delegate", json={
            "task_id": "t002",
            "from_instance_id": "peer-aaa",
            "from_user_id": "user1",
            "actor_id": "peer-aaa",
            "scope": "task.delegate",  # non dans allowed_scopes du peer
            "prompt": "Test",
        })
        assert r.status_code == 403
        assert "task.delegate" in r.text

    def test_unknown_scope_403(self, tmp_path, monkeypatch):
        client = self._client_with_peer_and_mocked_auth(tmp_path, monkeypatch, ["chat", "evil"])

        r = client.post("/api/peer/delegate", json={
            "task_id": "t003",
            "from_instance_id": "peer-aaa",
            "from_user_id": "user1",
            "actor_id": "peer-aaa",
            "scope": "evil",  # pas dans VALID_SCOPES même s'il est dans allowed_scopes
            "prompt": "Test",
        })
        assert r.status_code == 403
        assert "inconnu" in r.text

    def test_scope_refused_audit_written(self, tmp_path, monkeypatch):
        from src.runtime.peer_protocol import PEER_AUDIT_LOG
        audit_file = tmp_path / "audit.jsonl"

        client = self._client_with_peer_and_mocked_auth(tmp_path, monkeypatch, ["chat"])
        with patch("src.runtime.peer_protocol.PEER_AUDIT_LOG", audit_file):
            client.post("/api/peer/delegate", json={
                "task_id": "t004",
                "from_instance_id": "peer-aaa",
                "from_user_id": "user1",
                "actor_id": "peer-aaa",
                "scope": "task.delegate",
                "prompt": "Test",
            })

        lines = audit_file.read_text(encoding="utf-8").splitlines()
        refused = [l for l in lines if "delegate_refused" in l]
        assert refused
        entry = json.loads(refused[0])
        assert entry["scope"] == "task.delegate"
        assert entry["status"] == "refused"

    def test_activate_scope_then_delegate_ok(self, tmp_path, monkeypatch):
        """Activer task.delegate via PUT /scopes puis déléguer avec ce scope."""
        reg_file = tmp_path / "peer_registry.json"
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg_file)
        import src.utils.paths as _paths
        monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
        monkeypatch.setattr(_paths, "INSTANCE_NAME", "Lumena Principal")
        monkeypatch.setattr(_paths, "INSTANCE_ROLE", "standalone")
        monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
        monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
        monkeypatch.setenv("LUMENA_PORT", "8080")

        peer = {**TRUSTED_PEER, "allowed_scopes": ["chat"]}
        _write_registry(reg_file, {"peer-aaa": peer})

        app = _make_app()

        # 1. Activer task.delegate via PUT (pas de mock auth ici, route admin)
        with TestClient(app, raise_server_exceptions=True) as c:
            r = c.put(
                "/api/peers/peer-aaa/scopes",
                json={"allowed_scopes": ["chat", "task.delegate"]},
                headers=AUTH,
            )
            assert r.status_code == 200

        # 2. Peer mis à jour dans le registre — recharger
        updated_peer = json.loads(reg_file.read_text(encoding="utf-8"))["peer-aaa"]
        assert "task.delegate" in updated_peer["allowed_scopes"]

        # 3. Déléguer avec task.delegate — doit passer la validation scope
        app2 = _make_app()
        app2.dependency_overrides[peers_module.verify_peer_token] = lambda: updated_peer
        lumena_mock = MagicMock()
        lumena_mock.chat = AsyncMock(return_value="Tâche OK.")
        with TestClient(app2, raise_server_exceptions=True) as c2:
            with patch.object(peers_module.deps, "lumena", lumena_mock):
                r2 = c2.post("/api/peer/delegate", json={
                    "task_id": "t005",
                    "from_instance_id": "peer-aaa",
                    "from_user_id": "user1",
                    "actor_id": "peer-aaa",
                    "scope": "task.delegate",
                    "prompt": "Tâche test",
                })
        assert r2.status_code == 200
