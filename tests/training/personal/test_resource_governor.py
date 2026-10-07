from __future__ import annotations

from datetime import datetime

import pytest

from src.training.personal.resource_governor import ResourceGovernor, ResourceSnapshot, TrainingSettings, TrainingSettingsStore


def _snapshot(**changes):
    values = dict(cpu_percent=10, ram_percent=20, vram_percent=30, free_disk_gb=100, on_battery=False, idle_minutes=60)
    values.update(changes)
    return ResourceSnapshot(**values)


def test_governor_prioritizes_voice_and_interactive_work() -> None:
    settings = TrainingSettings(enabled=True, automatic=True, trigger="auto", window_start="00:00", window_end="23:59")
    decision = ResourceGovernor().evaluate(settings, _snapshot(voice_busy=True, agent_busy=True), now=datetime(2026, 10, 4, 3, 0))
    assert decision.allowed is False
    assert "voice_active" in decision.reason_codes
    assert "interactive_work_active" in decision.reason_codes


def test_overnight_window_and_resource_budgets() -> None:
    settings = TrainingSettings(enabled=True, automatic=True, trigger="scheduled", window_start="22:00", window_end="06:00")
    assert ResourceGovernor().evaluate(settings, _snapshot(), now=datetime(2026, 10, 4, 23, 0)).allowed is True
    denied = ResourceGovernor().evaluate(settings, _snapshot(free_disk_gb=2), now=datetime(2026, 10, 4, 23, 0))
    assert denied.reason_codes == ("disk_reserve_too_low",)


def test_settings_are_atomic_and_default_fail_closed(tmp_path) -> None:
    store = TrainingSettingsStore(tmp_path / "settings.json")
    assert store.load().enabled is False
    expected = TrainingSettings(enabled=True, automatic=False, profile="light")
    store.save(expected)
    assert store.load() == expected


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"days": ()}, "training_days_required"),
        ({"days": (7,)}, "training_days_invalid"),
        ({"min_idle_minutes": -1}, "training_idle_minutes_invalid"),
        ({"min_new_experiences": 0}, "training_min_experiences_invalid"),
        ({"max_frequency_days": 0}, "training_frequency_invalid"),
        ({"max_duration_minutes": 0}, "training_duration_invalid"),
        ({"min_free_disk_gb": -1}, "training_disk_reserve_invalid"),
    ],
)
def test_settings_reject_unsafe_bounds(changes, message) -> None:
    with pytest.raises(ValueError, match=message):
        TrainingSettings(**changes)
