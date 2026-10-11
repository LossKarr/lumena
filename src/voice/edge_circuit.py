"""Process-local circuit breaker for the optional Edge consumer TTS service."""
from __future__ import annotations

import os
import re
import threading
import time
from typing import Any, Callable, Dict


_STATUS_RE = re.compile(r"(?<!\d)(401|403|429|5\d\d)(?!\d)")
_IMMEDIATE_OPEN = frozenset({"unauthorized", "forbidden", "rate_limited"})


def classify_edge_failure(error: BaseException) -> str:
    """Return a bounded reason code; raw remote messages never enter status."""
    if isinstance(error, TimeoutError):
        return "timeout"
    status: Any = getattr(error, "status", None)
    if status is None:
        status = getattr(error, "status_code", None)
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    if status is None:
        match = _STATUS_RE.search(str(error))
        status = int(match.group(1)) if match else None
    if status == 401:
        return "unauthorized"
    if status == 403:
        return "forbidden"
    if status == 429:
        return "rate_limited"
    if status is not None and 500 <= status <= 599:
        return "upstream_5xx"
    if isinstance(error, (ConnectionError, OSError)):
        return "network"
    return "provider_error"


class EdgeCircuitBreaker:
    """Bounded closed/open/half-open state shared by all Edge calls in a process."""

    def __init__(
        self,
        *,
        cooldown_s: float = 900.0,
        transient_threshold: int = 2,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.cooldown_s = max(5.0, min(86400.0, float(cooldown_s)))
        self.transient_threshold = max(1, min(10, int(transient_threshold)))
        self._clock = clock
        self._lock = threading.Lock()
        self._state = "closed"
        self._failures = 0
        self._open_until = 0.0
        self._reason = ""
        self._probe_inflight = False

    def allow_request(self) -> bool:
        with self._lock:
            now = self._clock()
            if self._state == "open":
                if now < self._open_until:
                    return False
                self._state = "half_open"
                self._probe_inflight = False
            if self._state == "half_open":
                if self._probe_inflight:
                    return False
                self._probe_inflight = True
            return True

    def record_success(self) -> None:
        with self._lock:
            self._state = "closed"
            self._failures = 0
            self._open_until = 0.0
            self._reason = ""
            self._probe_inflight = False

    def record_failure(self, error: BaseException) -> str:
        reason = classify_edge_failure(error)
        with self._lock:
            self._failures += 1
            immediate = reason in _IMMEDIATE_OPEN
            if self._state == "half_open" or immediate or self._failures >= self.transient_threshold:
                self._state = "open"
                self._open_until = self._clock() + self.cooldown_s
            self._reason = reason
            self._probe_inflight = False
        return reason

    def reset(self) -> None:
        self.record_success()

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            now = self._clock()
            return {
                "state": self._state,
                "failure_count": self._failures,
                "reason": self._reason or None,
                "retry_in_s": round(max(0.0, self._open_until - now), 1),
                "cooldown_s": self.cooldown_s,
            }


def _from_env() -> EdgeCircuitBreaker:
    try:
        cooldown = float(os.getenv("LUMENA_EDGE_TTS_COOLDOWN_S", "900"))
    except (TypeError, ValueError):
        cooldown = 900.0
    try:
        threshold = int(os.getenv("LUMENA_EDGE_TTS_FAILURE_THRESHOLD", "2"))
    except (TypeError, ValueError):
        threshold = 2
    return EdgeCircuitBreaker(cooldown_s=cooldown, transient_threshold=threshold)


_CIRCUIT = _from_env()


def get_edge_tts_circuit() -> EdgeCircuitBreaker:
    return _CIRCUIT
