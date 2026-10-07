from __future__ import annotations

import pytest

from src.training.personal.contracts import TrainingRunState, TrainingRunV1, sha256_json, utc_now
from src.training.personal.job_store import TrainingJobStore


def _run(run_id="run_1"):
    now = utc_now()
    return TrainingRunV1(
        run_id=run_id, owner_scope="owner:local", lineage_id="lineage_1", target_version="1.0.0",
        dataset_manifest_hash=sha256_json("dataset"), training_config_hash=sha256_json("config"),
        state=TrainingRunState.QUEUED, created_at=now, updated_at=now,
    )


def test_job_transitions_are_durable_and_illegal_skips_fail(tmp_path) -> None:
    store = TrainingJobStore(tmp_path)
    store.create(_run())
    store.transition("run_1", TrainingRunState.RUNNING)
    store.transition("run_1", TrainingRunState.PAUSING)
    paused = store.transition("run_1", TrainingRunState.PAUSED, checkpoint_path="checkpoint-20")
    assert paused.checkpoint_path == "checkpoint-20"
    assert TrainingJobStore(tmp_path).get("run_1").state is TrainingRunState.PAUSED
    with pytest.raises(ValueError, match="invalid_transition"):
        store.transition("run_1", TrainingRunState.COMPLETED)


def test_restart_marks_only_nonterminal_jobs_interrupted(tmp_path) -> None:
    store = TrainingJobStore(tmp_path)
    store.create(_run("run_active"))
    store.transition("run_active", TrainingRunState.RUNNING)
    store.create(_run("run_done"))
    store.transition("run_done", TrainingRunState.RUNNING)
    store.transition("run_done", TrainingRunState.COMPLETED)
    assert store.reconcile_after_restart() == ["run_active"]
    assert store.get("run_active").state is TrainingRunState.UNKNOWN_INTERRUPTED
    assert store.get("run_done").state is TrainingRunState.COMPLETED
