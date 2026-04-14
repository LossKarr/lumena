from __future__ import annotations

import asyncio
import contextvars
import os
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

from loguru import logger


TRACE_EVENT_FIELDS = {
    "trace_id",
    "turn_id",
    "seq",
    "ts",
    "channel",
    "client",
    "mode",
    "request_id",
    "conversation_id",
    "task_id",
    "stage",
    "status",
    "duration_ms",
    "provider",
    "model",
    "tool_name",
    "summary",
    "error",
}


_trace_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "lumena_trace_id",
    default=None,
)
_turn_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "lumena_turn_id",
    default=None,
)
_channel_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "lumena_trace_channel",
    default="web",
)
_mode_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "lumena_trace_mode",
    default="chat",
)
_client_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "lumena_trace_client",
    default=None,
)
_request_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "lumena_trace_request_id",
    default=None,
)
_conversation_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "lumena_trace_conversation_id",
    default=None,
)
_task_id_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "lumena_trace_task_id",
    default=None,
)


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except Exception:
        return default  # parsing int échoué


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: Any, max_len: int) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


@dataclass
class _Subscriber:
    queue: asyncio.Queue
    loop: asyncio.AbstractEventLoop


class TraceBus:
    def __init__(
        self,
        enabled: bool = True,
        buffer_size: int = 500,
        summary_max_len: int = 280,
        heartbeat_sec: int = 10,
    ) -> None:
        self.enabled = enabled
        self.buffer_size = max(1, int(buffer_size))
        self.summary_max_len = max(40, int(summary_max_len))
        self.heartbeat_sec = max(3, int(heartbeat_sec))

        self._events: Deque[Dict[str, Any]] = deque(maxlen=self.buffer_size)
        self._seq = 0
        self._lock = threading.Lock()
        self._subscribers: Dict[str, _Subscriber] = {}

    def _sanitize_event(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        clean: Dict[str, Any] = {
            "trace_id": payload.get("trace_id") or uuid.uuid4().hex,
            "turn_id": payload.get("turn_id") or uuid.uuid4().hex,
            "ts": payload.get("ts") or _utc_now_iso(),
            "channel": payload.get("channel") or "web",
            "client": _safe_text(payload.get("client"), 80),
            "mode": payload.get("mode") or "chat",
            "request_id": _safe_text(payload.get("request_id"), 120),
            "conversation_id": _safe_text(payload.get("conversation_id"), 120),
            "task_id": _safe_text(payload.get("task_id"), 120),
            "stage": payload.get("stage") or "unknown",
            "status": payload.get("status") or "ok",
            "duration_ms": payload.get("duration_ms"),
            "provider": payload.get("provider"),
            "model": payload.get("model"),
            "tool_name": payload.get("tool_name"),
            "summary": _safe_text(payload.get("summary"), self.summary_max_len),
            "error": _safe_text(payload.get("error"), 400),
        }

        # Preserve any explicit known field overrides.
        for key in TRACE_EVENT_FIELDS:
            if key in payload and key not in {"summary", "error"}:
                clean[key] = payload[key]

        if clean.get("duration_ms") is not None:
            try:
                clean["duration_ms"] = float(clean["duration_ms"])
            except Exception:
                clean["duration_ms"] = None  # conversion float échouée

        return clean

    @staticmethod
    def _enqueue_event(queue: asyncio.Queue, event: Dict[str, Any]) -> None:
        try:
            queue.put_nowait(event)
            return
        except asyncio.QueueFull:
            pass
        except Exception:
            return  # telemetry must never break runtime

        # Drop oldest when saturated.
        try:
            queue.get_nowait()
        except Exception:
            pass  # queue drain best-effort

        try:
            queue.put_nowait(event)
        except Exception:
            pass  # event put best-effort

    def publish(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.enabled:
            return {}

        try:
            event = self._sanitize_event(payload)
            with self._lock:
                self._seq += 1
                event["seq"] = self._seq
                self._events.append(event)
                subscribers = list(self._subscribers.items())

            # Fan-out to live subscribers.
            stale_ids: List[str] = []
            for subscriber_id, subscriber in subscribers:
                if subscriber.loop.is_closed():
                    stale_ids.append(subscriber_id)
                    continue
                try:
                    subscriber.loop.call_soon_threadsafe(
                        self._enqueue_event,
                        subscriber.queue,
                        dict(event),
                    )
                except Exception:
                    stale_ids.append(subscriber_id)  # subscriber stale

            if stale_ids:
                with self._lock:
                    for stale_id in stale_ids:
                        self._subscribers.pop(stale_id, None)

            return event
        except Exception as exc:
            logger.debug("Trace publish ignored due to error: {}", exc)
            return {}

    def recent(self, limit: int = 200) -> List[Dict[str, Any]]:
        bounded = max(1, min(int(limit), 2000))
        with self._lock:
            data = list(self._events)[-bounded:]
        return [dict(item) for item in data]

    def subscribe(self, max_queue: int = 200) -> Tuple[str, asyncio.Queue]:
        queue_size = max(1, int(max_queue))
        queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        loop = asyncio.get_running_loop()
        subscriber_id = uuid.uuid4().hex
        with self._lock:
            self._subscribers[subscriber_id] = _Subscriber(queue=queue, loop=loop)
        return subscriber_id, queue

    def unsubscribe(self, subscriber_id: str) -> None:
        with self._lock:
            self._subscribers.pop(subscriber_id, None)

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            events_in_buffer = len(self._events)
            stream_clients = len(self._subscribers)
        return {
            "trace_enabled": bool(self.enabled),
            "trace_buffer_size": int(self.buffer_size),
            "trace_events_in_buffer": int(events_in_buffer),
            "trace_stream_clients": int(stream_clients),
            "trace_heartbeat_sec": int(self.heartbeat_sec),
        }

    def clear_for_tests(self) -> None:
        with self._lock:
            self._events.clear()
            self._subscribers.clear()
            self._seq = 0


# Singleton avec lock thread-safe (Phase 2.1)
_trace_bus: Optional[TraceBus] = None
_trace_bus_lock = threading.Lock()


def get_trace_bus() -> TraceBus:
    """Retourne l'instance singleton du TraceBus (thread-safe)."""
    global _trace_bus
    
    # Double-check locking pattern
    if _trace_bus is None:
        with _trace_bus_lock:
            if _trace_bus is None:
                _trace_bus = TraceBus(
                    enabled=_env_flag("LUMENA_TRACE_ENABLED", True),
                    buffer_size=_env_int("LUMENA_TRACE_BUFFER_SIZE", 500),
                    summary_max_len=_env_int("LUMENA_TRACE_SUMMARY_MAX_LEN", 280),
                    heartbeat_sec=_env_int("LUMENA_TRACE_HEARTBEAT_SEC", 10),
                )
    return _trace_bus


def reset_trace_bus_for_tests() -> None:
    """Reset le TraceBus pour les tests (thread-safe)."""
    global _trace_bus
    with _trace_bus_lock:
        _trace_bus = None


def current_trace_context() -> Dict[str, Any]:
    return {
        "trace_id": _trace_id_var.get(),
        "turn_id": _turn_id_var.get(),
        "channel": _channel_var.get(),
        "client": _client_var.get(),
        "mode": _mode_var.get(),
        "request_id": _request_id_var.get(),
        "conversation_id": _conversation_id_var.get(),
        "task_id": _task_id_var.get(),
    }


def push_trace_context(
    trace_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    channel: Optional[str] = None,
    client: Optional[str] = None,
    mode: Optional[str] = None,
    request_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    task_id: Optional[str] = None,
    force: bool = False,
) -> Dict[str, contextvars.Token]:
    tokens: Dict[str, contextvars.Token] = {}

    current_trace_id = _trace_id_var.get()
    current_turn_id = _turn_id_var.get()

    if force or not current_trace_id:
        tokens["trace_id"] = _trace_id_var.set(trace_id or uuid.uuid4().hex)
    if force or not current_turn_id:
        tokens["turn_id"] = _turn_id_var.set(turn_id or uuid.uuid4().hex)
    if force or not _channel_var.get():
        tokens["channel"] = _channel_var.set(channel or "web")
    elif channel:
        tokens["channel"] = _channel_var.set(channel)
    if force or not _mode_var.get():
        tokens["mode"] = _mode_var.set(mode or "chat")
    elif mode:
        tokens["mode"] = _mode_var.set(mode)
    if force or not _client_var.get():
        tokens["client"] = _client_var.set(client or None)
    elif client is not None:
        tokens["client"] = _client_var.set(client)
    if force or not _request_id_var.get():
        tokens["request_id"] = _request_id_var.set(request_id or None)
    elif request_id is not None:
        tokens["request_id"] = _request_id_var.set(request_id)
    if force or not _conversation_id_var.get():
        tokens["conversation_id"] = _conversation_id_var.set(conversation_id or None)
    elif conversation_id is not None:
        tokens["conversation_id"] = _conversation_id_var.set(conversation_id)
    if force or not _task_id_var.get():
        tokens["task_id"] = _task_id_var.set(task_id or None)
    elif task_id is not None:
        tokens["task_id"] = _task_id_var.set(task_id)

    return tokens


def pop_trace_context(tokens: Dict[str, contextvars.Token]) -> None:
    for key in (
        "task_id",
        "conversation_id",
        "request_id",
        "client",
        "mode",
        "channel",
        "turn_id",
        "trace_id",
    ):
        token = tokens.get(key)
        if token is None:
            continue
        try:
            {
                "trace_id": _trace_id_var,
                "turn_id": _turn_id_var,
                "channel": _channel_var,
                "mode": _mode_var,
                "client": _client_var,
                "request_id": _request_id_var,
                "conversation_id": _conversation_id_var,
                "task_id": _task_id_var,
            }[key].reset(token)
        except Exception:
            pass  # contextvars reset best-effort


def publish_trace(
    *,
    stage: str,
    status: str = "ok",
    duration_ms: Optional[float] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    tool_name: Optional[str] = None,
    summary: Optional[str] = None,
    error: Optional[str] = None,
    trace_id: Optional[str] = None,
    turn_id: Optional[str] = None,
    channel: Optional[str] = None,
    client: Optional[str] = None,
    mode: Optional[str] = None,
    request_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    context = current_trace_context()
    payload = {
        "trace_id": trace_id or context.get("trace_id") or uuid.uuid4().hex,
        "turn_id": turn_id or context.get("turn_id") or uuid.uuid4().hex,
        "channel": channel or context.get("channel") or "web",
        "client": client or context.get("client"),
        "mode": mode or context.get("mode") or "chat",
        "request_id": request_id or context.get("request_id"),
        "conversation_id": conversation_id or context.get("conversation_id"),
        "task_id": task_id or context.get("task_id"),
        "stage": stage,
        "status": status,
        "duration_ms": duration_ms,
        "provider": provider,
        "model": model,
        "tool_name": tool_name,
        "summary": summary,
        "error": error,
    }
    return get_trace_bus().publish(payload)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
