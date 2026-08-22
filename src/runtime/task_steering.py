"""Persistent, checkpoint-safe steering commands for TaskOrchestrator tasks."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
import uuid


_TEXT_KINDS = {"add_constraint", "reprioritize"}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def queue_steering(
    orchestrator: Any,
    task_id: str,
    kind: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if kind not in _TEXT_KINDS | {"pause", "resume", "cancel"}:
        raise ValueError(f"unsupported steering kind: {kind}")
    record = orchestrator.get_task(task_id)
    if not record:
        raise KeyError(task_id)
    metadata = record.get("metadata") or {}
    commands = list(metadata.get("steering_commands") or [])
    command = {
        "command_id": f"steer_{uuid.uuid4().hex}",
        "mission_id": task_id,
        "kind": kind,
        "payload": dict(payload or {}),
        "created_at": _now_iso(),
        "applied_at": None,
        "status": "pending",
    }
    commands.append(command)
    updates: Dict[str, Any] = {"steering_commands": commands}
    if kind == "pause":
        updates.update(pause_requested=True, paused=False)
    elif kind == "resume":
        updates.update(pause_requested=False, paused=False)
        command["status"] = "applied"
        command["applied_at"] = _now_iso()
    elif kind == "cancel":
        orchestrator.cancel_task(task_id, propagate=True)
        command["status"] = "applied"
        command["applied_at"] = _now_iso()
    orchestrator.set_task_metadata(task_id, **updates)
    return command


def consume_text_steering(orchestrator: Any, task_id: str) -> Tuple[str, List[str]]:
    """Atomically enough for one ReAct owner: consume pending textual commands."""
    record = orchestrator.get_task(task_id)
    if not record:
        return "", []
    metadata = record.get("metadata") or {}
    commands = list(metadata.get("steering_commands") or [])
    texts: List[str] = []
    applied: List[str] = []
    changed = False
    now = _now_iso()
    for command in commands:
        if command.get("status") != "pending" or command.get("kind") not in _TEXT_KINDS:
            continue
        text = str((command.get("payload") or {}).get("text") or "").strip()
        command["status"] = "applied"
        command["applied_at"] = now
        changed = True
        applied.append(str(command.get("command_id") or ""))
        if text:
            texts.append(text)
    if changed:
        orchestrator.set_task_metadata(task_id, steering_commands=commands)
    if not texts:
        return "", applied
    return (
        "INSTRUCTION UTILISATEUR RECUE PENDANT LE TRAVAIL (applique-la sans "
        "annuler ce qui est deja valide):\n- " + "\n- ".join(texts),
        applied,
    )


def acknowledge_control(orchestrator: Any, task_id: str, kind: str) -> List[str]:
    record = orchestrator.get_task(task_id)
    if not record:
        return []
    metadata = record.get("metadata") or {}
    commands = list(metadata.get("steering_commands") or [])
    applied: List[str] = []
    now = _now_iso()
    for command in commands:
        if command.get("kind") == kind and command.get("status") == "pending":
            command["status"] = "applied"
            command["applied_at"] = now
            applied.append(str(command.get("command_id") or ""))
    if applied:
        orchestrator.set_task_metadata(task_id, steering_commands=commands)
    return applied
