from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.runtime.task_orchestrator import TaskOrchestrator
from web.routes import deps
from web.routes import steering as routes


def _client(monkeypatch, *, authenticated=True):
    orchestrator = TaskOrchestrator(persistence_path=None)
    task = orchestrator.start_task(
        conversation_id="conv-a", channel="web", message_preview="objectif",
        metadata={
            "kind": "agent_turn", "owner_user_id": "local:owner",
            "source_conversation_id": "conv-a", "initial_objective": "objectif",
        },
    )
    orchestrator.mark_running(task.task_id)
    monkeypatch.setattr(deps, "get_task_orchestrator", lambda: orchestrator)
    monkeypatch.setattr(routes.deps, "TELEMETRY_AVAILABLE", False)
    app = FastAPI()
    app.include_router(routes.router)
    if authenticated:
        app.dependency_overrides[deps.verify_admin_token] = lambda: None
    return TestClient(app), orchestrator, task.task_id


def test_routes_require_admin_auth(monkeypatch):
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    monkeypatch.delenv("LUMENA_ADMIN_TOKEN", raising=False)
    client, _orchestrator, task_id = _client(monkeypatch, authenticated=False)
    response = client.get(f"/api/tasks/{task_id}/steering")
    assert response.status_code in {401, 403}


def test_create_list_and_replay_are_structured_and_do_not_expose_identity(monkeypatch):
    client, _orchestrator, task_id = _client(monkeypatch)
    payload = {
        "text": "garde le menu",
        "delivery_policy": "urgent_safe_boundary",
        "idempotency_key": "request-0001",
        "conversation_id": "conv-a",
    }
    created = client.post(f"/api/tasks/{task_id}/steering", json=payload)
    assert created.status_code == 200
    assert created.json()["accepted"] is True
    assert created.json()["safe_boundary"] in {"next_checkpoint", "generation_interruptible"}
    replay = client.post(f"/api/tasks/{task_id}/steering", json=payload)
    assert replay.json()["command_id"] == created.json()["command_id"]
    listed = client.get(f"/api/tasks/{task_id}/steering", params={"conversation_id": "conv-a"})
    assert len(listed.json()["commands"]) == 1
    assert "owner_user_id" not in listed.text
    assert "request_fingerprint" not in listed.text


def test_api_refuses_owner_and_conversation_mismatch(monkeypatch):
    client, orchestrator, task_id = _client(monkeypatch)
    assert client.post(
        f"/api/tasks/{task_id}/steering",
        json={"text": "x", "conversation_id": "conv-b"},
    ).status_code == 409
    orchestrator.set_task_metadata(task_id, owner_user_id="another-owner")
    assert client.get(f"/api/tasks/{task_id}/steering").status_code == 403


def test_unknown_fields_and_oversized_text_are_rejected(monkeypatch):
    client, _orchestrator, task_id = _client(monkeypatch)
    assert client.post(
        f"/api/tasks/{task_id}/steering", json={"text": "ok", "forged_owner": "local:owner"},
    ).status_code == 422
    assert client.post(
        f"/api/tasks/{task_id}/steering", json={"text": "x" * 8001},
    ).status_code == 422


def test_terminal_task_is_reported_without_losing_text(monkeypatch):
    client, orchestrator, task_id = _client(monkeypatch)
    orchestrator.mark_done(task_id, "done")
    response = client.post(f"/api/tasks/{task_id}/steering", json={"text": "arrive tard"})
    assert response.status_code == 200
    assert response.json()["accepted"] is False
    assert response.json()["status"] == "late"
    assert response.json()["text"] == "arrive tard"
    listed = client.get(f"/api/tasks/{task_id}/steering")
    assert listed.status_code == 200
    assert listed.json()["commands"][0]["status"] == "late"
