"""Bounded curation and judgment cycle for captured personal experiences."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from .contracts import ExperienceState
from .curation import PersonalCurationService
from .experience_store import ExperienceStore
from .judge_policy import JudgeCache, JudgeEngine, JudgeMode, JudgePolicy, PersonalJudgeService
from .lineage_store import LineageStore
from .policy import PersonalLearningPolicyStore
from .provider_judge import DedicatedJudgeAdapter


@dataclass(frozen=True, slots=True)
class LearningCycleReport:
    curated: int
    candidates: int
    accepted: int
    rejected: int
    quarantined: int
    skipped: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class PersonalLearningCycle:
    def __init__(self, root: Path, *, judge_call: Callable | None = None, owner_scope: str = "owner:local") -> None:
        self.root = Path(root).resolve()
        self.owner_scope = owner_scope
        self.store = ExperienceStore(self.root)
        self.policy_store = PersonalLearningPolicyStore(self.root / "config" / "policy.json")
        self.lineages = LineageStore(self.root)
        self.judge_call = judge_call

    def _judge_policy(self) -> JudgePolicy:
        governance = self.policy_store.load()
        active = self.lineages.active()
        personal = active.model_name if active else ""
        certified = bool(active and "ollama_canary" in active.artifact_hashes and active.evaluation_id)
        principal = (os.getenv("LUMENA_DEFAULT_MODEL") or "deepseek-flash").strip()
        return JudgePolicy(
            mode=JudgeMode(governance.judge_mode),
            default_model=principal,
            personal_model=personal,
            specific_model=governance.judge_model,
            personal_model_certified=certified,
            cloud_allowed=governance.cloud_judge_enabled,
            accept_threshold=governance.judge_accept_threshold,
            disagreement_tolerance=governance.judge_disagreement_tolerance,
        )

    def run(self, *, max_judgments: int = 10) -> LearningCycleReport:
        governance = self.policy_store.load()
        if not governance.learning_enabled or not governance.local_capture_enabled:
            return LearningCycleReport(0, 0, 0, 0, 0, 0)
        curated = PersonalCurationService(self.store, governance).curate(self.owner_scope)
        judge_call = self.judge_call or DedicatedJudgeAdapter(cloud_allowed=governance.cloud_judge_enabled)
        service = PersonalJudgeService(
            self.store,
            JudgeEngine(judge_call, JudgeCache(self.root / "judge" / "cache.json")),
            governance,
        )
        policy = self._judge_policy()
        index = self.store._load_index(self.owner_scope)
        candidate_ids = [
            experience_id for experience_id, record in sorted(index["records"].items())
            if record.get("state") == ExperienceState.CANDIDATE.value
        ][:max(0, min(int(max_judgments), 100))]
        accepted = rejected = quarantined = 0
        for experience_id in candidate_ids:
            verdict = service.judge_experience(self.owner_scope, experience_id, policy)
            accepted += verdict.decision == "accept"
            rejected += verdict.decision == "reject"
            quarantined += verdict.decision == "quarantine"
        return LearningCycleReport(
            curated=curated.examined,
            candidates=len(candidate_ids),
            accepted=accepted,
            rejected=curated.rejected + rejected,
            quarantined=curated.quarantined + quarantined,
            skipped=curated.skipped,
        )
