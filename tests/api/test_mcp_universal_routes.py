from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI

from src.mcp.connection_spec import AuthKind, AuthSpec
from src.mcp.official_registry import (
    OfficialRegistryRecord,
    RegistryRemote,
)


def _app(router, *, authenticated: bool) -> FastAPI:
    from web.routes import deps

    app = FastAPI()
    app.include_router(router)
    if authenticated:
        app.dependency_overrides[deps.verify_admin_token] = lambda: None
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize("route_name,path,method", [
    ("registry", "/api/mcp/registry/search", "GET"),
    (
        "schema",
        "/api/mcp/schema-drift/server/accept",
        "POST",
    ),
    ("oauth", "/api/mcp/oauth/server/start", "POST"),
])
async def test_new_mcp_admin_routes_require_auth(
    route_name, path, method, monkeypatch
):
    from web.routes import mcp_oauth, mcp_registry, mcp_schema

    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "real-admin-secret")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    routers = {
        "registry": mcp_registry.router,
        "schema": mcp_schema.router,
        "oauth": mcp_oauth.router,
    }
    app = _app(routers[route_name], authenticated=False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.request(method, path, json={})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_registry_route_returns_only_sanitized_public_fields(monkeypatch):
    from web.routes import mcp_registry

    record = OfficialRegistryRecord(
        canonical_name="example/weather",
        publisher_namespace="example",
        display_name="Weather <Server>",
        description="Weather data",
        version="1.0.0",
        status="active",
        is_latest=True,
        published_at=None,
        updated_at=None,
        packages=(),
        remotes=(RegistryRemote("streamable-http", "https://mcp.example.test"),),
    )
    monkeypatch.setattr(
        mcp_registry,
        "_client",
        SimpleNamespace(search=lambda query, limit: (record,)),
    )
    app = _app(mcp_registry.router, authenticated=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/api/mcp/registry/search?q=weather")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["target"] == "https://mcp.example.test"
    assert "authorization" not in str(response.json()).lower()


@pytest.mark.asyncio
async def test_schema_drift_dry_run_still_validates_exact_fingerprint(monkeypatch):
    from web.routes import mcp_schema

    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    app = _app(mcp_schema.router, authenticated=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        invalid = await client.post(
            "/api/mcp/schema-drift/server/accept",
            json={
                "confirmed": True,
                "confirmation_phrase": "server",
                "fingerprint": "bad",
            },
        )
        valid = await client.post(
            "/api/mcp/schema-drift/server/accept",
            json={
                "confirmed": True,
                "confirmation_phrase": "server",
                "fingerprint": "a" * 64,
            },
        )
    assert invalid.status_code == 400
    assert valid.json() == {
        "accepted": False,
        "dry_run": True,
        "server_id": "server",
    }


@pytest.mark.asyncio
async def test_oauth_start_requires_exact_human_confirmation(monkeypatch):
    from web.routes import mcp_oauth

    monkeypatch.setattr(
        mcp_oauth,
        "_entry_auth",
        lambda _server_id: AuthSpec(
            kind=AuthKind.OAUTH2,
            secret_keys=("OAUTH_ACCESS_TOKEN",),
            metadata_url="https://auth.example.test/.well-known/oauth-authorization-server",
            registration_mode="pre_registered",
            client_id="lumena-desktop",
        ),
    )
    app = _app(mcp_oauth.router, authenticated=True)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/api/mcp/oauth/server/start",
            json={"confirmed": True, "confirmation_phrase": "wrong"},
        )
    assert response.status_code == 400
    assert response.json()["detail"] == "confirmation_required"
