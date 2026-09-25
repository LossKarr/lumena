"""Deterministic omnichannel pre-router for clear steering messages."""
from __future__ import annotations

from dataclasses import asdict, dataclass
import re
from typing import Any, Dict, Optional

from .mission_steering import fanout_command
from .steering_dispatcher import SteeringDispatcher
from .work_registry import classify_work_turn
from .work_target_resolver import WorkTargetResolver

_TASK_ID = re.compile(r"\btask_[a-f0-9]{8,64}\b", re.IGNORECASE)
_EXTERNAL = {"telegram", "whatsapp", "discord", "api"}


@dataclass(frozen=True, slots=True)
class ChannelSteeringResult:
    handled: bool
    facts: Dict[str, Any]


def route_channel_steering(
    orchestrator: Any,
    text: str,
    *,
    source_channel: str,
    conversation_id: Optional[str],
    owner_user_id: str,
    sender_is_owner: bool,
) -> ChannelSteeringResult:
    raw = str(text or "").strip()
    explicit = raw.lower().startswith(("/orient ", "/steer "))
    if not explicit and classify_work_turn(raw) != "steer":
        return ChannelSteeringResult(False, {})
    channel = str(source_channel or "web").lower()
    if channel in _EXTERNAL and not sender_is_owner:
        return ChannelSteeringResult(True, {
            "code": "owner_mismatch", "accepted": False, "source_channel": channel,
        })
    preferred = (_TASK_ID.search(raw).group(0) if _TASK_ID.search(raw) else None)
    # Explicit IDs can cross linked conversations; automatic selection stays scoped
    # to the current conversation first, then falls back to the owner's unique root.
    resolver = WorkTargetResolver(orchestrator)
    result = resolver.resolve(
        owner_user_id=owner_user_id,
        conversation_id=None if preferred else conversation_id,
        preferred_task_id=preferred,
    )
    if result.code == "no_active_work" and conversation_id and not preferred:
        result = resolver.resolve(owner_user_id=owner_user_id)
    if not result.resolved:
        return ChannelSteeringResult(True, {
            "code": result.code,
            "accepted": False,
            "candidates": list(result.candidates),
            "source_channel": channel,
        })
    instruction = raw.split(" ", 1)[1].strip() if explicit and " " in raw else raw
    urgent = any(token in instruction.lower() for token in ("maintenant", "tout de suite", "priorite", "priorité"))
    command = SteeringDispatcher(orchestrator).enqueue(
        result.target.task_id,
        "add_constraint",
        text=instruction,
        delivery_policy="urgent_safe_boundary" if urgent else "next_checkpoint",
        source_channel=channel,
        source_conversation_id=conversation_id or "",
        requester_user_id=owner_user_id,
        owner_user_id=owner_user_id,
    )
    task = orchestrator.get_task(result.target.task_id) or {}
    fanout = None
    metadata = task.get("metadata") or {}
    if metadata.get("kind") == "mission" and not metadata.get("parent_id"):
        fanout = fanout_command(orchestrator, result.target.task_id, command)
    return ChannelSteeringResult(True, {
        "code": "steering_accepted" if command.get("status") != "late" else "task_terminal",
        "accepted": command.get("status") != "late",
        "task": asdict(result.target),
        "command": {
            "command_id": command.get("command_id"),
            "sequence": command.get("sequence"),
            "status": command.get("status"),
            "delivery_policy": command.get("delivery_policy"),
            "safe_boundary": command.get("safe_boundary"),
        },
        "fanout": fanout,
        "source_channel": channel,
    })
