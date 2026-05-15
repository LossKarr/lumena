"""Task management and daemon activity routes."""
from __future__ import annotations

import json as _json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from loguru import logger

from web.routes import deps
from web.routes.schemas import TaskStartRequest
from web.routes.system import _task_orchestrator_enabled
from web.routes.chat import _normalize_channel, _get_session_state

from src.utils.paths import ROOT_DIR, OPS_DIR

_PROJECT_ROOT = ROOT_DIR

router = APIRouter()


@router.post("/api/tasks/start", dependencies=[Depends(deps.verify_admin_token)])
async def start_task(request: TaskStartRequest):
    """Cree une tache orchestrateur pour un flux long."""
    if not _task_orchestrator_enabled():
        raise HTTPException(status_code=503, detail="task orchestrator disabled")
    try:
        record = deps._TASK_ORCHESTRATOR.start_task(
            conversation_id=request.conversation_id,
            channel=_normalize_channel(request.channel),
            message_preview=request.message_preview,
            metadata=request.metadata,
            task_id=request.task_id,
        )
        return {"success": True, "task": record.to_dict()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/api/tasks/{task_id}/cancel", dependencies=[Depends(deps.verify_admin_token)])
async def cancel_task(task_id: str):
    if not _task_orchestrator_enabled():
        raise HTTPException(status_code=503, detail="task orchestrator disabled")
    try:
        payload = deps._TASK_ORCHESTRATOR.cancel_task(task_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not payload.get("success"):
        return JSONResponse(status_code=404, content=payload)
    return JSONResponse(content=payload)


@router.post("/api/tasks/{task_id}/resume", dependencies=[Depends(deps.verify_admin_token)])
async def resume_task(task_id: str):
    if not _task_orchestrator_enabled():
        raise HTTPException(status_code=503, detail="task orchestrator disabled")
    try:
        payload = deps._TASK_ORCHESTRATOR.resume_task(task_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not payload.get("success"):
        return JSONResponse(status_code=404, content=payload)
    return JSONResponse(content=payload)


@router.get("/api/tasks", dependencies=[Depends(deps.verify_admin_token)])
async def list_tasks(limit: int = 200, state: str = ""):
    """Liste les taches planifiees conversationnelles du scheduler."""
    try:
        from src.tools.task_scheduler import _load_conv_tasks
        data = _load_conv_tasks()
        raw = data.get("tasks", {})
        tasks = []
        for ctask_id, meta in raw.items():
            t = dict(meta)
            t["task_id"] = ctask_id
            t["type"] = "scheduler"
            tasks.append(t)
        tasks = tasks[:limit]
        return {"success": True, "tasks": tasks, "total": len(tasks)}
    except Exception as e:
        logger.warning("list_tasks: failed to load scheduler tasks: {}", e)
        return {"success": True, "tasks": [], "total": 0}


@router.get("/api/daemon/activity", dependencies=[Depends(deps.verify_admin_token)])
async def get_daemon_activity():
    """Retourne la derniere execution de chaque handler ops du daemon + incidents."""
    _ops_dir = OPS_DIR
    results = {}
    metrics_path = _ops_dir / "metrics.jsonl"
    if metrics_path.exists():
        try:
            for raw_line in metrics_path.read_text(encoding="utf-8", errors="replace").splitlines():
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    entry = _json.loads(raw_line)
                    h = entry.get("handler") or entry.get("name") or ""
                    if h:
                        results[h] = entry
                except Exception:
                    pass
        except Exception as e:
            logger.warning("daemon/activity: metrics read error: {}", e)
    ops_extra = {}
    ops_state_path = _ops_dir / "ops_state.json"
    if ops_state_path.exists():
        try:
            ops_state = _json.loads(ops_state_path.read_text(encoding="utf-8", errors="replace"))
            ops_extra = {
                "incidents_today": ops_state.get("incidents_today", []),
                "daily_counters": ops_state.get("daily_counters", {}),
                "uptime_start": ops_state.get("uptime_start", ""),
                "saved_at": ops_state.get("saved_at", ""),
            }
        except Exception as e:
            logger.warning("daemon/activity: ops_state read error: {}", e)
    handlers = []
    for h, entry in results.items():
        data = entry.get("data") or {}
        handlers.append({
            "handler": h,
            "timestamp": entry.get("timestamp", ""),
            "success": data.get("success", True),
            "alerts": data.get("alerts") or [],
            "summary": data.get("reason") or data.get("summary") or "",
            "score_percent": data.get("score_percent"),
            "scheduler_pending": data.get("scheduler_pending"),
            "scheduler_overdue": data.get("scheduler_overdue"),
            "uptime_hours": data.get("uptime_hours"),
        })
    handlers.sort(key=lambda x: x["timestamp"], reverse=True)
    return {"success": True, "handlers": handlers, "ops": ops_extra, "total": len(handlers)}


@router.get("/api/tasks/{task_id}", dependencies=[Depends(deps.verify_admin_token)])
async def get_task(task_id: str):
    if not _task_orchestrator_enabled():
        raise HTTPException(status_code=503, detail="task orchestrator disabled")
    try:
        task = deps._TASK_ORCHESTRATOR.get_task(task_id)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    return {"success": True, "task": task}


@router.get("/api/sessions/{conversation_id}", dependencies=[Depends(deps.verify_admin_token)])
async def get_session(conversation_id: str, limit: int = 50):
    """Retourne les details connus pour une conversation omnicanal."""
    bounded_limit = max(1, min(int(limit), 200))
    try:
        tasks = []
        if _task_orchestrator_enabled():
            tasks = deps._TASK_ORCHESTRATOR.get_conversation_tasks(conversation_id, limit=bounded_limit)
        session_state = _get_session_state(conversation_id)
        session_detail = None
        store = getattr(deps, "_SESSION_STORE", None)
        if store is not None:
            session_detail = store.get_session(
                conversation_id,
                message_limit=bounded_limit,
                event_limit=bounded_limit,
            )
        payload = {
            "conversation_id": conversation_id,
            "count": len(tasks),
            "tasks": tasks,
            "session_state": session_state,
        }
        if session_detail:
            payload.update(
                {
                    "session": session_detail.get("session"),
                    "messages": session_detail.get("messages", []),
                    "events": session_detail.get("events", []),
                }
            )
        return payload
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
