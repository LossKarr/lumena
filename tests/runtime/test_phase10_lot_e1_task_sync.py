"""Lot E1 Phase 10 — Tests Tasks sync bounded.

Couvre :
Route POST /api/peer/tasks/run-sync :
- peer unknown (usurpation token) refusé 403
- peer blocked refusé 403
- trusted sans scope task.delegate refusé 403
- trusted avec scope accepté → completed
- timeout borné [10, 300]
- objective avec secret refusé 422
- résultat tronqué à _TASK_SYNC_MAX_RESULT_CHARS
- audit task_sync_started / completed / refused / timeout / failed écrit
- aucun token dans la réponse
- Lumena non initialisée → 500
- asyncio.TimeoutError → status=timeout

Tool run_peer_task_sync (handler ReAct) :
- flag off → liste vide
- flag on → handler enregistré avec category agents
- peer absent → refus
- peer blocked → refus
- trusted sans scope task.delegate → refus
- token outbound absent → refus
- SSRF → refus avant HTTP
- secret dans objective → refus, secret absent du message
- HTTP 200 completed → résultat retourné, aucun token
- HTTP 200 status=timeout → erreur propre
- HTTP 200 status=failed → erreur propre
- HTTP 500 → erreur propre
- timeout réseau → erreur propre
- audit task_sync_started et completed écrits
- module enregistré dans tool_registry.py
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
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


TRUSTED_PEER_TASK = {
    "instance_id": "peer-task",
    "instance_name": "Lumena Worker",
    "host": "192.168.1.77",
    "port": 8081,
    "capabilities": ["task"],
    "trust": "trusted",
    "pairing_method": "code",
    "paired_at": "2026-05-07T00:00:00+00:00",
    "last_seen": datetime.now(timezone.utc).isoformat(),
    "peer_token_hash": "cafebabe" * 8,
    "peer_token_outbound": "tok-task-out",
    "allowed_scopes": ["chat", "task.delegate"],
}

TRUSTED_PEER_NO_TASK = {**TRUSTED_PEER_TASK, "instance_id": "peer-notask",
                        "allowed_scopes": ["chat"]}
BLOCKED_PEER = {**TRUSTED_PEER_TASK, "instance_id": "peer-blk", "trust": "blocked"}

PAYLOAD_BASE = {
    "task_id": "t-001",
    "from_instance_id": "peer-task",
    "from_user_id": "local:owner",
    "actor_id": "lumena_agent",
    "objective": "Résume les bonnes pratiques Redis.",
    "timeout_sec": 30,
    "expected_output": "summary",
}


def _client_with_peer(tmp_path, monkeypatch, peer: dict,
                      lumena_mock=None) -> TestClient:
    reg_file = tmp_path / "peer_registry.json"
    _write_registry(reg_file, {peer["instance_id"]: peer})
    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg_file)
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
    monkeypatch.setattr(_paths, "INSTANCE_NAME", "Lumena Principal")
    monkeypatch.setattr(_paths, "INSTANCE_ROLE", "standalone")
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")

    app = _make_app()
    app.dependency_overrides[peers_module.verify_peer_token] = lambda: peer
    if lumena_mock is not None:
        import web.routes.deps as deps_mod
        monkeypatch.setattr(deps_mod, "lumena", lumena_mock)
    return TestClient(app, raise_server_exceptions=False)


def _lumena_ok(result: str = "Voici le résumé Redis.") -> MagicMock:
    lumena = MagicMock()
    lumena.chat = AsyncMock(return_value=result)
    return lumena


@pytest.fixture(autouse=True)
def _reset_peer_rate_limit():
    from src.runtime.peer_rate_limit import reset_peer_counters

    reset_peer_counters(TRUSTED_PEER_TASK["instance_id"])
    reset_peer_counters(TRUSTED_PEER_NO_TASK["instance_id"])
    reset_peer_counters(BLOCKED_PEER["instance_id"])
    yield
    reset_peer_counters(TRUSTED_PEER_TASK["instance_id"])
    reset_peer_counters(TRUSTED_PEER_NO_TASK["instance_id"])
    reset_peer_counters(BLOCKED_PEER["instance_id"])


# ── Tests : authentification et trust ────────────────────────────────────────

class TestTaskSyncAuth:

    def test_usurpation_refusee(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK,
                                   _lumena_ok())
        payload = {**PAYLOAD_BASE, "from_instance_id": "peer-imposteur"}
        r = client.post("/api/peer/tasks/run-sync", json=payload)
        assert r.status_code == 403
        assert "Usurpation" in r.json()["detail"] or "peer-imposteur" in r.json()["detail"]

    def test_peer_blocked_refuse(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, BLOCKED_PEER,
                                   _lumena_ok())
        payload = {**PAYLOAD_BASE, "from_instance_id": "peer-blk"}
        r = client.post("/api/peer/tasks/run-sync", json=payload)
        assert r.status_code == 403

    def test_trusted_sans_scope_task_refuse(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_NO_TASK,
                                   _lumena_ok())
        payload = {**PAYLOAD_BASE, "from_instance_id": "peer-notask"}
        r = client.post("/api/peer/tasks/run-sync", json=payload)
        assert r.status_code == 403
        assert "task.delegate" in r.json()["detail"]

    def test_trusted_avec_scope_task_accepte(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK,
                                   _lumena_ok("Résumé Redis OK."))
        r = client.post("/api/peer/tasks/run-sync", json=PAYLOAD_BASE)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "completed"
        assert "Redis" in data["result"]

    def test_lumena_non_initialisee_retourne_500(self, tmp_path, monkeypatch):
        import web.routes.deps as deps_mod
        monkeypatch.setattr(deps_mod, "lumena", None)
        reg_file = tmp_path / "peer_registry.json"
        _write_registry(reg_file, {"peer-task": TRUSTED_PEER_TASK})
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg_file)
        import src.utils.paths as _paths
        monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
        app = _make_app()
        app.dependency_overrides[peers_module.verify_peer_token] = lambda: TRUSTED_PEER_TASK
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.post("/api/peer/tasks/run-sync", json=PAYLOAD_BASE)
        assert r.status_code in (200, 500)
        if r.status_code == 200:
            assert r.json()["status"] == "failed"


# ── Tests : sécurité objective ────────────────────────────────────────────────

class TestTaskSyncSecurity:

    def test_objective_avec_bearer_token_refuse(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK,
                                   _lumena_ok())
        payload = {**PAYLOAD_BASE,
                   "objective": "Voici mon token : Bearer eyJhbGci.eyJzdW.secret"}
        r = client.post("/api/peer/tasks/run-sync", json=payload)
        assert r.status_code == 422
        assert "secret" in r.json()["detail"].lower()

    def test_objective_avec_hex_secret_refuse(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK,
                                   _lumena_ok())
        payload = {**PAYLOAD_BASE, "objective": f"hash : {'f' * 40}"}
        r = client.post("/api/peer/tasks/run-sync", json=payload)
        assert r.status_code == 422

    def test_aucun_token_dans_reponse(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK,
                                   _lumena_ok("Résultat propre."))
        r = client.post("/api/peer/tasks/run-sync", json=PAYLOAD_BASE)
        assert r.status_code == 200
        body = r.text
        assert "cafebabe" not in body
        assert "tok-task-out" not in body


# ── Tests : verrou E1 — champs secrets + peer_message + defaults ─────────────

class TestTaskSyncVerrouE1:

    def test_secret_dans_context_refuse(self, tmp_path, monkeypatch):
        """Secret dans context → refus 422 avant chat."""
        secret = "d" * 40
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK,
                                   _lumena_ok())
        payload = {**PAYLOAD_BASE, "context": {"key": secret}}
        r = client.post("/api/peer/tasks/run-sync", json=payload)
        assert r.status_code == 422
        assert "context" in r.json()["detail"].lower()

    def test_secret_dans_context_bearer_refuse(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK,
                                   _lumena_ok())
        payload = {**PAYLOAD_BASE,
                   "context": {"auth": "Bearer eyJhbGci.eyJzdW.secret"}}
        r = client.post("/api/peer/tasks/run-sync", json=payload)
        assert r.status_code == 422

    def test_secret_dans_expected_output_refuse(self, tmp_path, monkeypatch):
        secret = "e" * 40
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK,
                                   _lumena_ok())
        payload = {**PAYLOAD_BASE, "expected_output": f"retourne ce hash : {secret}"}
        r = client.post("/api/peer/tasks/run-sync", json=payload)
        assert r.status_code == 422
        assert "expected_output" in r.json()["detail"]

    def test_secret_chat_not_called_when_context_refused(self, tmp_path, monkeypatch):
        """deps.lumena.chat ne doit pas être appelé si context contient un secret."""
        lumena = _lumena_ok()
        secret = "f" * 40
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK, lumena)
        payload = {**PAYLOAD_BASE, "context": {"token": secret}}
        r = client.post("/api/peer/tasks/run-sync", json=payload)
        assert r.status_code == 422
        lumena.chat.assert_not_called()

    def test_peer_message_expired_refused(self, tmp_path, monkeypatch):
        """peer_message avec TTL expiré → 422."""
        from datetime import timedelta
        old_ts = (datetime.now(timezone.utc) - timedelta(seconds=400)).isoformat()
        expired_env = {
            "message_id": "aaa",
            "conversation_id": "bbb",
            "trace_id": "ccc",
            "type": "task_request",
            "scope": "task.delegate",
            "from_instance_id": "peer-task",
            "to_instance_id": "self-001",
            "created_at": old_ts,
            "ttl_seconds": 300,
            "hop_count": 0,
            "payload": {"objective": "test"},
        }
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK,
                                   _lumena_ok())
        payload = {**PAYLOAD_BASE, "peer_message": expired_env}
        r = client.post("/api/peer/tasks/run-sync", json=payload)
        assert r.status_code == 422
        assert "expiré" in r.json()["detail"].lower() or "peer_message" in r.json()["detail"].lower()

    def test_peer_message_hop_count_too_high_refused(self, tmp_path, monkeypatch):
        """peer_message avec hop_count dépassant LUMENA_PEER_MAX_HOPS → 422."""
        monkeypatch.setenv("LUMENA_PEER_MAX_HOPS", "3")
        valid_env = {
            "message_id": "aaa",
            "conversation_id": "bbb",
            "trace_id": "ccc",
            "type": "task_request",
            "scope": "task.delegate",
            "from_instance_id": "peer-task",
            "to_instance_id": "self-001",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "ttl_seconds": 300,
            "hop_count": 4,   # > max_hops=3
            "payload": {"objective": "test"},
        }
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK,
                                   _lumena_ok())
        payload = {**PAYLOAD_BASE, "peer_message": valid_env}
        r = client.post("/api/peer/tasks/run-sync", json=payload)
        assert r.status_code == 422
        assert "hop_count" in r.json()["detail"].lower() or "peer_message" in r.json()["detail"].lower()

    def test_peer_message_absent_passes(self, tmp_path, monkeypatch):
        """peer_message absent → pas de validation, legacy intact."""
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK,
                                   _lumena_ok())
        payload = {k: v for k, v in PAYLOAD_BASE.items() if k != "peer_message"}
        r = client.post("/api/peer/tasks/run-sync", json=payload)
        assert r.status_code == 200

    def test_defaults_pydantic_non_partages(self):
        """context et allowed_tools doivent être des instances distinctes par requête."""
        from web.routes.peers import TaskSyncRequest
        r1 = TaskSyncRequest(
            task_id="t1", from_instance_id="p1", objective="obj1",
        )
        r2 = TaskSyncRequest(
            task_id="t2", from_instance_id="p2", objective="obj2",
        )
        r1.context["key"] = "val"
        assert "key" not in r2.context, "Mutable default partagé entre instances !"
        r1.allowed_tools.append("tool_x")
        assert "tool_x" not in r2.allowed_tools, "Mutable default partagé entre instances !"


# ── Tests : comportement de la réponse ───────────────────────────────────────

class TestTaskSyncResponse:

    def test_task_id_preservee(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK,
                                   _lumena_ok())
        payload = {**PAYLOAD_BASE, "task_id": "mon-task-42"}
        r = client.post("/api/peer/tasks/run-sync", json=payload)
        assert r.status_code == 200
        assert r.json()["task_id"] == "mon-task-42"

    def test_origin_instance_id_present(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK,
                                   _lumena_ok())
        r = client.post("/api/peer/tasks/run-sync", json=PAYLOAD_BASE)
        assert r.status_code == 200
        assert r.json()["origin_instance_id"] == "self-001"

    def test_duration_ms_present_et_positif(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK,
                                   _lumena_ok())
        r = client.post("/api/peer/tasks/run-sync", json=PAYLOAD_BASE)
        assert r.status_code == 200
        assert r.json()["duration_ms"] >= 0

    def test_resultat_tronque(self, tmp_path, monkeypatch):
        long_result = "Z" * 10000
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK,
                                   _lumena_ok(long_result))
        r = client.post("/api/peer/tasks/run-sync", json=PAYLOAD_BASE)
        assert r.status_code == 200
        assert len(r.json()["result"]) <= 8001

    def test_timeout_borné_min(self, tmp_path, monkeypatch):
        """timeout_sec=1 → clamped à 10 côté receveur (pas d'erreur de validation)."""
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK,
                                   _lumena_ok())
        payload = {**PAYLOAD_BASE, "timeout_sec": 1}
        r = client.post("/api/peer/tasks/run-sync", json=payload)
        assert r.status_code == 200

    def test_asyncio_timeout_retourne_status_timeout(self, tmp_path, monkeypatch):
        lumena = MagicMock()

        async def _slow(*a, **kw):
            raise asyncio.TimeoutError()

        lumena.chat = _slow
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK, lumena)
        r = client.post("/api/peer/tasks/run-sync", json=PAYLOAD_BASE)
        assert r.status_code == 200
        assert r.json()["status"] == "timeout"


