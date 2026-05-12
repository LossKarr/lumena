"""Lot E2 Phase 10 — Tests Tasks async queue.

Couvre :
Route POST /api/peer/tasks/submit :
- peer unknown (usurpation token) refusé 403
- peer blocked refusé 403
- trusted sans scope task.delegate refusé 403
- objective avec secret refusé 422
- expected_output avec secret refusé 422
- context avec secret refusé 422
- peer_message expiré refusé 422
- trusted avec scope accepté → queued + task_id
- audit task_async_queued écrit
- Lumena non initialisée → background échoue mais submit accepté

Route GET /api/peer/tasks/{task_id}/status :
- tâche inconnue → 404
- tâche d'un autre pair → 403
- tâche queued → status queued
- tâche completed → status + résultat

Route DELETE /api/peer/tasks/{task_id} :
- tâche inconnue → 404
- tâche d'un autre pair → 403
- tâche queued → cancelled + store mis à jour
- tâche déjà terminée → note "Déjà terminée"
- asyncio.Task.cancel() appelé si tâche en cours

Tools submit_peer_task / get_peer_task_status (handlers ReAct) :
- flag on → liste avec 3 defs (sync + submit + status)
- flag off handler refuses
- submit : peer absent, blocked, sans scope, token absent, SSRF, secret → refus
- submit : HTTP 200 queued → task_id retourné
- submit : HTTP 500 → erreur propre
- get_status : peer absent → refus
- get_status : HTTP 200 completed → résultat retourné, redacté
- get_status : HTTP 404 → erreur propre
- get_status : HTTP 200 running → statut en cours

TTL cleanup :
- entrées > 1h retirées, entrées récentes conservées
"""
from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone, timedelta
from typing import Optional
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
    "instance_id": "peer-async",
    "instance_name": "Lumena Async Worker",
    "host": "192.168.1.77",
    "port": 8081,
    "capabilities": ["task"],
    "trust": "trusted",
    "pairing_method": "code",
    "paired_at": "2026-05-07T00:00:00+00:00",
    "last_seen": datetime.now(timezone.utc).isoformat(),
    "peer_token_hash": "cafecafe" * 8,
    "peer_token_outbound": "tok-async-out",
    "allowed_scopes": ["chat", "task.delegate"],
}

BLOCKED_PEER = {**TRUSTED_PEER, "instance_id": "peer-blk-a", "trust": "blocked"}
TRUSTED_NO_SCOPE = {**TRUSTED_PEER, "instance_id": "peer-noscope", "allowed_scopes": ["chat"]}
OTHER_PEER = {**TRUSTED_PEER, "instance_id": "peer-other"}


