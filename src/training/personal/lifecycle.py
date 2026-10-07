"""Transactional completion and evaluation of personal-model versions."""

from __future__ import annotations

import hashlib
import importlib.metadata
from pathlib import Path
from typing import Iterable

from src.utils.persistence import atomic_write_json, safe_read_json

from .contracts import ModelVersionState, PersonalModelLineageV1, TrainingRunState, utc_now
from .evaluator import EvaluationCase, EvaluationReportV1, SafeComparativeEvaluator
from .job_store import TrainingJobStore
from .lineage_store import LineageStore


def hash_artifact_tree(path: Path) -> str:
    root = Path(path).resolve()
    if not root.is_dir():
        raise FileNotFoundError("training_adapter_missing")
    digest = hashlib.sha256()
    files = sorted(item for item in root.rglob("*") if item.is_file())
    if not files:
        raise ValueError("training_adapter_empty")
    for item in files:
        relative = item.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        with item.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    return digest.hexdigest()


def dependency_manifest() -> dict[str, str]:
    result: dict[str, str] = {}
    for name in ("torch", "datasets", "transformers", "trl", "peft", "unsloth"):
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = "missing"
    return result


class PersonalModelLifecycle:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.jobs = TrainingJobStore(self.root)
        self.lineages = LineageStore(self.root)

    def finalize_training(self, run_id: str, *, adapter_path: Path) -> PersonalModelLineageV1:
        run = self.jobs.get(run_id)
        if run is None:
            raise KeyError("training_run_not_found")
        if run.state not in {TrainingRunState.RUNNING, TrainingRunState.RESUMING, TrainingRunState.COMPLETED}:
            raise ValueError("training_run_not_finalizable")
        run_dir = (self.root / "runs" / run_id).resolve()
        adapter = Path(adapter_path).resolve()
        if run_dir != adapter and run_dir not in adapter.parents:
            raise PermissionError("training_artifact_outside_run")
        reservation = safe_read_json(run_dir / "reservation.json", default={})
        config = safe_read_json(run_dir / "training-config.json", default={})
        provenance = safe_read_json(run_dir / "provenance.json", default={})
        if not all(isinstance(value, dict) for value in (reservation, config, provenance)):
            raise ValueError("training_provenance_invalid")
        if reservation.get("version") != run.target_version or reservation.get("lineage_id") != run.lineage_id:
            raise ValueError("training_reservation_mismatch")
        adapter_hash = hash_artifact_tree(adapter)
        artifact_hashes = {"adapter": adapter_hash}
        try:
            existing = self.lineages.get(run.lineage_id, run.target_version)
        except KeyError:
            versions = self.lineages.versions(run.lineage_id)
            parent = str(versions[-1].version) if versions else None
            existing = PersonalModelLineageV1(
                lineage_id=run.lineage_id,
                owner_scope=run.owner_scope,
                display_prefix=str(provenance.get("display_prefix") or "lumena"),
                version=run.target_version,
                model_name=str(reservation.get("model_name") or ""),
                parent_version=parent,
                base_model_id=str(provenance.get("base_model_id") or config.get("base_model_hf_id") or "unknown"),
                base_revision=str(provenance.get("base_revision") or "unresolved"),
                tokenizer_revision=str(provenance.get("tokenizer_revision") or "unresolved"),
                adapter_method=str(provenance.get("adapter_method") or "lora"),
                dataset_manifest_hash=run.dataset_manifest_hash,
                training_config_hash=run.training_config_hash,
                dependency_manifest=dependency_manifest(),
                artifact_hashes=artifact_hashes,
                evaluation_id=None,
                created_at=utc_now(),
                status=ModelVersionState.CANDIDATE,
            )
            self.lineages.register(existing, reservation_id=str(reservation.get("reservation_id") or ""))
        else:
            if existing.artifact_hashes.get("adapter") != adapter_hash:
                raise ValueError("training_completion_hash_mismatch")
        atomic_write_json(run_dir / "completion-proof.json", {
            "schema_version": 1,
            "run_id": run_id,
            "lineage_id": run.lineage_id,
            "version": run.target_version,
            "artifact_hashes": artifact_hashes,
        })
        if run.state != TrainingRunState.COMPLETED:
            self.jobs.transition(run_id, TrainingRunState.COMPLETED, artifact_hashes=artifact_hashes)
        return existing

    def evaluate_candidate(
        self,
        lineage_id: str,
        version: str,
        *,
        baseline_model: str,
        cases: Iterable[EvaluationCase],
        evaluator: SafeComparativeEvaluator,
        max_critical_drop: float = 0.0,
        min_overall_delta: float = 0.0,
    ) -> tuple[PersonalModelLineageV1, EvaluationReportV1]:
        candidate = self.lineages.get(lineage_id, version)
        if candidate.status != ModelVersionState.CANDIDATE:
            raise ValueError("candidate_not_pending_evaluation")
        report = evaluator.evaluate(
            baseline_model,
            candidate.model_name,
            cases,
            max_critical_drop=max_critical_drop,
            min_overall_delta=min_overall_delta,
        )
        report_path = self.root / "evaluations" / f"{report.evaluation_id}.json"
        atomic_write_json(report_path, report.to_dict())
        status = ModelVersionState.EVALUATED if report.passed else ModelVersionState.REJECTED
        updated = self.lineages.update_candidate(
            lineage_id,
            version,
            status=status,
            evaluation_id=report.evaluation_id,
            artifact_hashes={**candidate.artifact_hashes, "evaluation": hashlib.sha256(report_path.read_bytes()).hexdigest()},
        )
        return updated, report
