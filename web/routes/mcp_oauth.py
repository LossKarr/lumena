"""Human-consent OAuth bridge for remote MCP servers."""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse

from src.mcp.connection_spec import AuthKind, MCPConnectionSpec
from src.mcp.oauth_manager import MCPOAuthManager
from web.routes.deps import verify_admin_token


router = APIRouter()
_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_manager: Optional[MCPOAuthManager] = None


def _dependencies():
    from web.routes import deps
    return (
        getattr(deps, "_MCP_SERVER_CATALOG_SINGLETON", None),
        getattr(deps, "_MCP_CREDENTIALS_SERVICE_SINGLETON", None),
    )


def _oauth_manager() -> MCPOAuthManager:
    global _manager
    _, credentials = _dependencies()
    if credentials is None:
        raise HTTPException(status_code=503, detail="mcp_credentials_unavailable")
    if _manager is None:
        _manager = MCPOAuthManager(credentials_service=credentials)
    return _manager


def _entry_auth(server_id: str):
    catalog, _ = _dependencies()
    if catalog is None:
        raise HTTPException(status_code=503, detail="mcp_catalog_unavailable")
    try:
        entry = catalog.get_server(server_id)
        spec = MCPConnectionSpec.from_dict(entry.connection_spec)
    except Exception:
        raise HTTPException(status_code=404, detail="mcp_oauth_not_configured")
    if spec.auth.kind != AuthKind.OAUTH2:
        raise HTTPException(status_code=409, detail="mcp_oauth_not_configured")
    return spec.auth


@router.post(
    "/api/mcp/oauth/{server_id}/start",
    dependencies=[Depends(verify_admin_token)],
)
async def start_mcp_oauth(
    server_id: str,
    body: Optional[Dict[str, Any]] = Body(default=None),
):
    body = body or {}
    if not _SERVER_ID_RE.fullmatch(server_id):
        raise HTTPException(status_code=400, detail="server_id_invalid")
    if body.get("confirmed") is not True or body.get("confirmation_phrase") != server_id:
        raise HTTPException(status_code=400, detail="confirmation_required")
    auth = _entry_auth(server_id)
    port = int(os.getenv("LUMENA_PORT", "8080"))
    redirect_uri = f"http://127.0.0.1:{port}/api/mcp/oauth/callback"
    try:
        result = _oauth_manager().begin(
            server_id=server_id,
            metadata_url=auth.metadata_url,
            client_id=auth.client_id,
            redirect_uri=redirect_uri,
            scopes=auth.scopes,
            access_secret_key=auth.secret_keys[0],
        )
    except Exception:
        raise HTTPException(status_code=502, detail="mcp_oauth_start_failed")
    return {
        "authorization_url": result.authorization_url,
        "state": result.state,
        "expires_at": result.expires_at,
        "server_id": server_id,
    }


@router.get("/api/mcp/oauth/callback", response_class=HTMLResponse)
async def complete_mcp_oauth(
    state: str = Query(min_length=16, max_length=512),
    code: str = Query(min_length=1, max_length=4096),
):
    try:
        server_id, _ = _oauth_manager().complete(state=state, code=code)
    except Exception:
        return HTMLResponse(
            "<h1>Connexion MCP refusée</h1><p>Le code est invalide ou expiré. Vous pouvez fermer cette fenêtre.</p>",
            status_code=400,
            headers={"Cache-Control": "no-store"},
        )
    return HTMLResponse(
        f"<h1>Connexion MCP terminée</h1><p>{server_id} est authentifié. Vous pouvez fermer cette fenêtre.</p>",
        headers={"Cache-Control": "no-store"},
    )