def _valid_submit_payload(task_id: str = "ta-test001",
                          instance_id: str = "peer-async") -> dict:
    return {
        "task_id": task_id,
        "from_instance_id": instance_id,
        "objective": "Résume les étapes de la CI.",
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
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")

    app = _make_app()
    app.dependency_overrides[peers_module.verify_peer_token] = lambda: peer
    if lumena_mock is not None:
        import web.routes.deps as deps_mod
        monkeypatch.setattr(deps_mod, "lumena", lumena_mock)
    return TestClient(app, raise_server_exceptions=False)


def _lumena_ok(result: str = "Voici le résultat async.") -> MagicMock:
    lumena = MagicMock()
    lumena.chat = AsyncMock(return_value=result)
    return lumena


# ── Fixture nettoyage store ───────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_async_store():
    """Vide le store async entre les tests."""
    from src.runtime.peer_rate_limit import reset_peer_counters

    reset_peer_counters(TRUSTED_PEER["instance_id"])
    reset_peer_counters(BLOCKED_PEER["instance_id"])
    reset_peer_counters(TRUSTED_NO_SCOPE["instance_id"])
    reset_peer_counters(OTHER_PEER["instance_id"])
    with peers_module._async_tasks_lock:
        peers_module._async_task_store.clear()
    yield
    reset_peer_counters(TRUSTED_PEER["instance_id"])
    reset_peer_counters(BLOCKED_PEER["instance_id"])
    reset_peer_counters(TRUSTED_NO_SCOPE["instance_id"])
    reset_peer_counters(OTHER_PEER["instance_id"])
    with peers_module._async_tasks_lock:
        peers_module._async_task_store.clear()


def _seed_task(task_id: str, from_instance_id: str = "peer-async",
               status: str = "queued", result: Optional[str] = None,
               duration_ms: Optional[float] = None, asyncio_task=None):
    with peers_module._async_tasks_lock:
        peers_module._async_task_store[task_id] = {
            "status": status,
            "result": result,
            "duration_ms": duration_ms,
            "from_instance_id": from_instance_id,
            "origin_instance_id": "self-001",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "_created_mono": time.monotonic(),
            "_asyncio_task": asyncio_task,
        }


# ═══════════════════════════════════════════════════════════════════════════════
# POST /api/peer/tasks/submit
# ═══════════════════════════════════════════════════════════════════════════════

class TestTaskSubmitAuth:

    def test_usurpation_refusee(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER, _lumena_ok())
        payload = {**_valid_submit_payload(), "from_instance_id": "autre-instance"}
        r = client.post("/api/peer/tasks/submit", json=payload)
        assert r.status_code == 403
        assert "Usurpation" in r.json()["detail"]

    def test_peer_blocked_refuse(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, BLOCKED_PEER, _lumena_ok())
        payload = _valid_submit_payload(instance_id=BLOCKED_PEER["instance_id"])
        r = client.post("/api/peer/tasks/submit", json=payload)
        assert r.status_code == 403
        assert "bloquée" in r.json()["detail"]

    def test_trusted_sans_scope_task_refuse(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_NO_SCOPE, _lumena_ok())
        payload = _valid_submit_payload(instance_id=TRUSTED_NO_SCOPE["instance_id"])
        r = client.post("/api/peer/tasks/submit", json=payload)
        assert r.status_code == 403
        assert "task.delegate" in r.json()["detail"]

    def test_lumena_non_initialisee_submit_accepte(self, tmp_path, monkeypatch):
        import web.routes.deps as deps_mod
        monkeypatch.setattr(deps_mod, "lumena", None)
        reg_file = tmp_path / "peer_registry.json"
        _write_registry(reg_file, {TRUSTED_PEER["instance_id"]: TRUSTED_PEER})
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg_file)
        import src.utils.paths as _paths
        monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
        app = _make_app()
        app.dependency_overrides[peers_module.verify_peer_token] = lambda: TRUSTED_PEER
        with TestClient(app, raise_server_exceptions=False) as c:
            r = c.post("/api/peer/tasks/submit", json=_valid_submit_payload("ta-nolumena"))
        # Le submit est accepté (queued), l'échec se produit en background
        assert r.status_code == 200
        assert r.json()["status"] == "queued"


