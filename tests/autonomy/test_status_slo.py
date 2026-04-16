from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tests._server_compat import server_module
from src.runtime.slo_monitor import SLOMonitor


class _FakeLumena:
    def __init__(self):
        self.repo_map = None
        self.code_index = None
        self.rules_loader = None
        self.hook_system = None
        self.instinct_system = None
        self.memory = SimpleNamespace(get_stats=lambda: {"count": 0})
        self.emotion_manager = SimpleNamespace(
            get_mood=lambda: SimpleNamespace(value="focused"),
            get_stats=lambda: {"mood": "focused", "energy": "high"},
        )
        self._skills = {}
        self.skills_auto_activation = True


@pytest.mark.asyncio
async def test_status_exposes_slo_metrics_and_alerts(monkeypatch):
    monitor = SLOMonitor(window_size=20, alert_consecutive=3, success_rate_min=1.0)
    for _ in range(3):
        monitor.record(
            success=False,
            latency_ms=250,
            timeout_unrecovered=True,
            resumed=False,
            workspace_error=True,
            undo_success=False,
        )

    monkeypatch.setattr(server_module, "lumena", _FakeLumena())
    monkeypatch.setattr(server_module, "_SLO_MONITOR", monitor)

    payload = await server_module.get_status()

    assert payload["slo_enabled"] is True
    assert payload["slo_samples"] >= 3
    assert isinstance(payload["slo_breaches"], list)
    assert isinstance(payload["slo_breach_streaks"], dict)
    assert isinstance(payload["slo_alerts_recent"], list)
    assert payload["slo_alerts_recent"], "expected alert after 3 consecutive breaches"
