"""Cran 2 — Bus d'événements P2P (push SSE temps réel).

Mirror allégé de `src.telemetry.trace_bus` : fan-out thread-safe vers des
abonnés SSE via `loop.call_soon_threadsafe`. Charge utile libre (dict JSON-able),
on ajoute juste `ts`, `seq`, `type`.

Sert à pousser instantanément l'état des missions inter-Lumena (queued → running
→ completed/failed) et les refus, au lieu d'attendre le poll. Lecture seule côté
client : aucun secret/token ne doit transiter (l'appelant ne publie que des
métadonnées d'affichage).
"""
from __future__ import annotations

import asyncio
import threading
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Tuple

from loguru import logger


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class _Subscriber:
    queue: asyncio.Queue
    loop: asyncio.AbstractEventLoop


class PeerEventBus:
    def __init__(self, buffer_size: int = 300, heartbeat_sec: int = 15) -> None:
        self.buffer_size = max(1, int(buffer_size))
        self.heartbeat_sec = max(3, int(heartbeat_sec))
        self._events: Deque[Dict[str, Any]] = deque(maxlen=self.buffer_size)
        self._seq = 0
        self._lock = threading.Lock()
        self._subscribers: Dict[str, _Subscriber] = {}

    @staticmethod
    def _enqueue(queue: asyncio.Queue, event: Dict[str, Any]) -> None:
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass  # best-effort : on laisse tomber si l'abonné est en retard
        except Exception:
            pass

    def publish(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            event = dict(payload or {})
            event.setdefault("ts", _now_iso())
            event.setdefault("type", "peer")
            with self._lock:
                self._seq += 1
                event["seq"] = self._seq
                self._events.append(event)
                subscribers = list(self._subscribers.items())

            stale: List[str] = []
            for sid, sub in subscribers:
                if sub.loop.is_closed():
                    stale.append(sid)
                    continue
                try:
                    sub.loop.call_soon_threadsafe(self._enqueue, sub.queue, dict(event))
                except Exception:
                    stale.append(sid)
            if stale:
                with self._lock:
                    for sid in stale:
                        self._subscribers.pop(sid, None)
            return event
        except Exception as exc:
            logger.debug("[peer_event_bus] publish ignoré : {}", exc)
            return {}

    def subscribe(self, max_queue: int = 200) -> Tuple[str, asyncio.Queue]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=max(1, int(max_queue)))
        loop = asyncio.get_running_loop()
        sid = uuid.uuid4().hex
        with self._lock:
            self._subscribers[sid] = _Subscriber(queue=queue, loop=loop)
        return sid, queue

    def unsubscribe(self, subscriber_id: str) -> None:
        with self._lock:
            self._subscribers.pop(subscriber_id, None)

    def get_stats(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "events_in_buffer": len(self._events),
                "stream_clients": len(self._subscribers),
                "heartbeat_sec": self.heartbeat_sec,
            }

    def clear_for_tests(self) -> None:
        with self._lock:
            self._events.clear()
            self._subscribers.clear()
            self._seq = 0


_bus: Optional[PeerEventBus] = None
_bus_lock = threading.Lock()


def get_peer_event_bus() -> PeerEventBus:
    global _bus
    if _bus is None:
        with _bus_lock:
            if _bus is None:
                _bus = PeerEventBus()
    return _bus


def publish_peer_event(event_type: str, **fields: Any) -> Dict[str, Any]:
    """Helper : publie un événement pair (best-effort, ne lève jamais)."""
    try:
        return get_peer_event_bus().publish({"type": event_type, **fields})
    except Exception:
        return {}
