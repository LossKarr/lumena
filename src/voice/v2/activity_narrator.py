"""Policy layer turning verified public events into sparse natural speech."""
from __future__ import annotations

from collections import deque
import hashlib
import re
import time
from typing import Any, Callable, Deque, Optional

from src.reasoning.public_activity import PublicActivityEvent


_SPEAKABLE_PHASES = frozenset({
    "start", "inspect", "search", "change", "verify", "wait", "blocked",
    "success", "error",
})
_PHASE_PRIORITY = {
    "blocked": "error",
    "error": "error",
    "success": "status",
    "verify": "status",
}


def _fingerprint(event: PublicActivityEvent) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", event.public_update.lower()).strip()
    return hashlib.sha256(f"{event.phase}:{normalized}".encode("utf-8")).hexdigest()[:16]


class PublicActivityNarrator:
    """Speak only useful, fresh events; never asks an LLM or reads tool args."""

    def __init__(
        self,
        speech_coordinator: Any,
        *,
        min_interval_s: float = 2.5,
        dedupe_window_s: float = 45.0,
        max_updates_per_minute: int = 8,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.speech = speech_coordinator
        self.min_interval_s = max(0.0, float(min_interval_s))
        self.dedupe_window_s = max(0.0, float(dedupe_window_s))
        self.max_updates_per_minute = max(1, int(max_updates_per_minute))
        self._clock = clock
        self._last_spoken_at = float("-inf")
        self._recent: dict[str, float] = {}
        self._budget: Deque[float] = deque()
        self.spoken = 0
        self.dropped = 0
        self.last_drop_reason: Optional[str] = None

    async def narrate(self, event: Optional[PublicActivityEvent]) -> str:
        now = self._clock()
        if event is None:
            return self._drop("invalid")
        if event.expired:
            return self._drop("expired")
        if event.phase not in _SPEAKABLE_PHASES:
            return self._drop("phase")
        fingerprint = _fingerprint(event)
        previous = self._recent.get(fingerprint)
        if previous is not None and now - previous < self.dedupe_window_s:
            return self._drop("duplicate")
        while self._budget and now - self._budget[0] >= 60.0:
            self._budget.popleft()
        urgent = event.phase in {"blocked", "error"}
        if len(self._budget) >= self.max_updates_per_minute and not urgent:
            return self._drop("budget")
        if now - self._last_spoken_at < self.min_interval_s and not urgent:
            return self._drop("cadence")
        kind = _PHASE_PRIORITY.get(event.phase, "milestone")
        request_id = await self.speech.say(
            event.public_update,
            kind=kind,
            ttl_s=max(0.1, (event.expires_at - event.created_at).total_seconds()),
            replace_key=f"activity:{event.task_id or 'current'}:{event.phase}",
            turn=event.turn_id or "activity",
        )
        if not request_id:
            return self._drop("coordinator")
        self._recent[fingerprint] = now
        self._last_spoken_at = now
        self._budget.append(now)
        self.spoken += 1
        return request_id

    def _drop(self, reason: str) -> str:
        self.dropped += 1
        self.last_drop_reason = reason
        return ""

    def status(self) -> dict[str, Any]:
        return {
            "spoken": self.spoken,
            "dropped": self.dropped,
            "last_drop_reason": self.last_drop_reason,
            "recent_fingerprints": len(self._recent),
            "minute_budget_used": len(self._budget),
        }
