"""Small admin surface for approving one exact pending MCP schema drift."""
from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException

from src.mcp.schema_guard import MCPSchemaGuard
from src.utils.paths import DATA_DIR
from web.routes.deps import verify_admin_token


router = APIRouter()
_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,63}$")
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


@router.post(
    "/api/mcp/schema-drift/{server_id}/accept",
    dependencies=[Depends(verify_admin_token)],
)
async def accept_mcp_schema_drift(
    server_id: str,
    body: Optional[Dict[str, Any]] = Body(default=None),
):
    body = body or {}
    if not _SERVER_ID_RE.fullmatch(server_id) or ".." in server_id:
        raise HTTPException(status_code=400, detail="server_id_invalid")
    if body.get("confirmed") is not True:
        raise HTTPException(status_code=400, detail="confirmation_required")
    if body.get("confirmation_phrase") != server_id:
        raise HTTPException(status_code=400, detail="confirmation_phrase_invalid")
    fingerprint = body.get("fingerprint")
    if not isinstance(fingerprint, str) or not _FINGERPRINT_RE.fullmatch(fingerprint):
        raise HTTPException(status_code=400, detail="fingerprint_invalid")
    if os.getenv("LUMENA_MCP_LIVE", "").strip().lower() not in {
        "1", "true", "yes", "on",
    }:
        return {"accepted": False, "dry_run": True, "server_id": server_id}
    try:
        result = MCPSchemaGuard(
            DATA_DIR / "mcp_schema_guard"
        ).accept_pending(server_id, fingerprint)
    except Exception:
        raise HTTPException(status_code=409, detail="schema_drift_accept_failed")
    return {
        "accepted": True,
        "dry_run": False,
        "server_id": server_id,
        "fingerprint": result.fingerprint,
        "status": result.status,
    }
