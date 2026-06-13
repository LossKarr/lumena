"""Tests Phase I-6 — Routes API Library config (8 endpoints).

Couvre :
  - Auth obligatoire sur toutes les routes
  - GET /schema retourne curated/persisted/none
  - GET /config-status renvoie statut sans valeurs
  - PUT /secrets stocke (privacy : value jamais exposée)
  - PUT /config stocke
  - DELETE /secrets et /config
  - GET /ready vrai uniquement quand tout est set
  - POST /detect-schema relance la cascade et persiste
  - Validations server_id + key_name
  - 503 si services non initialisés
"""
from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI


def _make_app(*, with_auth_override: bool = True) -> FastAPI:
    from web.routes import deps, mcp
    app = FastAPI()
    app.include_router(mcp.router)
    if with_auth_override:
        app.dependency_overrides[deps.verify_admin_token] = lambda: None
    return app


def _setup_singletons(tmp_path: Path, monkeypatch):
    """Initialise les 3 singletons Phase I-6 sur tmp_path et patche les
    accesseurs deps pour qu'ils retournent ces instances."""
    from src.services.secrets_service import SecretsService
    from src.mcp.credentials_service import MCPCredentialsService
    from src.mcp.config_service import MCPConfigService
    from web.routes import deps as web_deps

    secrets = SecretsService(
        secrets_dir=tmp_path / "secrets",
        master_key_path=tmp_path / "master.key",
    )
    creds = MCPCredentialsService(secrets)
    config = MCPConfigService(config_root=tmp_path / "mcp_config")
    monkeypatch.setattr(web_deps, "_MCP_CREDENTIALS_SERVICE_SINGLETON", creds, raising=False)
    monkeypatch.setattr(web_deps, "_MCP_CONFIG_SERVICE_SINGLETON", config, raising=False)
    return creds, config


