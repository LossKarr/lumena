from __future__ import annotations

import json

import pytest

from src.training.personal.contracts import TrainingRunState, TrainingRunV1, sha256_json, utc_now
from src.training.personal.evaluator import EvaluationCase, SafeComparativeEvaluator
from src.training.personal.job_store import TrainingJobStore
from src.training.personal.lifecycle import PersonalModelLifecycle
from src.training.personal.lineage_store import LineageStore


def _running(tmp_path):
    lineage_store = LineageStore(tmp_path)
    lineage_id = lineage_store.create(owner_scope="owner:local", display_prefix="lumena")
    reservation = lineage_store.reserve_next(lineage_id)
    now = utc_now()
    run = TrainingRunV1(
        run_id="run_1", owner_scope="owner:local", lineage_id=lineage_id,
        target_version=reservation["version"], dataset_manifest_hash=sha256_json("dataset"),
        training_config_hash=sha256_json("config"), state=TrainingRunState.RUNNING,
        created_at=now, updated_at=now,
    )
    TrainingJobStore(tmp_path).create(run)
    run_dir = tmp_path / "runs" / run.run_id
    (run_dir / "reservation.json").write_text(json.dumps(reservation), encoding="utf-8")
    (run_dir / "training-config.json").write_text(json.dumps({"base_model_hf_id": "base/model"}), encoding="utf-8")
    (run_dir / "provenance.json").write_text(json.dumps({"base_revision": "abc123", "tokenizer_revision": "tok123"}), encoding="utf-8")
    adapter = run_dir / "artifacts" / "adapter"
    adapter.mkdir(parents=True)
    (adapter / "adapter_model.safetensors").write_bytes(b"adapter")
    return run, reservation, adapter


def test_training_completion_registers_reproducible_candidate_idempotently(tmp_path):
    run, _, adapter = _running(tmp_path)
    lifecycle = PersonalModelLifecycle(tmp_path)
    candidate = lifecycle.finalize_training(run.run_id, adapter_path=adapter)
    assert candidate.model_name == "lumena-model-1.0.0"
    assert candidate.status.value == "candidate"
    assert candidate.base_revision == "abc123"
    assert TrainingJobStore(tmp_path).get(run.run_id).state == TrainingRunState.COMPLETED
    assert lifecycle.finalize_training(run.run_id, adapter_path=adapter) == candidate


def test_completion_rejects_artifact_outside_run(tmp_path):
    run, _, _ = _running(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "adapter.bin").write_bytes(b"x")
    with pytest.raises(PermissionError, match="outside"):
        PersonalModelLifecycle(tmp_path).finalize_training(run.run_id, adapter_path=outside)


def test_evaluation_persists_proof_and_only_passed_candidate_becomes_evaluated(tmp_path):
    run, _, adapter = _running(tmp_path)
    lifecycle = PersonalModelLifecycle(tmp_path)
    candidate = lifecycle.finalize_training(run.run_id, adapter_path=adapter)
    evaluator = SafeComparativeEvaluator(
        lambda model, request: "good" if model == candidate.model_name else "ok",
        lambda output, metadata: {"ok": 0.5, "good": 1.0}[output],
    )
    updated, report = lifecycle.evaluate_candidate(
        candidate.lineage_id, candidate.version, baseline_model="base/model",
        cases=[EvaluationCase("case-1", "security", "bounded prompt", {}, critical=True)],
        evaluator=evaluator,
    )
    assert report.passed is True
    assert updated.status.value == "evaluated"
    assert updated.evaluation_id == report.evaluation_id
    assert (tmp_path / "evaluations" / f"{report.evaluation_id}.json").is_file()
