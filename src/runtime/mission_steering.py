"""Idempotent steering fan-out from a mission lead to relevant workers."""
from __future__ import annotations

from typing import Any, Dict, List, Set

from src.subagents.mission_amendments import request_scope_amendment
from .steering_dispatcher import SteeringDispatcher
from .task_steering_store import TaskSteeringStore

_ACTIVE = {"queued", "running", "waiting_io", "checkpointed"}
_ACTIVE_COMMANDS = {"pending", "delivered", "incorporated", "partially_applied"}


def _files(value: Any) -> Set[str]:
    if not isinstance(value, (list, tuple, set)):
        return set()
    return {str(item).strip().replace("\\", "/") for item in value if str(item).strip()}


def _worker_matches(worker: Dict[str, Any], requested: Set[str]) -> bool:
    if not requested:
        return True
    owned = _files((worker.get("metadata") or {}).get("allowed_files"))
    return bool(owned & requested)


def fanout_command(orchestrator: Any, lead_id: str, command: Dict[str, Any]) -> Dict[str, Any]:
    requested = _files((command.get("scope") or {}).get("files"))
    children = orchestrator.get_children(lead_id)
    active_children = [child for child in children if child.get("state") in _ACTIVE]
    recipients = [child for child in active_children if _worker_matches(child, requested)]
    child_commands: List[str] = []
    covered: Set[str] = set()
    for child in recipients:
        child_id = str(child.get("task_id") or "")
        covered.update(_files((child.get("metadata") or {}).get("allowed_files")) & requested)
        dispatched = SteeringDispatcher(orchestrator).enqueue(
            child_id,
            str(command.get("kind") or "add_constraint"),
            text=str(command.get("text") or ""),
            delivery_policy=str(command.get("delivery_policy") or "next_checkpoint"),
            idempotency_key=f"fanout:{command.get('command_id')}:{child_id}",
            source_channel=str(command.get("source_channel") or "internal"),
            source_conversation_id=str(command.get("source_conversation_id") or ""),
            requester_user_id=str(command.get("requester_user_id") or ""),
            owner_user_id=str(command.get("owner_user_id") or ""),
            root_task_id=lead_id,
            parent_command_id=str(command.get("command_id") or ""),
            scope=dict(command.get("scope") or {}),
        )
        child_commands.append(str(dispatched.get("command_id") or ""))
    missing = requested - covered
    amendment = None
    if missing:
        amendment = request_scope_amendment(
            orchestrator,
            lead_id,
            command_id=str(command.get("command_id") or ""),
            requested_files=sorted(missing),
        )
    return {
        "lead_command_id": command.get("command_id"),
        "worker_command_ids": child_commands,
        "worker_count": len(child_commands),
        "amendment": amendment,
    }


def fanout_to_new_worker(orchestrator: Any, lead_id: str, worker_id: str) -> List[str]:
    worker = orchestrator.get_task(worker_id) or {}
    created: List[str] = []
    for command in TaskSteeringStore(orchestrator).list(lead_id):
        scoped_files = _files((command.get("scope") or {}).get("files"))
        if command.get("status") not in _ACTIVE_COMMANDS or not _worker_matches(worker, scoped_files):
            continue
        result = SteeringDispatcher(orchestrator).enqueue(
            worker_id,
            str(command.get("kind") or "add_constraint"),
            text=str(command.get("text") or ""),
            delivery_policy=str(command.get("delivery_policy") or "next_checkpoint"),
            idempotency_key=f"fanout:{command.get('command_id')}:{worker_id}",
            source_channel=str(command.get("source_channel") or "internal"),
            source_conversation_id=str(command.get("source_conversation_id") or ""),
            requester_user_id=str(command.get("requester_user_id") or ""),
            owner_user_id=str(command.get("owner_user_id") or ""),
            root_task_id=lead_id,
            parent_command_id=str(command.get("command_id") or ""),
            scope=dict(command.get("scope") or {}),
        )
        created.append(str(result.get("command_id") or ""))
    return created
