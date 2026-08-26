from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.runtime import user_profile
from web.routes import deps, onboarding


def _client(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(deps, "setup_only_mode", False)
    monkeypatch.setattr(onboarding, "DATA_DIR", tmp_path)
    monkeypatch.setattr(user_profile, "MULTI_USER_ENABLED", False)
    app = FastAPI()
    app.include_router(onboarding.router)
    return TestClient(app, headers={"Authorization": "Bearer test-token"})


def test_status_start_progress_skip_complete_and_reset(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    assert client.get("/api/onboarding/status").json()["tour_status"] == "not_started"
    assert client.post("/api/onboarding/start").json()["tour_status"] == "in_progress"
    assert client.post("/api/onboarding/progress", json={"step": "orientation"}).status_code == 200
    assert client.post("/api/onboarding/skip", json={"steps": ["files"]}).json()["tour_status"] == "skipped"
    assert client.post("/api/onboarding/complete").json()["tour_status"] == "completed"
    reset = client.post("/api/onboarding/reset").json()
    assert reset["tour_status"] == "not_started"
    assert reset["setup_completed"] is True


def test_business_proof_requires_declared_application_event(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    denied_mode = client.post("/api/onboarding/progress", json={"step": "mode_choice"})
    assert denied_mode.status_code == 409
    accepted_mode = client.post("/api/onboarding/progress", json={
        "step": "mode_choice", "event": "mode_selected",
    })
    assert accepted_mode.status_code == 200
    denied = client.post("/api/onboarding/progress", json={"step": "first_message"})
    assert denied.status_code == 409
    accepted = client.post("/api/onboarding/progress", json={
        "step": "first_message", "event": "chat_response_received",
    })
    assert accepted.status_code == 200
    assert "first_message" in accepted.json()["completed_steps"]


def test_mutations_require_admin_after_bootstrap(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "secret")
    assert client.get("/api/onboarding/status").status_code == 200
    assert client.post("/api/onboarding/start", headers={"Authorization": ""}).status_code == 401
    assert client.post("/api/onboarding/start", headers={"Authorization": "Bearer secret"}).status_code == 200


def test_status_never_returns_secret_fields(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    payload = client.get("/api/onboarding/status").json()
    assert all("token" not in key.lower() and "secret" not in key.lower() for key in payload)


def test_multi_user_profiles_have_independent_state(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    monkeypatch.setattr(user_profile, "MULTI_USER_ENABLED", True)
    alice = {"X-Lumena-User": "telegram:alice"}
    bob = {"X-Lumena-User": "telegram:bob"}

    assert client.post("/api/onboarding/start", headers=alice).status_code == 200
    assert client.post(
        "/api/onboarding/progress", json={"step": "orientation"}, headers=alice,
    ).status_code == 200

    assert "orientation" in client.get("/api/onboarding/status", headers=alice).json()["completed_steps"]
    assert client.get("/api/onboarding/status", headers=bob).json()["completed_steps"] == []
    assert (tmp_path / "users" / "telegram__alice" / "onboarding" / "state.json").exists()
    assert (tmp_path / "users" / "telegram__bob" / "onboarding" / "state.json").exists() is False


def test_server_registers_onboarding_routes() -> None:
    from web.server import app
    paths = {route.path for route in app.routes}
    assert "/api/onboarding/status" in paths
    assert "/api/onboarding/reset" in paths


def test_optional_skip_does_not_dismiss_whole_tour(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    client.post("/api/onboarding/start")
    response = client.post(
        "/api/onboarding/skip",
        json={"steps": ["work_progress"], "dismiss": False},
    )
    assert response.status_code == 200
    assert response.json()["tour_status"] == "in_progress"
    assert response.json()["dismissed_at"] is None


def test_skip_default_remains_full_dismissal(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    client.post("/api/onboarding/start")
    response = client.post("/api/onboarding/skip", json={"steps": ["orientation"]})
    assert response.json()["tour_status"] == "skipped"
    assert response.json()["dismissed_at"]


def test_goal_route_persists_adaptive_path_and_rejects_unknown_goal(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    client.post("/api/onboarding/start")
    selected = client.post("/api/onboarding/goal", json={"goal": "file"})
    assert selected.status_code == 200
    assert selected.json()["selected_goal"] == "file"
    assert selected.json()["current_step"] == "files"
    assert client.get("/api/onboarding/status").json()["selected_goal"] == "file"
    assert client.post("/api/onboarding/goal", json={"goal": "unknown"}).status_code == 400
