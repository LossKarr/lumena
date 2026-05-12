"""Persistent conversation session routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse

from web.routes import deps

router = APIRouter()


def _require_store():
    store = getattr(deps, "_SESSION_STORE", None)
    if store is None:
        raise HTTPException(status_code=503, detail="session store unavailable")
    return store


@router.get("/api/sessions", dependencies=[Depends(deps.verify_admin_token)])
async def list_sessions(
    limit: int = 100,
    offset: int = 0,
    status: str = "",
    channel: str = "",
    q: str = "",
    include_archived: bool = False,
):
    """List persisted user conversation sessions."""
    store = _require_store()
    payload = store.list_sessions(
        limit=limit,
        offset=offset,
        status=(status or "").strip(),
        channel=(channel or "").strip(),
        query=(q or "").strip(),
        include_archived=include_archived,
    )
    payload["stats"] = store.stats()
    return payload


@router.post("/api/sessions/{conversation_id}/archive", dependencies=[Depends(deps.verify_admin_token)])
async def archive_session(conversation_id: str, archived: bool = True):
    store = _require_store()
    ok = store.archive_session(conversation_id, archived=archived)
    if not ok:
        return JSONResponse(status_code=404, content={"success": False, "detail": "session not found"})
    return {"success": True, "conversation_id": conversation_id, "archived": archived}


@router.post("/api/sessions/{conversation_id}/resume", dependencies=[Depends(deps.verify_admin_token)])
async def resume_session(conversation_id: str):
    store = _require_store()
    detail = store.get_session(conversation_id)
    if not detail:
        return JSONResponse(status_code=404, content={"success": False, "detail": "session not found"})
    return {
        "success": True,
        "conversation_id": conversation_id,
        "session": detail.get("session"),
        "messages": detail.get("messages", []),
    }


@router.delete("/api/sessions/{conversation_id}", dependencies=[Depends(deps.verify_admin_token)])
async def delete_session(conversation_id: str):
    store = _require_store()
    ok = store.delete_session(conversation_id)
    if not ok:
        return JSONResponse(status_code=404, content={"success": False, "detail": "session not found"})
    return {"success": True, "conversation_id": conversation_id}
