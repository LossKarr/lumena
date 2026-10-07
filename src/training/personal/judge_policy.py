"""Isolated, policy-driven judging for personal training experiences."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from enum import Enum
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from filelock import FileLock

from src.utils.persistence import atomic_write_json, safe_read_json

from .contracts import ExperienceState, LearningExperienceV1, canonical_json, sha256_json, utc_now
from .experience_store import ExperienceStore
from .policy import PersonalLearningPolicy
from .redaction import has_unredacted_sensitive_data


class JudgeMode(str, Enum):
    AUTO = "auto"
    PERSONAL = "personal"
    DEFAULT = "default"
    SPECIFIC = "specific"
    LOCAL_ONLY = "local_only"
    DOUBLE = "double"


@dataclass(frozen=True, slots=True)
class JudgePolicy:
    mode: JudgeMode = JudgeMode.AUTO
    default_model: str = ""
    personal_model: str = ""
    specific_model: str = ""
    personal_model_certified: bool = False
    cloud_allowed: bool = False
    rubric_version: str = "personal-quality-v1"
    accept_threshold: float = 7.0
    disagreement_tolerance: float = 1.5


@dataclass(frozen=True, slots=True)
class JudgeVerdict:
    decision: str
    score: float | None
    reason_codes: tuple[str, ...]
    judge_models: tuple[str, ...]
    evidence_hash: str
    collectable: bool = False
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


JudgeCallable = Callable[[str, dict[str, Any]], dict[str, Any]]


class JudgeCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._thread_lock = RLock()
        self._file_lock = FileLock(str(path) + ".lock", timeout=5)

    def get(self, key: str) -> dict[str, Any] | None:
        with self._thread_lock, self._file_lock:
            data = safe_read_json(self.path, default={})
            item = data.get("entries", {}).get(key) if isinstance(data, dict) else None
            return dict(item) if isinstance(item, dict) else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        with self._thread_lock, self._file_lock:
            data = safe_read_json(self.path, default={})
            if not isinstance(data, dict) or data.get("schema_version") != 1:
                data = {"schema_version": 1, "entries": {}}
            data.setdefault("entries", {})[key] = value
            atomic_write_json(self.path, data)


class JudgeEngine:
    """Builds bounded requests and never receives the active Lumena context."""

    REQUEST_KEYS = frozenset({
        "schema_version", "experience_id", "goal", "messages", "actions",
        "observations", "result", "evidence", "feedback", "rubric_version",
    })

    def __init__(self, judge_call: JudgeCallable, cache: JudgeCache | None = None) -> None:
        self.judge_call = judge_call
        self.cache = cache

    @staticmethod
    def _deterministic(experience: LearningExperienceV1) -> JudgeVerdict | None:
        flag = str(experience.feedback.get("quality_flag", "")).lower()
        if flag in {"negative_feedback", "negative_explicit", "incomplete"} or experience.result.get("success") is False:
            return JudgeVerdict("reject", 0.0, ("deterministic_failure",), ("deterministic",), sha256_json(experience.evidence), created_at=utc_now())
        positive = flag == "positive_explicit" or experience.feedback.get("rating") in {1, True, "positive"}
        proof_ok = bool(experience.evidence) and experience.result.get("success") is True
        if positive and proof_ok:
            return JudgeVerdict("accept", 10.0, ("explicit_positive_with_evidence",), ("deterministic",), sha256_json(experience.evidence), created_at=utc_now())
        return None

    @classmethod
    def build_request(cls, experience: LearningExperienceV1, rubric_version: str) -> dict[str, Any]:
        request = {
            "schema_version": 1,
            "experience_id": experience.experience_id,
            "goal": experience.goal,
            "messages": list(experience.messages),
            "actions": list(experience.actions),
            "observations": list(experience.observations),
            "result": experience.result,
            "evidence": list(experience.evidence),
            "feedback": experience.feedback,
            "rubric_version": rubric_version,
        }
        if set(request) != cls.REQUEST_KEYS:
            raise RuntimeError("judge_request_contract_invalid")
        if has_unredacted_sensitive_data(request):
            raise ValueError("judge_request_contains_sensitive_data")
        return request

    @staticmethod
    def _models(policy: JudgePolicy) -> list[str]:
        if policy.mode is JudgeMode.DEFAULT:
            return [policy.default_model] if policy.default_model else []
        if policy.mode is JudgeMode.SPECIFIC:
            return [policy.specific_model] if policy.specific_model else []
        if policy.mode is JudgeMode.PERSONAL:
            return [policy.personal_model] if policy.personal_model_certified and policy.personal_model else []
        if policy.mode is JudgeMode.LOCAL_ONLY:
            return [policy.personal_model] if policy.personal_model_certified and policy.personal_model else []
        if policy.mode is JudgeMode.DOUBLE:
            return list(dict.fromkeys(model for model in (policy.personal_model, policy.specific_model or policy.default_model) if model))
        if policy.personal_model_certified and policy.personal_model:
            return [policy.personal_model]
        return [policy.specific_model or policy.default_model] if (policy.specific_model or policy.default_model) else []

    @staticmethod
    def _parse(model: str, raw: dict[str, Any], threshold: float) -> tuple[float, str, tuple[str, ...]]:
        if not isinstance(raw, dict):
            raise ValueError("judge_response_not_object")
        score = float(raw.get("score"))
        if not 0 <= score <= 10:
            raise ValueError("judge_score_out_of_range")
        reasons_raw = raw.get("reason_codes", [])
        if not isinstance(reasons_raw, list) or not all(isinstance(item, str) for item in reasons_raw):
            raise ValueError("judge_reason_codes_invalid")
        decision = "accept" if score >= threshold else "reject"
        return score, decision, tuple(item[:96] for item in reasons_raw[:12])

    def judge(self, experience: LearningExperienceV1, policy: JudgePolicy) -> JudgeVerdict:
        deterministic = self._deterministic(experience)
        if deterministic is not None:
            return deterministic
        request = self.build_request(experience, policy.rubric_version)
        models = self._models(policy)
        if not models:
            return JudgeVerdict("quarantine", None, ("judge_unavailable",), (), sha256_json(experience.evidence), created_at=utc_now())

        verdicts: list[tuple[str, float, str, tuple[str, ...]]] = []
        for model in models:
            cache_key = sha256_json({"content_hash": experience.to_dict()["content_hash"], "rubric": policy.rubric_version, "model": model})
            raw = self.cache.get(cache_key) if self.cache else None
            if raw is None:
                raw = self.judge_call(model, json.loads(canonical_json(request)))
                if self.cache:
                    self.cache.put(cache_key, raw)
            score, decision, reasons = self._parse(model, raw, policy.accept_threshold)
            verdicts.append((model, score, decision, reasons))

        self_judged = len(verdicts) == 1 and verdicts[0][0] in {experience.teacher_model, policy.personal_model} and verdicts[0][0]
        if self_judged:
            return JudgeVerdict("quarantine", verdicts[0][1], ("self_judgment_requires_independent_proof",), (verdicts[0][0],), sha256_json(request), created_at=utc_now())
        if len(verdicts) >= 2:
            decisions = {item[2] for item in verdicts}
            scores = [item[1] for item in verdicts]
            if len(decisions) > 1 or max(scores) - min(scores) > policy.disagreement_tolerance:
                return JudgeVerdict("quarantine", sum(scores) / len(scores), ("judge_disagreement",), tuple(item[0] for item in verdicts), sha256_json(request), created_at=utc_now())
        average = sum(item[1] for item in verdicts) / len(verdicts)
        decision = "accept" if average >= policy.accept_threshold else "reject"
        reasons = tuple(reason for item in verdicts for reason in item[3]) or ("judge_score",)
        return JudgeVerdict(decision, round(average, 3), reasons, tuple(item[0] for item in verdicts), sha256_json(request), created_at=utc_now())


class PersonalJudgeService:
    def __init__(self, store: ExperienceStore, engine: JudgeEngine, governance: PersonalLearningPolicy) -> None:
        self.store = store
        self.engine = engine
        self.governance = governance

    def judge_experience(self, owner_scope: str, experience_id: str, policy: JudgePolicy) -> JudgeVerdict:
        experience = self.store.get(owner_scope, experience_id)
        if experience is None:
            raise KeyError("experience_not_found")
        if self.store._load_index(owner_scope)["records"][experience_id]["state"] != ExperienceState.CANDIDATE.value:
            raise ValueError("experience_not_candidate")
        if policy.cloud_allowed and not self.governance.cloud_judge_decision().allowed:
            raise PermissionError("cloud_judge_not_consented")
        self.store.transition(owner_scope, experience_id, ExperienceState.JUDGING, reason_code="judge_started")
        try:
            verdict = self.engine.judge(experience, policy)
        except Exception:
            self.store.transition(owner_scope, experience_id, ExperienceState.QUARANTINED, reason_code="judge_failed")
            raise
        target = {
            "accept": ExperienceState.ACCEPTED,
            "reject": ExperienceState.REJECTED,
            "quarantine": ExperienceState.QUARANTINED,
        }[verdict.decision]
        self.store.append_annotation(owner_scope, experience_id, kind="judge", payload=verdict.to_dict())
        self.store.transition(owner_scope, experience_id, target, reason_code=verdict.reason_codes[0])
        return verdict
