from __future__ import annotations

import pytest

from src.training.personal.evaluator import EvaluationCase, SafeComparativeEvaluator, require_promotable


def test_evaluator_compares_without_executing_generated_text(monkeypatch) -> None:
    generated = {("base", "code"): "raise SystemExit('must not run')", ("candidate", "code"): "safe answer"}
    evaluator = SafeComparativeEvaluator(
        lambda model, request: generated[(model, request["metadata"]["key"])],
        lambda output, metadata: 1.0 if output == "safe answer" else 0.0,
    )
    report = evaluator.evaluate("base", "candidate", [EvaluationCase("c1", "code", "write code", {"key": "code"}, critical=True)])
    assert report.passed is True
    assert report.overall_delta == 1.0
    assert require_promotable(report) is report


def test_critical_regression_blocks_promotion() -> None:
    evaluator = SafeComparativeEvaluator(
        lambda model, request: model,
        lambda output, metadata: 1.0 if output == "base" else 0.0,
    )
    report = evaluator.evaluate("base", "candidate", [EvaluationCase("c1", "security", "test", {}, critical=True)])
    assert report.passed is False
    with pytest.raises(ValueError, match="evaluation"):
        require_promotable(report)