class TestTaskSubmitVerrouE2:
    """Hardenings E2 : context["peer_message"], collision 409, cleanup orphelin, truncation."""

    def test_envelope_dans_context_expiree_refuse(self, tmp_path, monkeypatch):
        """Une enveloppe expirée dans context["peer_message"] doit retourner 422."""
        from src.runtime.peer_messages import create_peer_message
        expired_msg = create_peer_message(
            type="task_request", scope="task.delegate",
            from_instance_id="peer-async", to_instance_id="self-001",
            payload={"objective": "test"}, ttl_seconds=10,
        )
        d = expired_msg.to_dict()
        d["created_at"] = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()

        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER, _lumena_ok())
        payload = {
            **_valid_submit_payload(),
            "context": {"peer_message": d},
        }
        r = client.post("/api/peer/tasks/submit", json=payload)
        assert r.status_code == 422
        assert "peer_message" in r.json()["detail"].lower()

    def test_envelope_dans_context_hop_count_trop_haut_refuse(self, tmp_path, monkeypatch):
        """Une enveloppe avec hop_count dépassant la limite dans context doit retourner 422."""
        import os
        from src.runtime.peer_messages import create_peer_message
        max_hops = int(os.getenv("LUMENA_PEER_MAX_HOPS", "5"))
        valid_msg = create_peer_message(
            type="task_request", scope="task.delegate",
            from_instance_id="peer-async", to_instance_id="self-001",
            payload={"objective": "test"}, ttl_seconds=120,
        )
        d = valid_msg.to_dict()
        d["hop_count"] = max_hops + 1

        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER, _lumena_ok())
        payload = {
            **_valid_submit_payload(),
            "context": {"peer_message": d},
        }
        r = client.post("/api/peer/tasks/submit", json=payload)
        assert r.status_code == 422
        assert "peer_message" in r.json()["detail"].lower()

    def test_collision_task_id_retourne_409(self, tmp_path, monkeypatch):
        """Deux submits avec le même task_id : le second doit retourner 409."""
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER, _lumena_ok())
        payload = _valid_submit_payload("ta-collision1")
        r1 = client.post("/api/peer/tasks/submit", json=payload)
        assert r1.status_code == 200
        r2 = client.post("/api/peer/tasks/submit", json=payload)
        assert r2.status_code == 409

    def test_collision_ne_remplace_pas_entree_existante(self, tmp_path, monkeypatch):
        """En cas de collision 409, l'entrée originale dans le store n'est pas écrasée."""
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER, _lumena_ok())
        payload = _valid_submit_payload("ta-collision2")
        r1 = client.post("/api/peer/tasks/submit", json=payload)
        assert r1.status_code == 200
        original_created_at = r1.json()["created_at"]

        r2 = client.post("/api/peer/tasks/submit", json=payload)
        assert r2.status_code == 409

        with peers_module._async_tasks_lock:
            entry = peers_module._async_task_store.get("ta-collision2")
        assert entry is not None
        assert entry["created_at"] == original_created_at

    def test_cleanup_ttl_cancel_asyncio_task(self):
        """_cleanup_old_async_tasks annule les tâches asyncio orphelines."""
        from web.routes.peers import (_cleanup_old_async_tasks,
                                       _async_task_store, _async_tasks_lock)
        mock_task = MagicMock()
        mock_task.done.return_value = False
        old_mono = time.monotonic() - 3700
        with _async_tasks_lock:
            _async_task_store["ta-orphan"] = {
                "status": "running", "result": None,
                "from_instance_id": "x", "origin_instance_id": "y",
                "created_at": "2026-01-01T00:00:00+00:00",
                "_created_mono": old_mono,
                "_asyncio_task": mock_task,
            }
            _cleanup_old_async_tasks()
        mock_task.cancel.assert_called_once()
        with _async_tasks_lock:
            assert "ta-orphan" not in _async_task_store


class TestTaskSubmitSecurity:

    def test_secret_dans_objective_refuse(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER, _lumena_ok())
        payload = {**_valid_submit_payload(),
                   "objective": "fais ça avec 0a1b2c3d4e5f0a1b2c3d4e5f0a1b2c3d"}
        r = client.post("/api/peer/tasks/submit", json=payload)
        assert r.status_code == 422
        assert "secret" in r.json()["detail"].lower()

    def test_secret_dans_expected_output_refuse(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER, _lumena_ok())
        payload = {**_valid_submit_payload(),
                   "expected_output": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"}
        r = client.post("/api/peer/tasks/submit", json=payload)
        assert r.status_code == 422

    def test_secret_dans_context_refuse(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER, _lumena_ok())
        payload = {**_valid_submit_payload(),
                   "context": {"key": "deadbeef" * 8}}
        r = client.post("/api/peer/tasks/submit", json=payload)
        assert r.status_code == 422

    def test_peer_message_expire_refuse(self, tmp_path, monkeypatch):
        from src.runtime.peer_messages import create_peer_message
        expired_msg = create_peer_message(
            type="task_request", scope="task.delegate",
            from_instance_id="peer-async", to_instance_id="self-001",
            payload={"objective": "test"}, ttl_seconds=10,
        )
        d = expired_msg.to_dict()
        d["created_at"] = (datetime.now(timezone.utc) - timedelta(seconds=60)).isoformat()

        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER, _lumena_ok())
        payload = {**_valid_submit_payload(), "peer_message": d}
        r = client.post("/api/peer/tasks/submit", json=payload)
        assert r.status_code == 422
        assert "peer_message" in r.json()["detail"].lower()


