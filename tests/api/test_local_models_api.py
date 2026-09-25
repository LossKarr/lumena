from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import deps
from web.routes import local_models as routes


class FakeClient:
    async def list_installed(self):
        return []

    async def list_running(self):
        return []


class FakeManager:
    def __init__(self):
        self.client = FakeClient()

    async def status(self):
        return {"available": True, "active_jobs": 0}

    async def installed(self):
        return []

    async def search(self, query, **kwargs):
        return {"models": [], "sources": {"ollama": {"available": True}}, "cached": False, "stale": False}

    async def recommend(self, query, **kwargs):
        return {"recommendations": []}


def app(monkeypatch, *, authenticated=True):
    api = FastAPI()
    api.include_router(routes.router)
    monkeypatch.setattr(routes, "_manager", lambda: FakeManager())
    if authenticated:
        api.dependency_overrides[deps.verify_admin_token] = lambda: None
    return TestClient(api)


def test_routes_require_admin_auth(monkeypatch):
    response = app(monkeypatch, authenticated=False).get("/api/local-models/status")
    assert response.status_code in {401, 403, 503}


def test_search_contract_is_bounded_and_authenticated(monkeypatch):
    client = app(monkeypatch)
    assert client.get("/api/local-models/search?q=code&limit=30").status_code == 200
    assert client.get("/api/local-models/search?limit=101").status_code == 422


def test_mutation_schema_rejects_unknown_fields(monkeypatch):
    response = app(monkeypatch).post(
        "/api/local-models/disable", json={"source": "ollama", "ref": "qwen3:8b", "confirmed": True}
    )
    assert response.status_code == 422


def test_resolve_accepts_huggingface_slash_in_query_not_path(monkeypatch):
    response = app(monkeypatch).get(
        "/api/local-models/resolve", params={"source": "huggingface", "ref": "owner/model-GGUF"}
    )
    assert response.status_code == 200
    assert response.json()["reference"]["canonical"] == "owner/model-GGUF"


def test_delete_rejects_forged_confirmation_boolean(monkeypatch):
    response = app(monkeypatch).post(
        "/api/local-models/delete",
        json={
            "source": "ollama",
            "ref": "qwen3:8b",
            "ticket": "x" * 43,
            "confirmed": True,
        },
    )
    assert response.status_code == 422
