"""Bounded, redacted public activity events for user-facing narration.

This module deliberately receives no raw observation and never serializes tool
arguments.  It is safe to call synchronously from the ReAct hot path: validation
and publication are in-memory and bounded.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import inspect
import re
import time
import uuid
from typing import Any, Callable, Deque, Optional


_PRIVATE_MARKERS = re.compile(
    r"(?i)(?:\bTHOUGHT\s*:|\bACTION(?:_INPUT)?\s*:|\bOBSERVATION\s*:|"
    r"system prompt|developer message|traceback|stack trace)"
)
_SECRET_MARKERS = re.compile(
    r"(?i)(?:api[_ -]?key|secret|password|mot de passe|bearer\s+[A-Za-z0-9._-]+|"
    r"sk-[A-Za-z0-9_-]{12,}|token\s*[:=])"
)
_ABSOLUTE_PATH = re.compile(
    r"(?:\b[A-Za-z]:[\\/]|(?:^|\s)/(?:home|Users|var|tmp|etc|opt)/)", re.IGNORECASE
)
_SUCCESS_CLAIMS = re.compile(
    r"(?i)\b(?:termin(?:e|é|ee|ée)|reussi|réussi|succes|succès|tests?\s+(?:sont\s+)?(?:verts?|pass(?:e|é|es|és|ées))|"
    r"corrige|corrigé|installe|installé|cree|créé|envoye|envoyé)\b"
)
_ALLOWED_PHASES = frozenset({
    "start", "inspect", "search", "change", "verify", "wait", "blocked",
    "success", "error",
})


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _identifier(value: Any, *, limit: int = 80) -> Optional[str]:
    if value is None:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(value).strip())[:limit]
    return cleaned or None


def tool_category(tool_name: str) -> str:
    name = (tool_name or "").lower()
    if any(token in name for token in ("read", "list", "grep", "inspect", "status")):
        return "inspect"
    if any(token in name for token in ("search", "fetch", "research", "lookup", "find")):
        return "search"
    if any(token in name for token in ("write", "edit", "patch", "create", "delete")):
        return "change"
    if any(token in name for token in ("test", "verify", "check")):
        return "verify"
    if any(token in name for token in ("send", "mail", "telegram", "whatsapp")):
        return "communicate"
    if "browser" in name:
        return "browser"
    if any(token in name for token in ("delegate", "mission", "project")):
        return "delegate"
    if any(token in name for token in ("run", "exec", "shell", "command")):
        return "execute"
    return "other"


@dataclass(frozen=True)
class PublicActivityEvent:
    event_id: str
    task_id: Optional[str]
    turn_id: Optional[str]
    phase: str
    public_update: str
    tool_category: str
    proof_refs: tuple[str, ...] = field(default_factory=tuple)
    created_at: datetime = field(default_factory=_utcnow)
    expires_at: datetime = field(default_factory=lambda: _utcnow() + timedelta(seconds=20))
    priority: int = 20

    @property
    def expired(self) -> bool:
        return _utcnow() >= self.expires_at

    def trace_digest(self) -> dict[str, Any]:
        """Minimal trace projection: no text, args, paths, prompts or outputs."""
        return {
            "event_id": self.event_id,
            "task_id": self.task_id,
            "turn_id": self.turn_id,
            "phase": self.phase,
            "tool_category": self.tool_category,
            "proof_count": len(self.proof_refs),
            "priority": self.priority,
        }


def build_public_activity_event(
    public_update: Any,
    *,
    tool_name: str,
    task_id: Any = None,
    turn_id: Any = None,
    phase: Optional[str] = None,
    proof_refs: tuple[str, ...] = (),
    ttl_s: float = 20.0,
    priority: int = 20,
) -> Optional[PublicActivityEvent]:
    text = re.sub(r"\s+", " ", str(public_update or "")).strip()
    if not text or len(text) > 280:
        return None
    if _PRIVATE_MARKERS.search(text) or _SECRET_MARKERS.search(text) or _ABSOLUTE_PATH.search(text):
        return None
    clean_refs = tuple(
        ref for ref in (_identifier(item, limit=96) for item in proof_refs) if ref
    )[:8]
    resolved_phase = (phase or tool_category(tool_name)).strip().lower()
    if resolved_phase not in _ALLOWED_PHASES:
        resolved_phase = "start"
    if _SUCCESS_CLAIMS.search(text) and not clean_refs:
        return None
    created = _utcnow()
    return PublicActivityEvent(
        event_id=uuid.uuid4().hex,
        task_id=_identifier(task_id),
        turn_id=_identifier(turn_id),
        phase=resolved_phase,
        public_update=text,
        tool_category=tool_category(tool_name),
        proof_refs=clean_refs,
        created_at=created,
        expires_at=created + timedelta(seconds=max(0.1, min(float(ttl_s), 120.0))),
        priority=max(0, min(int(priority), 100)),
    )


class PublicActivityBus:
    """Non-blocking drop-oldest queue; publication never waits for a consumer."""

    def __init__(self, max_events: int = 32):
        self._events: Deque[PublicActivityEvent] = deque(maxlen=max(1, int(max_events)))
        self.published = 0
        self.dropped = 0

    def publish(self, event: PublicActivityEvent) -> None:
        if len(self._events) == self._events.maxlen:
            self.dropped += 1
        self._events.append(event)
        self.published += 1

    def drain(self) -> list[PublicActivityEvent]:
        now = _utcnow()
        result = [event for event in self._events if event.expires_at > now]
        self._events.clear()
        return result

    def __len__(self) -> int:
        return len(self._events)


_activity_bus = PublicActivityBus()


def get_public_activity_bus() -> PublicActivityBus:
    return _activity_bus


def notify_step_callback(
    callback: Optional[Callable[..., Any]],
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    public_update: Optional[str] = None,
    task_id: Any = None,
    turn_id: Any = None,
    bus: Optional[PublicActivityBus] = None,
) -> Optional[PublicActivityEvent]:
    """Publish a valid event and preserve every historical callback signature."""
    event = build_public_activity_event(
        public_update, tool_name=tool_name, task_id=task_id, turn_id=turn_id,
    )
    if event is not None:
        (bus or get_public_activity_bus()).publish(event)
    if not callable(callback):
        return event
    started = time.perf_counter()
    try:
        try:
            parameters = inspect.signature(callback).parameters
            accepts_event = len(parameters) >= 3 or any(
                parameter.kind in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)
                for parameter in parameters.values()
            )
        except (TypeError, ValueError):
            accepts_event = False
        if accepts_event:
            callback(tool_name, tool_args, event)
        else:
            callback(tool_name, tool_args)
    except Exception:
        # A narration hook must never stop tool execution.
        return event
    finally:
        # Deliberately measured here so profiling includes signature adaptation.
        _ = time.perf_counter() - started
    return event
