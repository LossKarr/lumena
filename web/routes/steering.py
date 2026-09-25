"""Authenticated HTTP contract for live work steering."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator
import json

from src.runtime.steering_dispatcher import SteeringDispatcher
from src.runtime.mission_steering import fanout_command
from src.runtime.task_steering_store import SteeringConflict, SteeringQueueFull, TaskSteeringStore
from src.runtime.work_registry import ActiveWorkRegistry
from src.runtime.work_target_resolver import WorkTargetResolver
from web.routes import deps

router = APIRouter()
_LOCAL_OWNER = "local:owner"


class SteeringRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=8000)
    kind: Literal[
        "add_constraint", "replace_constraint", "remove_constraint", "reprioritize",
    ] = "add_constraint"
    delivery_policy: Literal["next_checkpoint", "urgent_safe_boundary"] = "next_checkpoint"
    idempotency_key: Optional[str] = Field(default=None, min_length=8, max_length=160)
    supersedes: List[str] = Field(default_factory=list, max_length=50)
    scope: Dict[str, Any] = Field(default_factory=dict)
    conversation_id: Optional[str] = Field(default=None, max_length=160)

    @field_validator("scope")
    @classmethod
    def validate_scope(cls, value: Dict[str, Any]) -> Dict[str, Any]:
        if set(value) - {"files", "workers", "global"}:
            raise ValueError("unsupported_scope_field")
        if len(json.dumps(value, ensure_ascii=False)) > 8000:
            raise ValueError("scope_too_large")
        for key in ("files", "workers"):
            items = value.get(key, [])
            if not isinstance(items, list) or len(items) > 100:
                raise ValueError(f"invalid_scope_{key}")
            if any(not isinstance(item, str) or not item.strip() or len(item) > 500 for item in items):
                raise ValueError(f"invalid_scope_{key}")
        return value


def _orchestrator():
    orchestrator = deps.get_task_orchestrator()
    if orchestrator is None:
        raise HTTPException(status_code=503, detail={"code": "runtime_unavailable"})
    return orchestrator


def _authorized_target(
    task_id: str,
    conversation_id: Optional[str] = None,
    *,
    allow_terminal: bool = False,
):
    result = WorkTargetResolver(_orchestrator()).resolve(
        owner_user_id=_LOCAL_OWNER,
        conversation_id=conversation_id,
        preferred_task_id=task_id,
    )
    if result.code == "resolved" or (allow_terminal and result.code == "task_terminal"):
        return result.target
    status = 404 if result.code == "task_not_found" else (403 if result.code == "owner_mismatch" else 409)
    raise HTTPException(status_code=status, detail={"code": result.code, "candidates": list(result.candidates)})


def _public_command(command: Dict[str, Any]) -> Dict[str, Any]:
    hidden = {"request_fingerprint", "requester_user_id", "owner_user_id"}
    return {key: value for key, value in command.items() if key not in hidden}


def _publish(stage: str, command: Dict[str, Any]) -> None:
    if not getattr(deps, "TELEMETRY_AVAILABLE", False):
        return
    try:
        deps.publish_trace(
            stage=stage,
            status=str(command.get("status") or "ok"),
            mode="agent",
            task_id=str(command.get("target_task_id") or ""),
            summary=(
                f"command={command.get('command_id')} sequence={command.get('sequence')} "
                f"policy={command.get('delivery_policy')}"
            ),
        )
    except Exception:
        pass


@router.post("/api/tasks/{task_id}/steering", dependencies=[Depends(deps.verify_admin_token)])
async def create_steering(task_id: str, body: SteeringRequest):
    _authorized_target(task_id, body.conversation_id, allow_terminal=True)
    try:
        command = SteeringDispatcher(_orchestrator()).enqueue(
            task_id,
            body.kind,
            text=body.text,
            delivery_policy=body.delivery_policy,
            idempotency_key=body.idempotency_key,
            source_channel="web",
            source_conversation_id=body.conversation_id or "",
            requester_user_id=_LOCAL_OWNER,
            owner_user_id=_LOCAL_OWNER,
            supersedes=body.supersedes,
            scope=body.scope,
        )
    except SteeringConflict as exc:
        raise HTTPException(status_code=409, detail={"code": str(exc)}) from exc
    except SteeringQueueFull as exc:
        raise HTTPException(status_code=429, detail={"code": str(exc)}) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    _publish("steering_received", command)
    task = _orchestrator().get_task(task_id) or {}
    fanout = None
    if (task.get("metadata") or {}).get("kind") == "mission" and not (task.get("metadata") or {}).get("parent_id"):
        fanout = fanout_command(_orchestrator(), task_id, command)
    return {
        "accepted": command.get("status") != "late",
        **_public_command(command),
        "fanout": fanout,
    }


@router.get("/api/tasks/{task_id}/steering", dependencies=[Depends(deps.verify_admin_token)])
async def list_steering(
    task_id: str,
    conversation_id: Optional[str] = Query(default=None, max_length=160),
):
    _authorized_target(task_id, conversation_id, allow_terminal=True)
    return {"task_id": task_id, "commands": [
        _public_command(item) for item in TaskSteeringStore(_orchestrator()).list(task_id)
    ]}


@router.get("/api/tasks/{task_id}/steering/{command_id}", dependencies=[Depends(deps.verify_admin_token)])
async def get_steering(task_id: str, command_id: str):
    _authorized_target(task_id, allow_terminal=True)
    command = TaskSteeringStore(_orchestrator()).get(task_id, command_id)
    if command is None:
        raise HTTPException(status_code=404, detail={"code": "command_not_found"})
    return _public_command(command)


@router.post(
    "/api/tasks/{task_id}/steering/{command_id}/cancel",
    dependencies=[Depends(deps.verify_admin_token)],
)
async def cancel_steering(task_id: str, command_id: str):
    _authorized_target(task_id)
    try:
        command = TaskSteeringStore(_orchestrator()).transition(task_id, command_id, "cancelled")
    except KeyError as exc:
        raise HTTPException(status_code=404, detail={"code": "command_not_found"}) from exc
    _publish("steering_cancelled", command)
    return _public_command(command)


@router.get("/api/work/active", dependencies=[Depends(deps.verify_admin_token)])
async def active_work(conversation_id: Optional[str] = Query(default=None, max_length=160)):
    registry = ActiveWorkRegistry(
        _orchestrator(), conversation_id=conversation_id, owner_user_id=_LOCAL_OWNER,
    )
    return {"work": [asdict(registry.snapshot(task_id)) for task_id in registry.active_ids()]}


@router.post("/api/missions/{mission_id}/steering", dependencies=[Depends(deps.verify_admin_token)])
async def create_mission_steering(mission_id: str, body: SteeringRequest):
    return await create_steering(mission_id, body)


@router.get("/api/missions/{mission_id}/steering", dependencies=[Depends(deps.verify_admin_token)])
async def list_mission_steering(mission_id: str):
    return await list_steering(mission_id, None)