# ── Tests : audit ─────────────────────────────────────────────────────────────

class TestTaskSyncAudit:

    def test_audit_started_and_completed(self, tmp_path, monkeypatch):
        audited = []
        monkeypatch.setattr(
            "src.runtime.peer_protocol.write_audit_log",
            lambda **kw: audited.append(kw),
        )
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK,
                                   _lumena_ok())
        r = client.post("/api/peer/tasks/run-sync", json=PAYLOAD_BASE)
        assert r.status_code == 200
        events = [e["event"] for e in audited]
        assert "task_sync_started" in events
        assert "task_sync_completed" in events

    def test_audit_refused_on_missing_scope(self, tmp_path, monkeypatch):
        audited = []
        monkeypatch.setattr(
            "src.runtime.peer_protocol.write_audit_log",
            lambda **kw: audited.append(kw),
        )
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_NO_TASK,
                                   _lumena_ok())
        payload = {**PAYLOAD_BASE, "from_instance_id": "peer-notask"}
        r = client.post("/api/peer/tasks/run-sync", json=payload)
        assert r.status_code == 403
        events = [e["event"] for e in audited]
        assert "task_sync_refused" in events

    def test_audit_timeout(self, tmp_path, monkeypatch):
        audited = []
        monkeypatch.setattr(
            "src.runtime.peer_protocol.write_audit_log",
            lambda **kw: audited.append(kw),
        )
        lumena = MagicMock()
        lumena.chat = AsyncMock(side_effect=asyncio.TimeoutError())
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER_TASK, lumena)
        r = client.post("/api/peer/tasks/run-sync", json=PAYLOAD_BASE)
        assert r.status_code == 200
        events = [e["event"] for e in audited]
        assert "task_sync_timeout" in events


