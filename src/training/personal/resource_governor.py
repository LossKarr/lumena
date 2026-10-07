"""Resource policy that keeps interactive Lumena work ahead of training."""

from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from datetime import datetime, time
from pathlib import Path
from typing import Callable

from filelock import FileLock

from src.utils.persistence import atomic_write_json, safe_read_json


@dataclass(frozen=True, slots=True)
class TrainingSettings:
    enabled: bool = False
    automatic: bool = False
    trigger: str = "manual"
    days: tuple[int, ...] = (0, 1, 2, 3, 4, 5, 6)
    window_start: str = "01:00"
    window_end: str = "06:00"
    min_idle_minutes: int = 20
    min_new_experiences: int = 100
    max_frequency_days: int = 7
    max_duration_minutes: int = 240
    max_cpu_percent: float = 70.0
    max_ram_percent: float = 75.0
    max_vram_percent: float = 90.0
    min_free_disk_gb: float = 20.0
    allow_on_battery: bool = False
    pause_during_voice: bool = True
    pause_during_work: bool = True
    profile: str = "balanced"

    def __post_init__(self) -> None:
        if self.trigger not in {"manual", "idle", "scheduled", "auto"}:
            raise ValueError("training_trigger_invalid")
        if self.profile not in {"light", "balanced", "quality", "expert"}:
            raise ValueError("training_profile_invalid")
        if not all(0 <= day <= 6 for day in self.days):
            raise ValueError("training_days_invalid")
        if not self.days:
            raise ValueError("training_days_required")
        if not 0 <= self.min_idle_minutes <= 10_080:
            raise ValueError("training_idle_minutes_invalid")
        if not 1 <= self.min_new_experiences <= 1_000_000:
            raise ValueError("training_min_experiences_invalid")
        if not 1 <= self.max_frequency_days <= 3_650:
            raise ValueError("training_frequency_invalid")
        if not 1 <= self.max_duration_minutes <= 10_080:
            raise ValueError("training_duration_invalid")
        if not 0 <= self.min_free_disk_gb <= 100_000:
            raise ValueError("training_disk_reserve_invalid")
        for value in (self.max_cpu_percent, self.max_ram_percent, self.max_vram_percent):
            if not 1 <= value <= 100:
                raise ValueError("training_resource_percent_invalid")
        _parse_clock(self.window_start)
        _parse_clock(self.window_end)

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        payload = dict(data)
        payload["days"] = tuple(payload.get("days", (0, 1, 2, 3, 4, 5, 6)))
        return cls(**payload)


@dataclass(frozen=True, slots=True)
class ResourceSnapshot:
    cpu_percent: float
    ram_percent: float
    vram_percent: float | None
    free_disk_gb: float
    on_battery: bool
    idle_minutes: float
    agent_busy: bool = False
    voice_busy: bool = False
    mission_busy: bool = False
    codeagent_busy: bool = False
    video_busy: bool = False


@dataclass(frozen=True, slots=True)
class GovernorDecision:
    allowed: bool
    state: str
    reason_codes: tuple[str, ...]


def _parse_clock(value: str) -> time:
    try:
        hour, minute = (int(part) for part in value.split(":"))
        return time(hour=hour, minute=minute)
    except Exception as exc:
        raise ValueError("training_window_invalid") from exc


def _in_window(now: datetime, start: str, end: str) -> bool:
    start_time, end_time = _parse_clock(start), _parse_clock(end)
    current = now.time().replace(second=0, microsecond=0)
    if start_time <= end_time:
        return start_time <= current <= end_time
    return current >= start_time or current <= end_time


class TrainingSettingsStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = FileLock(str(self.path) + ".lock", timeout=10)

    def load(self) -> TrainingSettings:
        with self._lock:
            data = safe_read_json(self.path, default={})
        if not data:
            return TrainingSettings()
        try:
            return TrainingSettings.from_dict(data.get("settings", data))
        except (TypeError, ValueError):
            return TrainingSettings()

    def save(self, settings: TrainingSettings) -> TrainingSettings:
        with self._lock:
            atomic_write_json(self.path, {"schema_version": 1, "settings": settings.to_dict()})
        return settings


class ResourceGovernor:
    def evaluate(self, settings: TrainingSettings, snapshot: ResourceSnapshot, *, now: datetime | None = None, manual: bool = False) -> GovernorDecision:
        reasons: list[str] = []
        moment = now or datetime.now()
        if not settings.enabled:
            reasons.append("training_disabled")
        if not manual and not settings.automatic:
            reasons.append("automatic_training_disabled")
        if not manual and settings.trigger in {"scheduled", "auto"}:
            if moment.weekday() not in settings.days or not _in_window(moment, settings.window_start, settings.window_end):
                reasons.append("outside_schedule")
        if not manual and settings.trigger in {"idle", "auto"} and snapshot.idle_minutes < settings.min_idle_minutes:
            reasons.append("user_not_idle")
        if snapshot.on_battery and not settings.allow_on_battery:
            reasons.append("battery_not_allowed")
        if snapshot.cpu_percent > settings.max_cpu_percent:
            reasons.append("cpu_budget_exceeded")
        if snapshot.ram_percent > settings.max_ram_percent:
            reasons.append("ram_budget_exceeded")
        if snapshot.vram_percent is not None and snapshot.vram_percent > settings.max_vram_percent:
            reasons.append("vram_budget_exceeded")
        if snapshot.free_disk_gb < settings.min_free_disk_gb:
            reasons.append("disk_reserve_too_low")
        if settings.pause_during_voice and snapshot.voice_busy:
            reasons.append("voice_active")
        if settings.pause_during_work and any((snapshot.agent_busy, snapshot.mission_busy, snapshot.codeagent_busy, snapshot.video_busy)):
            reasons.append("interactive_work_active")
        return GovernorDecision(not reasons, "ready" if not reasons else "waiting_idle", tuple(reasons))

    @staticmethod
    def probe(root: Path, *, idle_minutes: float = 0.0, activity_probe: Callable[[], dict] | None = None) -> ResourceSnapshot:
        cpu = ram = 0.0
        on_battery = False
        try:
            import psutil
            cpu = float(psutil.cpu_percent(interval=None))
            ram = float(psutil.virtual_memory().percent)
            battery = psutil.sensors_battery()
            on_battery = bool(battery and not battery.power_plugged)
        except Exception:
            pass
        vram = None
        try:
            import torch
            if torch.cuda.is_available():
                free, total = torch.cuda.mem_get_info()
                vram = 100.0 * (1.0 - (free / max(1, total)))
        except Exception:
            pass
        activity = activity_probe() if activity_probe else {}
        return ResourceSnapshot(
            cpu_percent=cpu,
            ram_percent=ram,
            vram_percent=vram,
            free_disk_gb=shutil.disk_usage(root).free / (1024 ** 3),
            on_battery=on_battery,
            idle_minutes=idle_minutes,
            agent_busy=bool(activity.get("agent")),
            voice_busy=bool(activity.get("voice")),
            mission_busy=bool(activity.get("mission")),
            codeagent_busy=bool(activity.get("codeagent")),
            video_busy=bool(activity.get("video")),
        )
