"""Steering contracts and compatibility helpers.

New code should use TaskSteeringStore. These functions preserve existing call
sites while sharing its atomic and persistent implementation.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from .task_steering_store import TaskSteeringStore


def queue_steering(
    orchestrator: Any,
    task_id: str,
    kind: str,
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    values = dict(payload or {})
    text = str(values.pop("text", "") or "")
    command = TaskSteeringStore(orchestrator).enqueue(
        task_id,
        kind,
        text=text,
        delivery_policy=str(values.pop("delivery_policy", "next_checkpoint")),
        idempotency_key=values.pop("idempotency_key", None),
        source_channel=str(values.pop("source_channel", "internal")),
        source_conversation_id=str(values.pop("source_conversation_id", "")),
        requester_user_id=str(values.pop("requester_user_id", "")),
        owner_user_id=str(values.pop("owner_user_id", "")),
        root_task_id=values.pop("root_task_id", None),
        parent_command_id=values.pop("parent_command_id", None),
        supersedes=values.pop("supersedes", ()),
        scope=values.pop("scope", None),
        payload={"text": text, **values} if text else values,
    )
    if command.get("delivery_policy") == "urgent_safe_boundary" and command.get("status") == "pending":
        from .steering_dispatcher import SIGNALS
        SIGNALS.signal(task_id)
    return command


def consume_text_steering(orchestrator: Any, task_id: str) -> Tuple[str, List[str]]:
    """Deliver pending text once; delivery is not proof of application."""
    return TaskSteeringStore(orchestrator).deliver_pending_text(task_id)


def acknowledge_control(orchestrator: Any, task_id: str, kind: str) -> List[str]:
    return TaskSteeringStore(orchestrator).acknowledge_control(task_id, kind)
