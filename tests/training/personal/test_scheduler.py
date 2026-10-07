from __future__ import annotations

from src.training.personal.contracts import TrainingRunState, TrainingRunV1, sha256_json, utc_now
from src.training.personal.job_store import TrainingJobStore
from src.training.personal.resource_governor import TrainingSettings
from src.training.personal.scheduler import PersonalTrainingScheduler


def _job(root, state=TrainingRunState.QUEUED):
    now = utc_now()
    run = TrainingRunV1(
        run_id="run_1", owner_scope="owner:local", lineage_id="lineage_1", target_version="1.0.0",
        dataset_manifest_hash=sha256_json("dataset"), training_config_hash=sha256_json("config"),
        state=state, created_at=now, updated_at=now,
    )
    TrainingJobStore(root).create(run)
    return run


def test_scheduler_waits_while_interactive_work_is_active(tmp_path):
    run = _job(tmp_path)
    scheduler = PersonalTrainingScheduler(tmp_path, activity_probe=lambda: {"agent": True})
    scheduler.plane.settings_store.save(TrainingSettings(enabled=True, automatic=True, trigger="auto", min_idle_minutes=0))
    result = scheduler.tick()
    assert result["status"] == "waiting_idle"
    assert "interactive_work_active" in result["reason_codes"]
    assert scheduler.plane.jobs.get(run.run_id).state == TrainingRunState.WAITING_IDLE


def test_scheduler_launches_waiting_job_when_machine_is_ready(tmp_path, monkeypatch):
    run = _job(tmp_path, TrainingRunState.WAITING_IDLE)
    scheduler = PersonalTrainingScheduler(tmp_path, activity_probe=lambda: {})
    scheduler.plane.settings_store.save(TrainingSettings(enabled=True, automatic=True, trigger="auto", min_idle_minutes=0, min_free_disk_gb=0))
    launched = {}
    monkeypatch.setattr(scheduler.plane, "launch_training", lambda run_id, **kw: launched.update(run_id=run_id, **kw) or {"launched": True})
    result = scheduler.tick()
    assert result["status"] == "launched"
    assert launched["run_id"] == run.run_id
    assert launched["manual"] is False


def test_scheduler_reports_dependency_block_instead_of_false_launch(tmp_path, monkeypatch):
    run = _job(tmp_path, TrainingRunState.WAITING_IDLE)
    scheduler = PersonalTrainingScheduler(tmp_path, activity_probe=lambda: {})
    scheduler.plane.settings_store.save(TrainingSettings(enabled=True, automatic=True, trigger="auto", min_idle_minutes=0, min_free_disk_gb=0))
    monkeypatch.setattr(
        scheduler.plane,
        "launch_training",
        lambda *_args, **_kwargs: {
            "launched": False,
            "state": "waiting_idle",
            "reason_codes": ("training_dependencies_missing",),
        },
    )

    result = scheduler.tick()

    assert result["status"] == "waiting_idle"
    assert result["reason_codes"] == ("training_dependencies_missing",)
    assert result["run_id"] == run.run_id
