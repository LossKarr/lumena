"""Background training arbitration with interactive Lumena work as priority."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.autonomy.presence import seconds_since_activity

from .contracts import TrainingRunState
from .control_plane import PersonalModelControlPlane
from .resource_governor import ResourceGovernor


ActivityProbe = Callable[[], dict[str, bool]]


def runtime_activity() -> dict[str, bool]:
    activity = {"agent": False, "voice": False, "mission": False, "codeagent": False, "video": False}
    try:
        from src.autonomy.scheduler import is_agent_busy
        activity["agent"] = bool(is_agent_busy())
    except Exception:
        pass
    try:
        from src.voice.lifecycle import VoiceLifecycleManager
        activity["voice"] = bool(VoiceLifecycleManager.get_instance().running)
    except Exception:
        pass
    try:
        from src.core import get_lumena
        core = get_lumena()
        orchestrator = getattr(core, "task_orchestrator", None) if core else None
        tasks = orchestrator.list_all_tasks(limit=500) if orchestrator else []
        for task in tasks or []:
            if task.get("state") not in {"queued", "running", "waiting_io", "checkpointed"}:
                continue
            kind = str((task.get("metadata") or {}).get("kind") or "")
            activity["mission"] |= kind == "mission"
            activity["codeagent"] |= kind in {"codeagent", "agent_turn"}
            activity["video"] |= kind in {"video", "remotion"}
    except Exception:
        pass
    return activity


class PersonalTrainingScheduler:
    """One idempotent scheduler tick; it never creates or promotes a model."""

    def __init__(self, root: Path, *, activity_probe: ActivityProbe = runtime_activity) -> None:
        self.plane = PersonalModelControlPlane(root)
        self.activity_probe = activity_probe
        self.reconciliation = self.plane.reconcile_training_jobs(actor="personal-training-scheduler")

    @staticmethod
    def _age_minutes(timestamp: str) -> float:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        return max(0.0, (datetime.now(timezone.utc) - parsed).total_seconds() / 60.0)

    def tick(self) -> dict[str, Any]:
        settings = self.plane.settings_store.load()
        jobs = self.plane.jobs.list()
        run = next((item for item in jobs if item.state in {
            TrainingRunState.RUNNING, TrainingRunState.RESUMING, TrainingRunState.QUEUED,
            TrainingRunState.WAITING_IDLE, TrainingRunState.PAUSED,
        }), None)
        if run is None:
            return {"status": "idle", "reason_codes": ("no_training_job",)}
        idle_seconds = seconds_since_activity()
        snapshot = ResourceGovernor.probe(
            self.plane.root,
            idle_minutes=(idle_seconds / 60.0) if idle_seconds is not None else 10_000.0,
            activity_probe=self.activity_probe,
        )
        decision = ResourceGovernor().evaluate(settings, snapshot, manual=False)
        if run.state in {TrainingRunState.RUNNING, TrainingRunState.RESUMING}:
            timed_out = self._age_minutes(run.updated_at) >= settings.max_duration_minutes
            if timed_out or not decision.allowed:
                result = self.plane.pause_training(run.run_id, actor="personal-training-scheduler")
                return {"status": "pause_requested", "run_id": run.run_id, "reason_codes": ("max_duration_reached",) if timed_out else decision.reason_codes, "result": result}
            return {"status": "running", "run_id": run.run_id, "reason_codes": ()}
        if not decision.allowed:
            if run.state == TrainingRunState.QUEUED:
                self.plane.jobs.transition(run.run_id, TrainingRunState.WAITING_IDLE)
            return {"status": "waiting_idle", "run_id": run.run_id, "reason_codes": decision.reason_codes}
        if run.state == TrainingRunState.PAUSED:
            result = self.plane.resume_training(run.run_id, actor="personal-training-scheduler")
            if result.get("launched") is False:
                return {
                    "status": "waiting_idle",
                    "run_id": run.run_id,
                    "reason_codes": tuple(result.get("reason_codes") or ()),
                    "result": result,
                }
            return {"status": "resumed", "run_id": run.run_id, "reason_codes": (), "result": result}
        result = self.plane.launch_training(run.run_id, actor="personal-training-scheduler", manual=False)
        if result.get("launched") is False:
            return {
                "status": "waiting_idle",
                "run_id": run.run_id,
                "reason_codes": tuple(result.get("reason_codes") or ()),
                "result": result,
            }
        return {"status": "launched", "run_id": run.run_id, "reason_codes": (), "result": result}
