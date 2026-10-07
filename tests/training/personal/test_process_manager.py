from __future__ import annotations

import sys

from src.utils.persistence import atomic_write_json

from src.training.personal.contracts import TrainingRunState, TrainingRunV1, sha256_json, utc_now
from src.training.personal.job_store import TrainingJobStore
from src.training.personal.process_manager import TrainingProcessManager


class _FakeProcess:
    pid = 4242

    def poll(self):
        return None


def _run():
    now = utc_now()
    return TrainingRunV1(
        run_id="run_safe", owner_scope="owner:local", lineage_id="lineage", target_version="1.0.0",
        dataset_manifest_hash=sha256_json("dataset"), training_config_hash=sha256_json("config"),
        state=TrainingRunState.QUEUED, created_at=now, updated_at=now,
    )


def test_manager_uses_fixed_worker_without_shell_and_persists_controls(tmp_path) -> None:
    jobs = TrainingJobStore(tmp_path)
    jobs.create(_run())
    calls = []

    def fake_popen(command, **kwargs):
        calls.append((command, kwargs))
        return _FakeProcess()

    manager = TrainingProcessManager(tmp_path, jobs, popen_factory=fake_popen)
    result = manager.launch("run_safe", {"backend": "canonical_sft_v1"})
    command, kwargs = calls[0]
    assert command[:3] == [sys.executable, "-m", "src.training.personal.worker"]
    assert kwargs["shell"] is False
    assert result["pid"] == 4242
    assert manager.request_pause("run_safe")["state"] == "pausing"
    assert manager.control_signal("run_safe") == "pause"


def test_manager_resumes_only_from_a_persisted_checkpoint(tmp_path) -> None:
    jobs = TrainingJobStore(tmp_path)
    jobs.create(_run())
    jobs.transition("run_safe", TrainingRunState.RUNNING)
    checkpoint = tmp_path / "runs" / "run_safe" / "checkpoint-3"
    checkpoint.mkdir(parents=True)
    jobs.transition("run_safe", TrainingRunState.PAUSING)
    jobs.transition("run_safe", TrainingRunState.PAUSED, checkpoint_path=str(checkpoint))
    calls = []

    def fake_popen(command, **kwargs):
        calls.append(command)
        return _FakeProcess()

    manager = TrainingProcessManager(tmp_path, jobs, popen_factory=fake_popen)
    result = manager.resume("run_safe", {"backend": "canonical_sft_v1", "finetune": {}})
    assert result["state"] == "running"
    payload = __import__("json").loads((tmp_path / "runs" / "run_safe" / "worker_config.json").read_text(encoding="utf-8"))
    assert payload["finetune"]["resume_from_checkpoint"] == str(checkpoint)


def test_restart_reconciliation_preserves_verified_live_worker(tmp_path) -> None:
    jobs = TrainingJobStore(tmp_path)
    jobs.create(_run())
    jobs.transition("run_safe", TrainingRunState.RUNNING)
    atomic_write_json(tmp_path / "runs" / "run_safe" / "process.json", {
        "schema_version": 1, "pid": 4242, "command_kind": "personal_training_worker",
    })
    manager = TrainingProcessManager(tmp_path, jobs, worker_alive_probe=lambda pid, run_id: pid == 4242 and run_id == "run_safe")

    assert manager.reconcile_orphans() == {"alive": ["run_safe"], "interrupted": []}
    assert jobs.get("run_safe").state is TrainingRunState.RUNNING


def test_restart_reconciliation_marks_only_missing_worker_interrupted(tmp_path) -> None:
    jobs = TrainingJobStore(tmp_path)
    jobs.create(_run())
    jobs.transition("run_safe", TrainingRunState.RUNNING)
    atomic_write_json(tmp_path / "runs" / "run_safe" / "process.json", {
        "schema_version": 1, "pid": 9999, "command_kind": "personal_training_worker",
    })
    manager = TrainingProcessManager(tmp_path, jobs, worker_alive_probe=lambda _pid, _run_id: False)

    assert manager.reconcile_orphans() == {"alive": [], "interrupted": ["run_safe"]}
    recovered = jobs.get("run_safe")
    assert recovered.state is TrainingRunState.UNKNOWN_INTERRUPTED
    assert recovered.error_code == "worker_process_missing_after_restart"


def test_cancel_without_live_worker_becomes_terminal_immediately(tmp_path) -> None:
    jobs = TrainingJobStore(tmp_path)
    jobs.create(_run())
    jobs.transition("run_safe", TrainingRunState.RUNNING)
    manager = TrainingProcessManager(tmp_path, jobs, worker_alive_probe=lambda _pid, _run_id: False)

    result = manager.request_cancel("run_safe")

    assert result["state"] == "cancelled"
    assert jobs.get("run_safe").state is TrainingRunState.CANCELLED


def test_cancel_paused_job_does_not_wait_for_absent_worker(tmp_path) -> None:
    jobs = TrainingJobStore(tmp_path)
    jobs.create(_run())
    jobs.transition("run_safe", TrainingRunState.RUNNING)
    jobs.transition("run_safe", TrainingRunState.PAUSING)
    jobs.transition("run_safe", TrainingRunState.PAUSED, checkpoint_path="checkpoint-1")
    manager = TrainingProcessManager(tmp_path, jobs)

    assert manager.request_cancel("run_safe")["state"] == "cancelled"
