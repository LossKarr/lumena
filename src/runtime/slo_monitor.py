"""In-memory SLO monitor for omnichannel runtime health."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from threading import Lock
from typing import Deque, Dict, List, Optional


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return float(numerator) / float(denominator)


@dataclass(frozen=True)
class SLOSample:
    ts: str
    success: bool
    latency_ms: int
    timeout_unrecovered: bool
    resumed: bool
    workspace_error: bool
    undo_success: Optional[bool]


class SLOMonitor:
    def __init__(
        self,
        *,
        window_size: int = 300,
        alert_consecutive: int = 3,
        success_rate_min: float = 0.92,
        timeout_rate_max: float = 0.02,
        latency_median_max_ms: int = 8000,
        latency_p95_max_ms: int = 35000,
        workspace_errors_max: int = 0,
        undo_success_rate_min: float = 1.0,
    ) -> None:
        self.window_size = max(20, int(window_size))
        self.alert_consecutive = max(1, int(alert_consecutive))
        self.success_rate_min = float(success_rate_min)
        self.timeout_rate_max = float(timeout_rate_max)
        self.latency_median_max_ms = max(1, int(latency_median_max_ms))
        self.latency_p95_max_ms = max(1, int(latency_p95_max_ms))
        self.workspace_errors_max = max(0, int(workspace_errors_max))
        self.undo_success_rate_min = max(0.0, float(undo_success_rate_min))

        self._lock = Lock()
        self._samples: Deque[SLOSample] = deque(maxlen=self.window_size)
        self._alerts: Deque[Dict[str, object]] = deque(maxlen=50)
        self._breach_streaks: Dict[str, int] = {}

    def _snapshot_locked(self) -> Dict[str, object]:
        items = list(self._samples)
        total = len(items)
        latencies = [int(sample.latency_ms) for sample in items]
        latencies_sorted = sorted(latencies)
        median_ms = int(median(latencies)) if latencies else 0
        if latencies_sorted:
            p95_index = max(0, min(len(latencies_sorted) - 1, int(round((len(latencies_sorted) - 1) * 0.95))))
            p95_ms = int(latencies_sorted[p95_index])
        else:
            p95_ms = 0

        success_count = sum(1 for sample in items if sample.success)
        timeout_count = sum(1 for sample in items if sample.timeout_unrecovered)
        resumed_count = sum(1 for sample in items if sample.resumed)
        workspace_errors = sum(1 for sample in items if sample.workspace_error)
        undo_known = [sample.undo_success for sample in items if sample.undo_success is not None]
        undo_success_count = sum(1 for value in undo_known if value is True)
        undo_known_count = len(undo_known)

        return {
            "window_size": self.window_size,
            "samples": total,
            "success_count": success_count,
            "success_rate": _safe_rate(success_count, total),
            "timeout_unrecovered_count": timeout_count,
            "timeout_unrecovered_rate": _safe_rate(timeout_count, total),
            "resumed_count": resumed_count,
            "resumed_rate": _safe_rate(resumed_count, total),
            "workspace_error_count": workspace_errors,
            "workspace_error_rate": _safe_rate(workspace_errors, total),
            "undo_known_count": undo_known_count,
            "undo_success_count": undo_success_count,
            "undo_success_rate": _safe_rate(undo_success_count, undo_known_count) if undo_known_count else None,
            "latency_median_ms": median_ms,
            "latency_p95_ms": p95_ms,
            "alerts_recent": list(self._alerts),
        }

    def _detect_breaches(self, snapshot: Dict[str, object]) -> List[str]:
        samples = int(snapshot.get("samples", 0))
        if samples <= 0:
            return []

        breaches: List[str] = []
        if float(snapshot.get("success_rate", 0.0)) < self.success_rate_min:
            breaches.append("success_rate")
        if float(snapshot.get("timeout_unrecovered_rate", 0.0)) > self.timeout_rate_max:
            breaches.append("timeout_unrecovered_rate")
        if int(snapshot.get("latency_median_ms", 0)) > self.latency_median_max_ms:
            breaches.append("latency_median_ms")
        if int(snapshot.get("latency_p95_ms", 0)) > self.latency_p95_max_ms:
            breaches.append("latency_p95_ms")
        if int(snapshot.get("workspace_error_count", 0)) > self.workspace_errors_max:
            breaches.append("workspace_error_count")

        undo_rate = snapshot.get("undo_success_rate")
        if undo_rate is not None and float(undo_rate) < self.undo_success_rate_min:
            breaches.append("undo_success_rate")

        return breaches

    def record(
        self,
        *,
        success: bool,
        latency_ms: int,
        timeout_unrecovered: bool,
        resumed: bool,
        workspace_error: bool,
        undo_success: Optional[bool],
    ) -> Dict[str, object]:
        sample = SLOSample(
            ts=_utc_now_iso(),
            success=bool(success),
            latency_ms=max(0, int(latency_ms)),
            timeout_unrecovered=bool(timeout_unrecovered),
            resumed=bool(resumed),
            workspace_error=bool(workspace_error),
            undo_success=undo_success if undo_success is None else bool(undo_success),
        )
        with self._lock:
            self._samples.append(sample)
            snapshot = self._snapshot_locked()
            breaches = self._detect_breaches(snapshot)

            active_set = set(breaches)
            for metric in list(self._breach_streaks.keys()):
                if metric not in active_set:
                    self._breach_streaks[metric] = 0

            triggered: List[Dict[str, object]] = []
            for metric in breaches:
                current = int(self._breach_streaks.get(metric, 0)) + 1
                self._breach_streaks[metric] = current
                if current == self.alert_consecutive:
                    alert = {
                        "ts": _utc_now_iso(),
                        "metric": metric,
                        "streak": current,
                        "samples": int(snapshot.get("samples", 0)),
                        "value": snapshot.get(metric),
                    }
                    self._alerts.append(alert)
                    triggered.append(alert)

            snapshot["breaches"] = breaches
            snapshot["breach_streaks"] = dict(self._breach_streaks)
            snapshot["alerts_recent"] = list(self._alerts)
            return {
                "snapshot": snapshot,
                "breaches": breaches,
                "triggered_alerts": triggered,
            }

    def snapshot(self) -> Dict[str, object]:
        with self._lock:
            snapshot = self._snapshot_locked()
            snapshot["breaches"] = self._detect_breaches(snapshot)
            snapshot["breach_streaks"] = dict(self._breach_streaks)
            return snapshot

    def clear_for_tests(self) -> None:
        with self._lock:
            self._samples.clear()
            self._alerts.clear()
            self._breach_streaks.clear()
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