class TestTaskSubmitAccepted:

    def test_submit_accepte_retourne_queued(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER, _lumena_ok())
        r = client.post("/api/peer/tasks/submit", json=_valid_submit_payload("ta-abc123"))
        assert r.status_code == 200
        data = r.json()
        assert data["task_id"] == "ta-abc123"
        assert data["status"] == "queued"
        assert data["origin_instance_id"]
        assert data["created_at"]

    def test_submit_enregistre_dans_store(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER, _lumena_ok())
        r = client.post("/api/peer/tasks/submit", json=_valid_submit_payload("ta-store001"))
        assert r.status_code == 200
        with peers_module._async_tasks_lock:
            entry = peers_module._async_task_store.get("ta-store001")
        assert entry is not None
        assert entry["from_instance_id"] == TRUSTED_PEER["instance_id"]


# ═══════════════════════════════════════════════════════════════════════════════
# GET /api/peer/tasks/{task_id}/status
# ═══════════════════════════════════════════════════════════════════════════════

class TestTaskStatusRoute:

    def test_tache_inconnue_404(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER)
        r = client.get("/api/peer/tasks/ta-inconnu/status")
        assert r.status_code == 404

    def test_tache_autre_pair_403(self, tmp_path, monkeypatch):
        _seed_task("ta-other", from_instance_id="peer-async")
        # OTHER_PEER essaie de lire la tâche qui appartient à TRUSTED_PEER
        client = _client_with_peer(tmp_path, monkeypatch, OTHER_PEER)
        r = client.get("/api/peer/tasks/ta-other/status")
        assert r.status_code == 403

    def test_tache_queued_retourne_status(self, tmp_path, monkeypatch):
        _seed_task("ta-queued1", status="queued")
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER)
        r = client.get("/api/peer/tasks/ta-queued1/status")
        assert r.status_code == 200
        data = r.json()
        assert data["task_id"] == "ta-queued1"
        assert data["status"] == "queued"
        assert data["result"] is None

    def test_tache_completed_retourne_resultat(self, tmp_path, monkeypatch):
        _seed_task("ta-done1", status="completed",
                   result="Voilà le résultat.", duration_ms=142.0)
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER)
        r = client.get("/api/peer/tasks/ta-done1/status")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "completed"
        assert "résultat" in data["result"]
        assert data["duration_ms"] == 142.0

    def test_tache_running_retourne_status_running(self, tmp_path, monkeypatch):
        _seed_task("ta-run1", status="running")
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER)
        r = client.get("/api/peer/tasks/ta-run1/status")
        assert r.status_code == 200
        assert r.json()["status"] == "running"


# ═══════════════════════════════════════════════════════════════════════════════
# DELETE /api/peer/tasks/{task_id}
# ═══════════════════════════════════════════════════════════════════════════════

