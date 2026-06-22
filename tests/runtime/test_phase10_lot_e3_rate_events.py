"""Lot E3 Phase 10 - rate limit, task events and restart recovery."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import peers as peers_module
from web.routes.peers import router as peers_router


TRUSTED_PEER = {
    "instance_id": "peer-e3",
    "instance_name": "Lumena E3",
    "host": "192.168.1.77",
    "port": 8081,
    "capabilities": ["task"],
    "trust": "trusted",
    "pairing_method": "code",
    "paired_at": "2026-05-07T00:00:00+00:00",
    "last_seen": datetime.now(timezone.utc).isoformat(),
    "peer_token_hash": "c0ffee" * 10 + "c0ff",
    "peer_token_outbound": "tok-e3-out",
    "allowed_scopes": ["chat", "task.delegate"],
}

OTHER_PEER = {**TRUSTED_PEER, "instance_id": "peer-e3-other"}


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(peers_router)
    return app


def _write_registry(path, peers: dict) -> None:
    path.write_text(json.dumps(peers, ensure_ascii=False, indent=2), encoding="utf-8")


def _payload(task_id: str = "e3-task-1", objective: str = "Fais un resume court.") -> dict:
    return {
        "task_id": task_id,
        "from_instance_id": TRUSTED_PEER["instance_id"],
        "objective": objective,
        "timeout_sec": 30,
        "expected_output": "summary",
    }


def _lumena_ok(result: str = "OK") -> MagicMock:
    lumena = MagicMock()
    lumena.chat = AsyncMock(return_value=result)
    lumena.think_and_act_silent = AsyncMock(return_value=result)
    return lumena


def _client(tmp_path, monkeypatch, peer: dict = TRUSTED_PEER, lumena=None) -> TestClient:
    reg_file = tmp_path / "peer_registry.json"
    _write_registry(reg_file, {TRUSTED_PEER["instance_id"]: TRUSTED_PEER, OTHER_PEER["instance_id"]: OTHER_PEER})
    events_file = tmp_path / "peer_tasks" / "task_events.jsonl"

    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg_file)
    monkeypatch.setattr(peers_module, "_PEER_TASK_EVENTS_FILE", events_file)
    monkeypatch.setattr(peers_module, "_task_recovery_done", False)

    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", "self-e3")
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")

    import web.routes.deps as deps_mod
    monkeypatch.setattr(deps_mod, "lumena", lumena if lumena is not None else _lumena_ok())

    app = _make_app()
    app.dependency_overrides[peers_module.verify_peer_token] = lambda: peer
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clean_state():
    from src.runtime.peer_rate_limit import reset_peer_counters

    reset_peer_counters(TRUSTED_PEER["instance_id"])
    reset_peer_counters(OTHER_PEER["instance_id"])
    with peers_module._async_tasks_lock:
        peers_module._async_task_store.clear()
    yield
    reset_peer_counters(TRUSTED_PEER["instance_id"])
    reset_peer_counters(OTHER_PEER["instance_id"])
    with peers_module._async_tasks_lock:
        peers_module._async_task_store.clear()


class TestRateLimit:

    def test_task_delegate_rate_limit_returns_429_retry_after(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LUMENA_RATE_TASK_DELEGATE", "1")
        client = _client(tmp_path, monkeypatch)

        first = client.post("/api/peer/tasks/run-sync", json=_payload("e3-rate-1"))
        second = client.post("/api/peer/tasks/run-sync", json=_payload("e3-rate-2"))

        assert first.status_code == 200
        assert second.status_code == 429
        assert int(second.headers["Retry-After"]) >= 1
        assert "Rate limit" in second.json()["detail"]

    def test_invalid_payload_does_not_consume_rate_quota(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LUMENA_RATE_TASK_DELEGATE", "1")
        client = _client(tmp_path, monkeypatch)

        invalid = client.post(
            "/api/peer/tasks/run-sync",
            json=_payload("e3-invalid", "token Bearer eyJhbGci.eyJzdW.secret"),
        )
        valid = client.post("/api/peer/tasks/run-sync", json=_payload("e3-valid"))

        assert invalid.status_code == 422
        assert valid.status_code == 200

    def test_async_parallel_limit_returns_429(self, tmp_path, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_MAX_PARALLEL_TASKS", "1")
        monkeypatch.setenv("LUMENA_RATE_TASK_DELEGATE", "100")

        lumena = MagicMock()

        async def _slow(*_args, **_kwargs):
            await asyncio.sleep(0.3)
            return "done"

        lumena.chat = AsyncMock(side_effect=_slow)
        lumena.think_and_act_silent = AsyncMock(side_effect=_slow)
        with _client(tmp_path, monkeypatch, lumena=lumena) as client:
            first = client.post("/api/peer/tasks/submit", json=_payload("e3-par-1"))
            second = client.post("/api/peer/tasks/submit", json=_payload("e3-par-2"))

        assert first.status_code == 200
        assert second.status_code == 429
        assert int(second.headers["Retry-After"]) >= 1


class TestTaskEvents:

    def test_submit_writes_events_and_events_route_returns_them(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        r = client.post("/api/peer/tasks/submit", json=_payload("e3-events-1"))
        assert r.status_code == 200

        events = client.get("/api/peer/tasks/e3-events-1/events")
        assert events.status_code == 200
        data = events.json()
        assert data["task_id"] == "e3-events-1"
        assert data["count"] >= 1
        assert data["events"][0]["event"] == "task_async_queued"

    def test_events_route_rejects_other_peer(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        r = client.post("/api/peer/tasks/submit", json=_payload("e3-owner-1"))
        assert r.status_code == 200

        other_client = _client(tmp_path, monkeypatch, peer=OTHER_PEER)
        events = other_client.get("/api/peer/tasks/e3-owner-1/events")
        assert events.status_code == 403

    def test_restart_recovery_marks_old_running_task_interrupted(self, tmp_path, monkeypatch):
        client = _client(tmp_path, monkeypatch)
        peers_module._write_task_event(
            task_id="e3-old-running",
            from_instance_id=TRUSTED_PEER["instance_id"],
            event="task_async_running",
            status="running",
            origin_instance_id="self-e3",
        )
        with peers_module._async_tasks_lock:
            peers_module._async_task_store.clear()
        monkeypatch.setattr(peers_module, "_task_recovery_done", False)

        status = client.get("/api/peer/tasks/e3-old-running/status")
        assert status.status_code == 200
        assert status.json()["status"] == "interrupted"

        events = client.get("/api/peer/tasks/e3-old-running/events")
        assert events.status_code == 200
        assert events.json()["events"][-1]["event"] == "task_async_interrupted"


class TestNetworkMiniUi:

    def test_peer_scopes_rendered_and_test_button_scope_gated(self):
        js = (peers_module.Path(__file__).parents[2] / "web" / "static" / "js" / "panels.js").read_text(
            encoding="utf-8"
        )
        assert "function _peerScopesHtml" in js
        assert "_peerScopesHtml(p)" in js
        assert "scopes.includes('chat')" in js
        assert "onclick=\"testDelegation" in js