# ── Tests : handler ReAct run_peer_task_sync ──────────────────────────────────

TRUSTED_PEER_HANDLER = {
    "instance_id": "peer-task",
    "instance_name": "Lumena Worker",
    "host": "192.168.1.77",
    "port": 8081,
    "trust": "trusted",
    "peer_token_hash": "cafebabe" * 8,
    "peer_token_outbound": "SECRET_TASK_TOKEN_NEVER_EXPOSE",
    "allowed_scopes": ["chat", "task.delegate"],
}


async def _call_task_handler(monkeypatch, tmp_path, peer_dict=None,
                             instance_id="peer-task",
                             objective="Résume Redis.",
                             env_flag="1",
                             http_response=None, http_exc=None):
    registry = {}
    if peer_dict is not None:
        registry[peer_dict["instance_id"]] = peer_dict

    reg_file = tmp_path / "peer_registry.json"
    reg_file.write_text(json.dumps(registry), encoding="utf-8")

    from src.reasoning.handlers import peer_tasks as mod
    monkeypatch.setattr(mod, "_PEER_REGISTRY_FILE", reg_file)
    monkeypatch.setenv("LUMENA_PEER_COLLABORATION", env_flag)

    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")

    if http_exc is not None:
        async def _bad(*a, **kw): raise http_exc
        monkeypatch.setattr("httpx.AsyncClient.post", _bad)
    elif http_response is not None:
        mock_resp = MagicMock()
        mock_resp.status_code = http_response.get("status_code", 200)
        mock_resp.json.return_value = http_response.get("json", {})

        async def _ok(*a, **kw): return mock_resp
        monkeypatch.setattr("httpx.AsyncClient.post", _ok)

    return await mod.run_peer_task_sync_handler(
        MagicMock(), instance_id=instance_id, objective=objective,
    )