class TestTaskCancelRoute:

    def test_tache_inconnue_404(self, tmp_path, monkeypatch):
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER)
        r = client.delete("/api/peer/tasks/ta-inconnu")
        assert r.status_code == 404

    def test_tache_autre_pair_403(self, tmp_path, monkeypatch):
        _seed_task("ta-cancel-other", from_instance_id="peer-async")
        client = _client_with_peer(tmp_path, monkeypatch, OTHER_PEER)
        r = client.delete("/api/peer/tasks/ta-cancel-other")
        assert r.status_code == 403

    def test_annulation_task_queued(self, tmp_path, monkeypatch):
        _seed_task("ta-cancel1", status="queued")
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER)
        r = client.delete("/api/peer/tasks/ta-cancel1")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["status"] == "cancelled"
        with peers_module._async_tasks_lock:
            entry = peers_module._async_task_store.get("ta-cancel1")
        assert entry["status"] == "cancelled"

    def test_annulation_tache_deja_terminee(self, tmp_path, monkeypatch):
        _seed_task("ta-done2", status="completed")
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER)
        r = client.delete("/api/peer/tasks/ta-done2")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["status"] == "completed"
        assert "Déjà terminée" in data["note"]

    def test_annulation_appelle_cancel_asyncio_task(self, tmp_path, monkeypatch):
        mock_task = MagicMock()
        mock_task.done.return_value = False
        _seed_task("ta-bg1", status="running", asyncio_task=mock_task)
        client = _client_with_peer(tmp_path, monkeypatch, TRUSTED_PEER)
        r = client.delete("/api/peer/tasks/ta-bg1")
        assert r.status_code == 200
        mock_task.cancel.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════════════
# Tool handlers submit_peer_task / get_peer_task_status
# ═══════════════════════════════════════════════════════════════════════════════

