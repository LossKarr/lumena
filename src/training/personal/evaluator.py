"""Side-effect-free comparative evaluation and promotion gates."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Callable, Iterable

from .contracts import new_id, sha256_json, utc_now


GenerateCallable = Callable[[str, dict[str, Any]], str]
ScoreCallable = Callable[[str, dict[str, Any]], float]


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    case_id: str
    domain: str
    prompt: str
    metadata: dict[str, Any]
    critical: bool = False


@dataclass(frozen=True, slots=True)
class EvaluationReportV1:
    evaluation_id: str
    baseline_model: str
    candidate_model: str
    case_set_hash: str
    baseline_scores: dict[str, float]
    candidate_scores: dict[str, float]
    domain_deltas: dict[str, float]
    overall_delta: float
    critical_regressions: tuple[str, ...]
    passed: bool
    created_at: str
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SafeComparativeEvaluator:
    """Scores returned text as data and never executes model-generated code."""

    def __init__(self, generate: GenerateCallable, score: ScoreCallable) -> None:
        self.generate = generate
        self.score = score

    def evaluate(
        self,
        baseline_model: str,
        candidate_model: str,
        cases: Iterable[EvaluationCase],
        *,
        max_critical_drop: float = 0.0,
        min_overall_delta: float = 0.0,
    ) -> EvaluationReportV1:
        case_list = list(cases)
        if not case_list:
            raise ValueError("evaluation_cases_required")
        baseline_by_domain: dict[str, list[float]] = {}
        candidate_by_domain: dict[str, list[float]] = {}
        critical_domains = {case.domain for case in case_list if case.critical}
        for case in case_list:
            # The output is passed only to a scorer.  No eval/exec/subprocess or
            # host tool receives generated text through this contract.
            baseline_output = self.generate(baseline_model, {"prompt": case.prompt, "metadata": case.metadata})
            candidate_output = self.generate(candidate_model, {"prompt": case.prompt, "metadata": case.metadata})
            baseline_by_domain.setdefault(case.domain, []).append(float(self.score(baseline_output, case.metadata)))
            candidate_by_domain.setdefault(case.domain, []).append(float(self.score(candidate_output, case.metadata)))
        baseline_scores = {domain: round(sum(values) / len(values), 4) for domain, values in baseline_by_domain.items()}
        candidate_scores = {domain: round(sum(values) / len(values), 4) for domain, values in candidate_by_domain.items()}
        deltas = {domain: round(candidate_scores[domain] - baseline_scores[domain], 4) for domain in baseline_scores}
        critical = tuple(sorted(domain for domain in critical_domains if deltas.get(domain, 0.0) < -abs(max_critical_drop)))
        overall = round(sum(deltas.values()) / len(deltas), 4)
        passed = not critical and overall >= min_overall_delta
        case_hash = sha256_json([asdict(case) for case in case_list])
        return EvaluationReportV1(
            evaluation_id=new_id("eval"), baseline_model=baseline_model, candidate_model=candidate_model,
            case_set_hash=case_hash, baseline_scores=baseline_scores, candidate_scores=candidate_scores,
            domain_deltas=deltas, overall_delta=overall, critical_regressions=critical,
            passed=passed, created_at=utc_now(),
        )


def require_promotable(report: EvaluationReportV1 | None) -> EvaluationReportV1:
    if report is None:
        raise ValueError("promotion_requires_baseline_evaluation")
    if not report.passed:
        raise ValueError("promotion_blocked_by_evaluation")
    if report.critical_regressions:
        raise ValueError("promotion_blocked_by_critical_regression")
    return report