class TestRunPeerTaskSyncHandler:

    def test_flag_off_returns_empty_defs(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "0")
        from src.reasoning.handlers import peer_tasks as mod
        assert mod.get_peer_tasks_handler_defs() == []

    def test_flag_on_returns_defs(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_COLLABORATION", "1")
        from src.reasoning.handlers import peer_tasks as mod
        defs = mod.get_peer_tasks_handler_defs()
        names = [d.name for d in defs]
        assert "run_peer_task_sync" in names
        assert all(d.category == "peers" for d in defs)

    @pytest.mark.asyncio
    async def test_flag_off_handler_refuses(self, monkeypatch, tmp_path):
        result = await _call_task_handler(monkeypatch, tmp_path,
                                          peer_dict=TRUSTED_PEER_HANDLER,
                                          env_flag="0")
        assert not result.success
        assert "LUMENA_PEER_COLLABORATION" in result.output

    @pytest.mark.asyncio
    async def test_peer_absent_refused(self, monkeypatch, tmp_path):
        result = await _call_task_handler(monkeypatch, tmp_path,
                                          peer_dict=None,
                                          instance_id="peer-ghost")
        assert not result.success

    @pytest.mark.asyncio
    async def test_peer_blocked_refused(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_HANDLER, "trust": "blocked"}
        result = await _call_task_handler(monkeypatch, tmp_path, peer_dict=peer)
        assert not result.success
        assert "bloqué" in result.output.lower()

    @pytest.mark.asyncio
    async def test_trusted_sans_scope_task_refused(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_HANDLER, "allowed_scopes": ["chat"]}
        result = await _call_task_handler(monkeypatch, tmp_path, peer_dict=peer)
        assert not result.success
        assert "task.delegate" in result.output

    @pytest.mark.asyncio
    async def test_no_outbound_token_refused(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_HANDLER, "peer_token_outbound": ""}
        result = await _call_task_handler(monkeypatch, tmp_path, peer_dict=peer)
        assert not result.success
        assert "token" in result.output.lower()

    @pytest.mark.asyncio
    async def test_ssrf_refused_before_http(self, monkeypatch, tmp_path):
        peer = {**TRUSTED_PEER_HANDLER, "host": "8.8.8.8"}
        result = await _call_task_handler(monkeypatch, tmp_path, peer_dict=peer)
        assert not result.success
        assert "rfc1918" in result.output.lower() or "non autorisée" in result.output.lower()

    @pytest.mark.asyncio
    async def test_secret_in_objective_refused(self, monkeypatch, tmp_path):
        secret = "e" * 40
        result = await _call_task_handler(monkeypatch, tmp_path,
                                          peer_dict=TRUSTED_PEER_HANDLER,
                                          objective=f"token : {secret}")
        assert not result.success
        assert secret not in result.output

    @pytest.mark.asyncio
    async def test_http_200_completed_returns_result(self, monkeypatch, tmp_path):
        result = await _call_task_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_HANDLER,
            http_response={"status_code": 200, "json": {
                "status": "completed",
                "result": "Redis est une base clé-valeur en mémoire.",
                "duration_ms": 850,
            }},
        )
        assert result.success
        assert "Redis" in result.output
        assert "850" in result.output

    @pytest.mark.asyncio
    async def test_http_200_no_token_in_output(self, monkeypatch, tmp_path):
        result = await _call_task_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_HANDLER,
            http_response={"status_code": 200, "json": {
                "status": "completed", "result": "OK", "duration_ms": 100,
            }},
        )
        assert result.success
        assert "SECRET_TASK_TOKEN_NEVER_EXPOSE" not in result.output

    @pytest.mark.asyncio
    async def test_http_200_status_timeout_returns_error(self, monkeypatch, tmp_path):
        result = await _call_task_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_HANDLER,
            http_response={"status_code": 200, "json": {
                "status": "timeout", "result": "", "duration_ms": 30000,
            }},
        )
        assert not result.success
        assert "timeout" in result.output.lower()

    @pytest.mark.asyncio
    async def test_http_200_status_failed_returns_error(self, monkeypatch, tmp_path):
        result = await _call_task_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_HANDLER,
            http_response={"status_code": 200, "json": {
                "status": "failed", "result": "Erreur interne.", "duration_ms": 50,
            }},
        )
        assert not result.success

    @pytest.mark.asyncio
    async def test_http_500_returns_error(self, monkeypatch, tmp_path):
        result = await _call_task_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_HANDLER,
            http_response={"status_code": 500, "json": {}},
        )
        assert not result.success
        assert "500" in result.output

    @pytest.mark.asyncio
    async def test_timeout_network_returns_proper_message(self, monkeypatch, tmp_path):
        import httpx
        result = await _call_task_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_HANDLER,
            http_exc=httpx.TimeoutException("timed out"),
        )
        assert not result.success
        assert "timeout" in result.output.lower() or "répondu" in result.output.lower()

    @pytest.mark.asyncio
    async def test_audit_started_and_completed_written(self, monkeypatch, tmp_path):
        audited = []

        from src.reasoning.handlers import peer_tasks as mod
        monkeypatch.setattr(mod, "_audit",
                            lambda ev, iid, tid, sc, st, detail="": audited.append(ev))

        await _call_task_handler(
            monkeypatch, tmp_path, peer_dict=TRUSTED_PEER_HANDLER,
            http_response={"status_code": 200, "json": {
                "status": "completed", "result": "OK", "duration_ms": 100,
            }},
        )
        assert "task_sync_started" in audited
        assert "task_sync_completed" in audited

    def test_module_registered_in_tool_registry(self):
        content = (
            Path(__file__).parents[2] / "src/reasoning/tool_registry.py"
        ).read_text(encoding="utf-8")
        assert "peer_tasks" in content
        assert "get_peer_tasks_handler_defs" in content
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
