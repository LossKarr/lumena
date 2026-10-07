from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import deps
from web.routes import personal_models as routes


class _Plane:
    def status(self):
        return {"principal_model": "deepseek-flash", "personal_model": None}

    def health(self):
        return {"healthy": False, "blockers": ["personal_learning_disabled"]}

    def experience_stats(self):
        return {"total": 0, "counts": {}}

    def training_settings(self):
        return {"requested": {"enabled": False}, "effective": {"enabled": False}, "provenance": "test"}

    def recommendations(self):
        return []

    def audit_trail(self, limit):
        return []

    def update_training_settings(self, changes, actor):
        return {"settings": changes, "proof_id": "audit_test"}

    def approval_preview(self, action, resource, actor):
        return {"approval_token": "x" * 43, "action": action, "resource": resource}

    def list_backups(self):
        return [{"name": "personal-model-test.lumena-model.zip", "sha256": "a" * 64}]

    def migration_sources(self):
        return [{"path": "training_pool/history.jsonl", "size_bytes": 12}]


def _client(monkeypatch, authenticated=True):
    app = FastAPI()
    app.include_router(routes.router)
    monkeypatch.setattr(routes, "get_personal_model_control_plane", lambda: _Plane())
    if authenticated:
        app.dependency_overrides[deps.verify_admin_token] = lambda: None
    return TestClient(app)


def test_personal_model_routes_require_admin_token(monkeypatch) -> None:
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "secret")
    response = _client(monkeypatch, authenticated=False).get("/api/personal-model/status")
    assert response.status_code == 401


def test_status_and_settings_contract(monkeypatch) -> None:
    client = _client(monkeypatch)
    assert client.get("/api/personal-model/status").json()["principal_model"] == "deepseek-flash"
    response = client.patch("/api/personal-model/settings", json={"changes": {"enabled": True}})
    assert response.status_code == 200
    assert response.json()["proof_id"] == "audit_test"


def test_strict_api_rejects_unknown_fields(monkeypatch) -> None:
    response = _client(monkeypatch).post("/api/personal-model/approvals", json={"action": "cancel_training", "resource": "run", "confirmed": True})
    assert response.status_code == 422


def test_data_inventory_routes_expose_bounded_metadata(monkeypatch) -> None:
    client = _client(monkeypatch)
    assert client.get("/api/personal-model/backups").json()["backups"][0]["name"].endswith(".lumena-model.zip")
    assert client.get("/api/personal-model/migration/sources").json()["sources"][0]["path"] == "training_pool/history.jsonl"
