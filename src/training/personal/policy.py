"""Consent and governance policy for personal learning."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from filelock import FileLock

from src.utils.persistence import atomic_write_json, safe_read_json


@dataclass(frozen=True, slots=True)
class GovernanceDecision:
    allowed: bool
    reason_code: str


@dataclass(frozen=True, slots=True)
class PersonalLearningPolicy:
    learning_enabled: bool = False
    local_capture_enabled: bool = False
    cloud_judge_enabled: bool = False
    judge_mode: str = "auto"
    judge_model: str = ""
    judge_accept_threshold: float = 7.0
    judge_disagreement_tolerance: float = 1.5
    allowed_surfaces: frozenset[str] = field(default_factory=lambda: frozenset({
        "web", "agent", "mission", "voice", "telegram", "whatsapp", "discord", "ide"
    }))
    excluded_surfaces: frozenset[str] = frozenset()
    excluded_projects: frozenset[str] = frozenset()
    excluded_conversations: frozenset[str] = frozenset()
    allowed_license_policies: frozenset[str] = field(default_factory=lambda: frozenset({"user_owned", "provider_allowed", "local_private"}))

    def __post_init__(self) -> None:
        if self.judge_mode not in {"auto", "personal", "default", "specific", "local_only", "double"}:
            raise ValueError("judge_mode_invalid")
        if not 0 <= self.judge_accept_threshold <= 10:
            raise ValueError("judge_accept_threshold_invalid")
        if not 0 <= self.judge_disagreement_tolerance <= 10:
            raise ValueError("judge_disagreement_tolerance_invalid")

    def capture_decision(
        self,
        *,
        source_surface: str,
        project_id: str = "",
        conversation_id: str = "",
        internal: bool = False,
    ) -> GovernanceDecision:
        if internal:
            return GovernanceDecision(False, "internal_activity_excluded")
        if not self.learning_enabled:
            return GovernanceDecision(False, "personal_learning_disabled")
        if not self.local_capture_enabled:
            return GovernanceDecision(False, "local_capture_not_consented")
        if source_surface not in self.allowed_surfaces or source_surface in self.excluded_surfaces:
            return GovernanceDecision(False, "surface_excluded")
        if project_id and project_id in self.excluded_projects:
            return GovernanceDecision(False, "project_excluded")
        if conversation_id and conversation_id in self.excluded_conversations:
            return GovernanceDecision(False, "conversation_excluded")
        return GovernanceDecision(True, "capture_allowed")

    def cloud_judge_decision(self) -> GovernanceDecision:
        if not self.learning_enabled:
            return GovernanceDecision(False, "personal_learning_disabled")
        if not self.cloud_judge_enabled:
            return GovernanceDecision(False, "cloud_judge_not_consented")
        return GovernanceDecision(True, "cloud_judge_allowed")

    def dataset_license_decision(self, license_policy: str) -> GovernanceDecision:
        if license_policy not in self.allowed_license_policies:
            return GovernanceDecision(False, "license_policy_not_allowed")
        return GovernanceDecision(True, "license_policy_allowed")

    def to_dict(self) -> dict:
        return {
            "learning_enabled": self.learning_enabled,
            "local_capture_enabled": self.local_capture_enabled,
            "cloud_judge_enabled": self.cloud_judge_enabled,
            "judge_mode": self.judge_mode,
            "judge_model": self.judge_model,
            "judge_accept_threshold": self.judge_accept_threshold,
            "judge_disagreement_tolerance": self.judge_disagreement_tolerance,
            "allowed_surfaces": sorted(self.allowed_surfaces),
            "excluded_surfaces": sorted(self.excluded_surfaces),
            "excluded_projects": sorted(self.excluded_projects),
            "excluded_conversations": sorted(self.excluded_conversations),
            "allowed_license_policies": sorted(self.allowed_license_policies),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PersonalLearningPolicy":
        payload = dict(data or {})
        for key in ("allowed_surfaces", "excluded_surfaces", "excluded_projects", "excluded_conversations", "allowed_license_policies"):
            if key in payload:
                payload[key] = frozenset(str(value) for value in payload[key])
        return cls(**payload)


class PersonalLearningPolicyStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = FileLock(str(self.path) + ".lock", timeout=10)

    def load(self) -> PersonalLearningPolicy:
        with self._lock:
            data = safe_read_json(self.path, default={})
        try:
            return PersonalLearningPolicy.from_dict(data.get("policy", data)) if data else PersonalLearningPolicy()
        except (TypeError, ValueError):
            return PersonalLearningPolicy()

    def save(self, policy: PersonalLearningPolicy) -> PersonalLearningPolicy:
        with self._lock:
            atomic_write_json(self.path, {"schema_version": 1, "policy": policy.to_dict()})
        return policy