class TestSubmitPeerTaskHandler:

    def _make_registry(self, tmp_path, peers: dict):
        reg_file = tmp_path / "peer_registry.json"
        reg_file.write_text(json.dumps(peers), encoding="utf-8")
        return reg_file

    def test_flag_on_retourne_3_defs(self):
        from src.reasoning.handlers.peer_tasks import get_peer_tasks_handler_defs
        with patch.dict("os.environ", {"LUMENA_PEER_COLLABORATION": "1"}):
            defs = get_peer_tasks_handler_defs()
        names = [d.name for d in defs]
        assert "run_peer_task_sync" in names
        assert "submit_peer_task" in names
        assert "get_peer_task_status" in names

    def test_flag_off_handler_refuses(self):
        from src.reasoning.handlers.peer_tasks import submit_peer_task_handler
        with patch.dict("os.environ", {"LUMENA_PEER_COLLABORATION": "0"}):
            result = asyncio.get_event_loop().run_until_complete(
                submit_peer_task_handler(None, "peer-async", "objectif")
            )
        assert not result.success

    def test_peer_absent_refused(self, tmp_path):
        from src.reasoning.handlers.peer_tasks import submit_peer_task_handler
        reg_file = self._make_registry(tmp_path, {})
        with patch.dict("os.environ", {"LUMENA_PEER_COLLABORATION": "1"}):
            with patch("src.reasoning.handlers.peer_tasks._PEER_REGISTRY_FILE", reg_file):
                result = asyncio.get_event_loop().run_until_complete(
                    submit_peer_task_handler(None, "peer-absent", "objectif")
                )
        assert not result.success
        assert "inconnu" in result.output.lower()

    def test_peer_blocked_refused(self, tmp_path):
        from src.reasoning.handlers.peer_tasks import submit_peer_task_handler
        registry = {"peer-blk": {**TRUSTED_PEER, "instance_id": "peer-blk", "trust": "blocked"}}
        reg_file = self._make_registry(tmp_path, registry)
        with patch.dict("os.environ", {"LUMENA_PEER_COLLABORATION": "1"}):
            with patch("src.reasoning.handlers.peer_tasks._PEER_REGISTRY_FILE", reg_file):
                result = asyncio.get_event_loop().run_until_complete(
                    submit_peer_task_handler(None, "peer-blk", "objectif")
                )
        assert not result.success
        assert "bloqué" in result.output.lower()

    def test_ssrf_refused(self, tmp_path):
        from src.reasoning.handlers.peer_tasks import submit_peer_task_handler
        registry = {"peer-pub": {**TRUSTED_PEER, "instance_id": "peer-pub", "host": "8.8.8.8"}}
        reg_file = self._make_registry(tmp_path, registry)
        with patch.dict("os.environ", {"LUMENA_PEER_COLLABORATION": "1"}):
            with patch("src.reasoning.handlers.peer_tasks._PEER_REGISTRY_FILE", reg_file):
                result = asyncio.get_event_loop().run_until_complete(
                    submit_peer_task_handler(None, "peer-pub", "objectif")
                )
        assert not result.success

    def test_secret_dans_objective_refused(self, tmp_path):
        from src.reasoning.handlers.peer_tasks import submit_peer_task_handler
        registry = {TRUSTED_PEER["instance_id"]: TRUSTED_PEER}
        reg_file = self._make_registry(tmp_path, registry)
        with patch.dict("os.environ", {"LUMENA_PEER_COLLABORATION": "1"}):
            with patch("src.reasoning.handlers.peer_tasks._PEER_REGISTRY_FILE", reg_file):
                result = asyncio.get_event_loop().run_until_complete(
                    submit_peer_task_handler(
                        None, TRUSTED_PEER["instance_id"],
                        "objectif avec 0a1b2c3d4e5f0a1b2c3d4e5f0a1b2c3d"
                    )
                )
        assert not result.success
        assert "secret" in result.output.lower()

    def test_http_200_queued_retourne_task_id(self, tmp_path):
        from src.reasoning.handlers.peer_tasks import submit_peer_task_handler
        registry = {TRUSTED_PEER["instance_id"]: TRUSTED_PEER}
        reg_file = self._make_registry(tmp_path, registry)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "task_id": "ta-resp001",
            "status": "queued",
            "origin_instance_id": "peer-async",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        with patch.dict("os.environ", {"LUMENA_PEER_COLLABORATION": "1"}):
            with patch("src.reasoning.handlers.peer_tasks._PEER_REGISTRY_FILE", reg_file):
                with patch("httpx.AsyncClient") as mock_cls:
                    mock_client = AsyncMock()
                    mock_client.post = AsyncMock(return_value=mock_resp)
                    mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                    result = asyncio.get_event_loop().run_until_complete(
                        submit_peer_task_handler(
                            None, TRUSTED_PEER["instance_id"], "Résume les logs d'hier."
                        )
                    )
        assert result.success
        assert "ta-resp001" in result.output

    def test_http_500_retourne_erreur(self, tmp_path):
        from src.reasoning.handlers.peer_tasks import submit_peer_task_handler
        registry = {TRUSTED_PEER["instance_id"]: TRUSTED_PEER}
        reg_file = self._make_registry(tmp_path, registry)

        mock_resp = MagicMock()
        mock_resp.status_code = 500

        with patch.dict("os.environ", {"LUMENA_PEER_COLLABORATION": "1"}):
            with patch("src.reasoning.handlers.peer_tasks._PEER_REGISTRY_FILE", reg_file):
                with patch("httpx.AsyncClient") as mock_cls:
                    mock_client = AsyncMock()
                    mock_client.post = AsyncMock(return_value=mock_resp)
                    mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                    result = asyncio.get_event_loop().run_until_complete(
                        submit_peer_task_handler(
                            None, TRUSTED_PEER["instance_id"], "Résume les logs."
                        )
                    )
        assert not result.success
        assert "500" in result.output


