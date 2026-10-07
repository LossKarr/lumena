"""Evaluation-gated promotion for personal model versions."""

from __future__ import annotations

from dataclasses import replace

from .contracts import ModelVersionState, PersonalModelLineageV1
from .evaluator import EvaluationReportV1, require_promotable
from .lineage_store import LineageStore


class PromotionService:
    def __init__(self, lineages: LineageStore) -> None:
        self.lineages = lineages

    def promote_personal(
        self,
        record: PersonalModelLineageV1,
        report: EvaluationReportV1 | None,
        *,
        approval_id: str,
    ) -> PersonalModelLineageV1:
        checked = require_promotable(report)
        if checked.candidate_model != record.model_name:
            raise ValueError("evaluation_candidate_mismatch")
        if record.evaluation_id != checked.evaluation_id:
            raise ValueError("evaluation_id_mismatch")
        if record.status not in {ModelVersionState.EVALUATED, ModelVersionState.AVAILABLE}:
            raise ValueError("candidate_not_evaluated")
        return self.lineages.activate_personal(record.lineage_id, record.version, approval_id=approval_id)
