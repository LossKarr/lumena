from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.runtime.slo_monitor import SLOMonitor


def test_slo_monitor_records_snapshot_metrics():
    monitor = SLOMonitor(window_size=20, alert_consecutive=3)
    monitor.record(
        success=True,
        latency_ms=120,
        timeout_unrecovered=False,
        resumed=False,
        workspace_error=False,
        undo_success=None,
    )
    monitor.record(
        success=False,
        latency_ms=220,
        timeout_unrecovered=True,
        resumed=False,
        workspace_error=True,
        undo_success=False,
    )

    snapshot = monitor.snapshot()
    assert snapshot["samples"] == 2
    assert snapshot["success_count"] == 1
    assert snapshot["timeout_unrecovered_count"] == 1
    assert snapshot["workspace_error_count"] == 1
    assert snapshot["latency_median_ms"] >= 120
    assert snapshot["latency_p95_ms"] >= snapshot["latency_median_ms"]
    assert "breaches" in snapshot


def test_slo_monitor_triggers_alert_after_three_consecutive_breaches():
    monitor = SLOMonitor(
        window_size=20,
        alert_consecutive=3,
        success_rate_min=1.0,
        timeout_rate_max=0.0,
        latency_median_max_ms=1,
        latency_p95_max_ms=1,
        workspace_errors_max=0,
        undo_success_rate_min=1.0,
    )

    first = monitor.record(
        success=False,
        latency_ms=200,
        timeout_unrecovered=True,
        resumed=False,
        workspace_error=True,
        undo_success=False,
    )
    second = monitor.record(
        success=False,
        latency_ms=220,
        timeout_unrecovered=True,
        resumed=False,
        workspace_error=True,
        undo_success=False,
    )
    third = monitor.record(
        success=False,
        latency_ms=240,
        timeout_unrecovered=True,
        resumed=False,
        workspace_error=True,
        undo_success=False,
    )

    assert not list(first.get("triggered_alerts") or [])
    assert not list(second.get("triggered_alerts") or [])
    assert list(third.get("triggered_alerts") or [])
    snapshot = monitor.snapshot()
    assert snapshot["alerts_recent"]