class TestGetPeerTaskStatusHandler:

    def _make_registry(self, tmp_path, peers: dict):
        reg_file = tmp_path / "peer_registry.json"
        reg_file.write_text(json.dumps(peers), encoding="utf-8")
        return reg_file

    def test_flag_off_refused(self):
        from src.reasoning.handlers.peer_tasks import get_peer_task_status_handler
        with patch.dict("os.environ", {"LUMENA_PEER_COLLABORATION": "0"}):
            result = asyncio.get_event_loop().run_until_complete(
                get_peer_task_status_handler(None, "peer-async", "ta-001")
            )
        assert not result.success

    def test_peer_absent_refused(self, tmp_path):
        from src.reasoning.handlers.peer_tasks import get_peer_task_status_handler
        reg_file = self._make_registry(tmp_path, {})
        with patch.dict("os.environ", {"LUMENA_PEER_COLLABORATION": "1"}):
            with patch("src.reasoning.handlers.peer_tasks._PEER_REGISTRY_FILE", reg_file):
                result = asyncio.get_event_loop().run_until_complete(
                    get_peer_task_status_handler(None, "peer-absent", "ta-001")
                )
        assert not result.success

    def test_http_200_completed_retourne_resultat(self, tmp_path):
        from src.reasoning.handlers.peer_tasks import get_peer_task_status_handler
        registry = {TRUSTED_PEER["instance_id"]: TRUSTED_PEER}
        reg_file = self._make_registry(tmp_path, registry)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "task_id": "ta-done001",
            "status": "completed",
            "result": "Voici le résultat de la tâche demandée.",
            "duration_ms": 234.0,
            "origin_instance_id": "peer-async",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        with patch.dict("os.environ", {"LUMENA_PEER_COLLABORATION": "1"}):
            with patch("src.reasoning.handlers.peer_tasks._PEER_REGISTRY_FILE", reg_file):
                with patch("httpx.AsyncClient") as mock_cls:
                    mock_client = AsyncMock()
                    mock_client.get = AsyncMock(return_value=mock_resp)
                    mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                    result = asyncio.get_event_loop().run_until_complete(
                        get_peer_task_status_handler(
                            None, TRUSTED_PEER["instance_id"], "ta-done001"
                        )
                    )
        assert result.success
        assert "completed" in result.output
        assert "résultat" in result.output.lower()

    def test_result_redacte(self, tmp_path):
        from src.reasoning.handlers.peer_tasks import get_peer_task_status_handler
        registry = {TRUSTED_PEER["instance_id"]: TRUSTED_PEER}
        reg_file = self._make_registry(tmp_path, registry)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "task_id": "ta-redact",
            "status": "completed",
            "result": "Résultat avec secret abcdef0123456789abcdef0123456789 dedans.",
            "duration_ms": 100.0,
            "origin_instance_id": "peer-async",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        with patch.dict("os.environ", {"LUMENA_PEER_COLLABORATION": "1"}):
            with patch("src.reasoning.handlers.peer_tasks._PEER_REGISTRY_FILE", reg_file):
                with patch("httpx.AsyncClient") as mock_cls:
                    mock_client = AsyncMock()
                    mock_client.get = AsyncMock(return_value=mock_resp)
                    mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                    result = asyncio.get_event_loop().run_until_complete(
                        get_peer_task_status_handler(
                            None, TRUSTED_PEER["instance_id"], "ta-redact"
                        )
                    )
        assert result.success
        assert "abcdef0123456789" not in result.output
        assert "[REDACTED]" in result.output

    def test_http_404_retourne_erreur(self, tmp_path):
        from src.reasoning.handlers.peer_tasks import get_peer_task_status_handler
        registry = {TRUSTED_PEER["instance_id"]: TRUSTED_PEER}
        reg_file = self._make_registry(tmp_path, registry)

        mock_resp = MagicMock()
        mock_resp.status_code = 404

        with patch.dict("os.environ", {"LUMENA_PEER_COLLABORATION": "1"}):
            with patch("src.reasoning.handlers.peer_tasks._PEER_REGISTRY_FILE", reg_file):
                with patch("httpx.AsyncClient") as mock_cls:
                    mock_client = AsyncMock()
                    mock_client.get = AsyncMock(return_value=mock_resp)
                    mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                    result = asyncio.get_event_loop().run_until_complete(
                        get_peer_task_status_handler(
                            None, TRUSTED_PEER["instance_id"], "ta-gone"
                        )
                    )
        assert not result.success
        assert "inconnue" in result.output.lower()

    def test_result_trop_long_tronque(self, tmp_path):
        """Un résultat > 4000 chars doit être tronqué côté handler."""
        from src.reasoning.handlers.peer_tasks import get_peer_task_status_handler
        registry = {TRUSTED_PEER["instance_id"]: TRUSTED_PEER}
        reg_file = self._make_registry(tmp_path, registry)

        # Chaîne non-hex pour éviter le filtre _SECRET_VALUE_RE (z n'est pas hex)
        long_result = "lumena_résultat_" * 320  # ~5120 chars, pas de hex32+
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "task_id": "ta-long",
            "status": "completed",
            "result": long_result,
            "duration_ms": 50.0,
            "origin_instance_id": "peer-async",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        with patch.dict("os.environ", {"LUMENA_PEER_COLLABORATION": "1"}):
            with patch("src.reasoning.handlers.peer_tasks._PEER_REGISTRY_FILE", reg_file):
                with patch("httpx.AsyncClient") as mock_cls:
                    mock_client = AsyncMock()
                    mock_client.get = AsyncMock(return_value=mock_resp)
                    mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                    result = asyncio.get_event_loop().run_until_complete(
                        get_peer_task_status_handler(
                            None, TRUSTED_PEER["instance_id"], "ta-long"
                        )
                    )
        assert result.success
        # Le résultat brut fait ~5120 chars mais le handler doit tronquer à 4000
        assert "lumena_résultat_" in result.output
        assert "…" in result.output  # marqueur de troncature
        assert len(result.output) < 4200 + 200  # marge pour le texte autour

    def test_http_200_running_retourne_statut(self, tmp_path):
        from src.reasoning.handlers.peer_tasks import get_peer_task_status_handler
        registry = {TRUSTED_PEER["instance_id"]: TRUSTED_PEER}
        reg_file = self._make_registry(tmp_path, registry)

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "task_id": "ta-running",
            "status": "running",
            "result": None,
            "duration_ms": None,
            "origin_instance_id": "peer-async",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        with patch.dict("os.environ", {"LUMENA_PEER_COLLABORATION": "1"}):
            with patch("src.reasoning.handlers.peer_tasks._PEER_REGISTRY_FILE", reg_file):
                with patch("httpx.AsyncClient") as mock_cls:
                    mock_client = AsyncMock()
                    mock_client.get = AsyncMock(return_value=mock_resp)
                    mock_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
                    mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)
                    result = asyncio.get_event_loop().run_until_complete(
                        get_peer_task_status_handler(
                            None, TRUSTED_PEER["instance_id"], "ta-running"
                        )
                    )
        assert result.success
        assert "running" in result.output


# ── TTL cleanup ───────────────────────────────────────────────────────────────

class TestAsyncTaskStoreTTL:

    def test_cleanup_retire_entrees_expirees(self):
        from web.routes.peers import (_cleanup_old_async_tasks,
                                       _async_task_store, _async_tasks_lock)
        old_mono = time.monotonic() - 3700  # > 1h
        with _async_tasks_lock:
            _async_task_store["ta-old"] = {
                "status": "completed", "result": "ok",
                "from_instance_id": "x", "origin_instance_id": "y",
                "created_at": "2026-01-01T00:00:00+00:00",
                "_created_mono": old_mono, "_asyncio_task": None,
            }
            _async_task_store["ta-recent"] = {
                "status": "queued", "result": None,
                "from_instance_id": "x", "origin_instance_id": "y",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "_created_mono": time.monotonic(),
                "_asyncio_task": None,
            }
            _cleanup_old_async_tasks()
            keys = list(_async_task_store.keys())
        assert "ta-old" not in keys
        assert "ta-recent" in keys
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
