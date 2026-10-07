from __future__ import annotations

import pytest

from src.training.personal.evaluation_service import VersionEvaluationService


class _Lineages:
    def get(self, lineage_id, version):
        return type("Candidate", (), {"model_name": "lumena-model-1.0.0", "artifact_hashes": {"ollama_canary": "proof"}})()


class _Lifecycle:
    lineages = _Lineages()

    def evaluate_candidate(self, lineage_id, version, **kwargs):
        return "updated", kwargs["evaluator"].evaluate(kwargs["baseline_model"], "lumena-model-1.0.0", kwargs["cases"])


def test_version_evaluation_uses_public_cases_and_independent_judge():
    seen = []
    service = VersionEvaluationService(
        _Lifecycle(),
        generate=lambda model, request: f"answer:{model}",
        judge=lambda model, request: seen.append((model, request)) or {"score": 8.0, "reason_codes": ["ok"]},
    )
    updated, report = service.evaluate("lineage", "1.0.0", baseline_model="base", judge_model="judge", cloud_allowed=False)
    assert updated == "updated"
    assert report.passed is True
    assert seen and all(item[0] == "judge" for item in seen)
    assert all(set(item[1]) == {"schema_version", "rubric", "criterion", "candidate_output"} for item in seen)


def test_candidate_cannot_be_its_only_judge():
    with pytest.raises(ValueError, match="independent"):
        VersionEvaluationService(_Lifecycle(), generate=lambda *_: "x", judge=lambda *_: {"score": 10}).evaluate(
            "lineage", "1.0.0", baseline_model="base", judge_model="lumena-model-1.0.0", cloud_allowed=False,
        )