_ALL_LIBRARY_PATHS = [
    ("GET",    "/api/mcp/library/slack/schema"),
    ("GET",    "/api/mcp/library/slack/config-status"),
    ("PUT",    "/api/mcp/library/slack/secrets/SOMEKEY_TOKEN"),
    ("DELETE", "/api/mcp/library/slack/secrets/SOMEKEY_TOKEN"),
    ("PUT",    "/api/mcp/library/slack/config/SOME_CONFIG"),
    ("DELETE", "/api/mcp/library/slack/config/SOME_CONFIG"),
    ("GET",    "/api/mcp/library/slack/ready"),
    ("POST",   "/api/mcp/library/slack/detect-schema"),
]


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Auth obligatoire (8 routes Library)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", _ALL_LIBRARY_PATHS)
async def test_library_route_requires_admin_token_401(method, path, monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app = _make_app(with_auth_override=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.request(method, path)
    assert resp.status_code == 401


@pytest.mark.asyncio
@pytest.mark.parametrize("method,path", _ALL_LIBRARY_PATHS)
async def test_library_route_bad_token_403(method, path, monkeypatch):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    app = _make_app(with_auth_override=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.request(method, path, headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 403


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — GET /schema
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_get_schema_curated_slack(tmp_path, monkeypatch):
    _setup_singletons(tmp_path, monkeypatch)
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/library/slack/schema")
    assert resp.status_code == 200
    d = resp.json()
    assert d["server_id"] == "slack"
    assert d["source"] in ("persisted", "curated")
    assert d["schema"] is not None
    names = [f["name"] for f in d["schema"]["fields"]]
    assert "SLACK_BOT_TOKEN" in names


@pytest.mark.asyncio
async def test_get_schema_unknown_server(tmp_path, monkeypatch):
    _setup_singletons(tmp_path, monkeypatch)
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/library/unknown-mcp-xyz/schema")
    assert resp.status_code == 200
    d = resp.json()
    assert d["schema"] is None
    assert d["source"] == "none"


@pytest.mark.asyncio
async def test_schema_invalid_server_id_400(tmp_path, monkeypatch):
    _setup_singletons(tmp_path, monkeypatch)
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/library/INVALID!/schema")
    assert resp.status_code == 400


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — GET /config-status
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_status_slack_all_missing(tmp_path, monkeypatch):
    _setup_singletons(tmp_path, monkeypatch)
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/library/slack/config-status")
    assert resp.status_code == 200
    d = resp.json()
    assert d["ready"] is False
    assert set(d["status"].keys()) == {"SLACK_BOT_TOKEN", "SLACK_TEAM_ID"}
    assert all(v == "missing" for v in d["status"].values())
    assert sorted(d["missing"]) == ["SLACK_BOT_TOKEN", "SLACK_TEAM_ID"]


@pytest.mark.asyncio
async def test_status_503_when_services_down(monkeypatch):
    from web.routes import deps as web_deps
    monkeypatch.setattr(web_deps, "_MCP_CREDENTIALS_SERVICE_SINGLETON", None, raising=False)
    monkeypatch.setattr(web_deps, "_MCP_CONFIG_SERVICE_SINGLETON", None, raising=False)
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/library/slack/config-status")
    assert resp.status_code == 503


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — PUT /secrets + Privacy (value JAMAIS dans la réponse)
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_put_secret_stores_and_never_returns_value(tmp_path, monkeypatch):
    creds, _ = _setup_singletons(tmp_path, monkeypatch)
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        secret_value = "xoxb-VERY-SECRET-12345"
        resp = await client.put(
            "/api/mcp/library/slack/secrets/SLACK_BOT_TOKEN",
            json={"value": secret_value},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"ok": True, "is_set": True}
    assert secret_value not in resp.text
    # Server-side : la valeur EST stockée
    assert creds.get("slack", "SLACK_BOT_TOKEN") == secret_value


@pytest.mark.asyncio
async def test_put_secret_empty_string_deletes(tmp_path, monkeypatch):
    creds, _ = _setup_singletons(tmp_path, monkeypatch)
    creds.set("slack", "SLACK_BOT_TOKEN", "xoxb-pre-existing")
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(
            "/api/mcp/library/slack/secrets/SLACK_BOT_TOKEN",
            json={"value": ""},
        )
    assert resp.status_code == 200
    assert resp.json()["is_set"] is False
    assert creds.has("slack", "SLACK_BOT_TOKEN") is False


@pytest.mark.asyncio
async def test_put_secret_missing_body_400(tmp_path, monkeypatch):
    _setup_singletons(tmp_path, monkeypatch)
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(
            "/api/mcp/library/slack/secrets/SLACK_BOT_TOKEN", json={},
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_put_secret_invalid_key_400(tmp_path, monkeypatch):
    _setup_singletons(tmp_path, monkeypatch)
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.put(
            "/api/mcp/library/slack/secrets/lowercase",
            json={"value": "x"},
        )
    assert resp.status_code == 400


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — DELETE secrets / config
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_delete_secret(tmp_path, monkeypatch):
    creds, _ = _setup_singletons(tmp_path, monkeypatch)
    creds.set("slack", "SLACK_BOT_TOKEN", "xoxb-")
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.delete(
            "/api/mcp/library/slack/secrets/SLACK_BOT_TOKEN"
        )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "removed": True}
    assert creds.has("slack", "SLACK_BOT_TOKEN") is False


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — PUT /config + GET /ready
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_put_config_then_ready_partial(tmp_path, monkeypatch):
    creds, config = _setup_singletons(tmp_path, monkeypatch)
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # Set le team_id mais pas le token
        await client.put(
            "/api/mcp/library/slack/config/SLACK_TEAM_ID",
            json={"value": "T01234"},
        )
        resp = await client.get("/api/mcp/library/slack/ready")
    assert resp.status_code == 200
    d = resp.json()
    assert d["ready"] is False
    assert "SLACK_BOT_TOKEN" in d["missing_secrets"]
    assert d["missing_config"] == []


@pytest.mark.asyncio
async def test_full_ready_when_all_set(tmp_path, monkeypatch):
    creds, config = _setup_singletons(tmp_path, monkeypatch)
    creds.set("slack", "SLACK_BOT_TOKEN", "xoxb-")
    config.set("slack", "SLACK_TEAM_ID", "T01")
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/mcp/library/slack/ready")
    assert resp.status_code == 200
    d = resp.json()
    assert d["ready"] is True
    assert d["missing_secrets"] == []
    assert d["missing_config"] == []


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — POST /detect-schema
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_detect_schema_via_intent(tmp_path, monkeypatch):
    _setup_singletons(tmp_path, monkeypatch)
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/library/slack/detect-schema",
            json={"intent": "slack"},
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["schema"] is not None
    assert d["detected_from"] == "curated"


@pytest.mark.asyncio
async def test_detect_schema_via_user_snippet(tmp_path, monkeypatch):
    _setup_singletons(tmp_path, monkeypatch)
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/library/custom-mcp/detect-schema",
            json={"user_snippet": "MY_API_TOKEN=xx\nMY_BASE_URL=https://x"},
        )
    assert resp.status_code == 200
    d = resp.json()
    assert d["detected_from"] == "user"
    names = [f["name"] for f in d["schema"]["fields"]]
    assert "MY_API_TOKEN" in names
    assert "MY_BASE_URL" in names


@pytest.mark.asyncio
async def test_detect_schema_empty_body_returns_none(tmp_path, monkeypatch):
    _setup_singletons(tmp_path, monkeypatch)
    app = _make_app()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/mcp/library/random/detect-schema", json={},
        )
    assert resp.status_code == 200
    assert resp.json()["schema"] is None
