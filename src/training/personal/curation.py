"""Canonical deterministic curation before any model-based judgment."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any

from .contracts import ExperienceState, LearningExperienceV1
from .experience_store import ExperienceStore
from .policy import PersonalLearningPolicy


_WORD_RE = re.compile(r"[a-z0-9]{3,}", re.I)
_FAILURE_FLAGS = frozenset({"negative_feedback", "negative_explicit", "incomplete", "failed", "cancelled"})


def semantic_fingerprint(experience: LearningExperienceV1) -> str:
    text = " ".join(str(item.get("content", "")) for item in experience.messages).lower()
    tokens = sorted(set(_WORD_RE.findall(text)))
    return hashlib.sha256(" ".join(tokens).encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True, slots=True)
class CurationDecision:
    experience_id: str
    target: ExperienceState
    score: float
    reason_codes: tuple[str, ...]
    semantic_cluster: str


@dataclass(slots=True)
class CurationReport:
    examined: int = 0
    candidates: int = 0
    rejected: int = 0
    quarantined: int = 0
    skipped: int = 0
    decisions: list[CurationDecision] = field(default_factory=list)


class PersonalCurationService:
    def __init__(self, store: ExperienceStore, policy: PersonalLearningPolicy) -> None:
        self.store = store
        self.policy = policy

    @staticmethod
    def evaluate(experience: LearningExperienceV1) -> CurationDecision:
        reasons: list[str] = []
        score = 0.0
        roles = [str(item.get("role", "")) for item in experience.messages]
        assistant_text = " ".join(str(item.get("content", "")) for item in experience.messages if item.get("role") == "assistant").strip()
        feedback_flag = str(experience.feedback.get("quality_flag", "")).lower()
        result_status = str(experience.result.get("status", "")).lower()

        if "user" not in roles or "assistant" not in roles or len(assistant_text) < 20:
            reasons.append("conversation_incomplete")
            return CurationDecision(experience.experience_id, ExperienceState.REJECTED, 0.0, tuple(reasons), semantic_fingerprint(experience))
        if feedback_flag in _FAILURE_FLAGS or result_status in _FAILURE_FLAGS:
            reasons.append("explicit_failure")
            return CurationDecision(experience.experience_id, ExperienceState.REJECTED, 0.0, tuple(reasons), semantic_fingerprint(experience))

        score += min(2.0, len(assistant_text) / 800.0)
        reasons.append("complete_exchange")
        if experience.evidence:
            score += 2.0
            reasons.append("evidence_present")
        if experience.actions and experience.observations:
            score += 1.5
            reasons.append("tool_trajectory_complete")
        if experience.feedback.get("rating") in {1, True, "positive"} or feedback_flag == "positive_explicit":
            score += 3.0
            reasons.append("positive_feedback")
        if experience.result.get("success") is True or result_status in {"success", "completed", "done"}:
            score += 1.5
            reasons.append("result_success")

        plan = experience.public_context.get("plan") if isinstance(experience.public_context.get("plan"), dict) else {}
        total = int(plan.get("plan_total", plan.get("total_tasks", 0)) or 0)
        done = int(plan.get("plan_done", plan.get("completed_tasks", 0)) or 0)
        if total > 0 and done == 0:
            reasons.append("plan_zero_completion")
            return CurationDecision(experience.experience_id, ExperienceState.REJECTED, 0.0, tuple(reasons), semantic_fingerprint(experience))
        if total > 0 and done >= total:
            score += 1.0
            reasons.append("plan_completed")

        target = ExperienceState.CANDIDATE if score >= 0.5 else ExperienceState.QUARANTINED
        return CurationDecision(experience.experience_id, target, round(score, 3), tuple(reasons), semantic_fingerprint(experience))

    def curate(self, owner_scope: str) -> CurationReport:
        report = CurationReport()
        if not self.policy.learning_enabled or not self.policy.local_capture_enabled:
            return report
        index = self.store._load_index(owner_scope)
        seen_clusters: dict[str, str] = {
            str(record["semantic_cluster"]): experience_id
            for experience_id, record in index["records"].items()
            if record.get("state") != ExperienceState.RAW.value and record.get("semantic_cluster")
        }
        for experience_id, record in sorted(index["records"].items()):
            if record.get("state") != ExperienceState.RAW.value:
                report.skipped += 1
                continue
            experience = self.store.get(owner_scope, experience_id)
            if experience is None:
                report.quarantined += 1
                continue
            decision = self.evaluate(experience)
            report.examined += 1
            if decision.semantic_cluster in seen_clusters:
                decision = CurationDecision(
                    experience_id,
                    ExperienceState.REJECTED,
                    decision.score,
                    decision.reason_codes + ("semantic_duplicate",),
                    decision.semantic_cluster,
                )
            else:
                seen_clusters[decision.semantic_cluster] = experience_id
            self.store.append_annotation(owner_scope, experience_id, kind="curation", payload={
                "score": decision.score,
                "reason_codes": list(decision.reason_codes),
                "semantic_cluster": decision.semantic_cluster,
            })
            self.store.transition(owner_scope, experience_id, decision.target, reason_code=decision.reason_codes[-1])
            report.decisions.append(decision)
            if decision.target is ExperienceState.CANDIDATE:
                report.candidates += 1
            elif decision.target is ExperienceState.REJECTED:
                report.rejected += 1
            else:
                report.quarantined += 1
        return report
