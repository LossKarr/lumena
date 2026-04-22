"""Reliability metrics — singleton thread-safe pour observer la fiabilité runtime.

Agrège :
- Décisions intent_router (intent/source/confidence)
- Refus de policy (mutations code bloquées)
- Upgrades par stickiness / registry fallback
- Erreurs tool execution

Exposé via GET /api/system/reliability (src.ui.api.system_routes).
"""

from __future__ import annotations

import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Optional


@dataclass
class _Bucket:
    total: int = 0
    by_intent: Counter = field(default_factory=Counter)
    by_source: Counter = field(default_factory=Counter)
    sum_confidence: float = 0.0


class ReliabilityMetrics:
    """Singleton thread-safe. Accéder via `get_metrics()`."""

    _instance: Optional["ReliabilityMetrics"] = None
    _singleton_lock = threading.Lock()

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._started_at = time.time()
        self._routing = _Bucket()
        self._policy_refuse_count = 0
        self._policy_refuse_by_tool: Counter = Counter()
        self._sticky_upgrades = 0
        self._registry_fallbacks = 0
        self._tool_errors: Counter = Counter()
        self._tool_success_count = 0
        self._recent_events: Deque[dict] = deque(maxlen=200)

    # ── Recorders ────────────────────────────────────────────────────

    def record_routing(self, *, intent: str, source: str, confidence: float) -> None:
        with self._lock:
            self._routing.total += 1
            self._routing.by_intent[intent] += 1
            self._routing.by_source[source] += 1
            try:
                self._routing.sum_confidence += float(confidence)
            except Exception:
                pass
            self._recent_events.append({
                "ts": time.time(),
                "kind": "route",
                "intent": intent,
                "source": source,
                "confidence": float(confidence) if confidence is not None else None,
            })

    def record_policy_refuse(self, *, tool: str, path: str, project: str) -> None:
        with self._lock:
            self._policy_refuse_count += 1
            self._policy_refuse_by_tool[tool] += 1
            self._recent_events.append({
                "ts": time.time(),
                "kind": "policy_refuse",
                "tool": tool,
                "path": path,
                "project": project,
            })

    def record_sticky_upgrade(self, *, project: str) -> None:
        with self._lock:
            self._sticky_upgrades += 1
            self._recent_events.append({
                "ts": time.time(),
                "kind": "sticky_upgrade",
                "project": project,
            })

    def record_registry_fallback(self, *, project: str) -> None:
        with self._lock:
            self._registry_fallbacks += 1
            self._recent_events.append({
                "ts": time.time(),
                "kind": "registry_fallback",
                "project": project,
            })

    def record_tool_result(self, *, tool: str, success: bool) -> None:
        with self._lock:
            if success:
                self._tool_success_count += 1
            else:
                self._tool_errors[tool] += 1

    # ── Snapshot ─────────────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            total_routes = self._routing.total
            avg_conf = (
                self._routing.sum_confidence / total_routes
                if total_routes > 0 else 0.0
            )
            return {
                "uptime_seconds": int(time.time() - self._started_at),
                "routing": {
                    "total": total_routes,
                    "by_intent": dict(self._routing.by_intent),
                    "by_source": dict(self._routing.by_source),
                    "avg_confidence": round(avg_conf, 3),
                },
                "policy": {
                    "refuse_count": self._policy_refuse_count,
                    "refuse_by_tool": dict(self._policy_refuse_by_tool),
                },
                "stickiness": {
                    "sticky_upgrades": self._sticky_upgrades,
                    "registry_fallbacks": self._registry_fallbacks,
                },
                "tools": {
                    "success_count": self._tool_success_count,
                    "errors_by_tool": dict(self._tool_errors),
                    "error_total": sum(self._tool_errors.values()),
                },
                "recent_events": list(self._recent_events)[-50:],
            }


def get_metrics() -> ReliabilityMetrics:
    """Accès au singleton."""
    if ReliabilityMetrics._instance is None:
        with ReliabilityMetrics._singleton_lock:
            if ReliabilityMetrics._instance is None:
                ReliabilityMetrics._instance = ReliabilityMetrics()
    return ReliabilityMetrics._instance
