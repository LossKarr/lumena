from __future__ import annotations

import pytest

from src.training.personal.contracts import ExperienceState, LearningExperienceV1, new_id, utc_now
from src.training.personal.experience_store import ExperienceStore
from src.training.personal.judge_policy import JudgeCache, JudgeEngine, JudgeMode, JudgePolicy, PersonalJudgeService
from src.training.personal.policy import PersonalLearningPolicy


def _candidate(*, teacher_model="teacher", feedback=None, result=None, evidence=()):
    return LearningExperienceV1(
        experience_id=new_id("exp"), owner_scope="owner:local", source_surface="web", mode="chat",
        created_at=utc_now(), goal="evaluate", public_context={},
        messages=({"role": "user", "content": "question"}, {"role": "assistant", "content": "answer with details"}),
        result=result or {}, evidence=evidence, feedback=feedback or {}, teacher_model=teacher_model,
        quality_state=ExperienceState.CANDIDATE, privacy_state="redacted", license_policy="user_owned",
    )


def test_judge_request_is_bounded_and_does_not_accept_active_context() -> None:
    exp = _candidate()
    request = JudgeEngine.build_request(exp, "rubric-v1")
    assert set(request) == JudgeEngine.REQUEST_KEYS
    assert "public_context" not in request
    assert "memory" not in request
    assert "tools" not in request


def test_deterministic_positive_proof_does_not_call_model(tmp_path) -> None:
    calls = []
    engine = JudgeEngine(lambda model, request: calls.append((model, request)) or {"score": 0, "reason_codes": []})
    verdict = engine.judge(_candidate(feedback={"rating": 1}, result={"success": True}, evidence=({"test": "passed"},)), JudgePolicy(default_model="judge"))
    assert verdict.decision == "accept"
    assert verdict.judge_models == ("deterministic",)
    assert calls == []


def test_personal_model_cannot_be_its_only_judge() -> None:
    engine = JudgeEngine(lambda model, request: {"score": 9, "reason_codes": ["looks_good"]})
    verdict = engine.judge(
        _candidate(teacher_model="lumena-model-1.0.0"),
        JudgePolicy(mode=JudgeMode.PERSONAL, personal_model="lumena-model-1.0.0", personal_model_certified=True),
    )
    assert verdict.decision == "quarantine"
    assert verdict.reason_codes == ("self_judgment_requires_independent_proof",)


def test_double_judge_disagreement_is_quarantined() -> None:
    scores = {"personal": 9, "external": 3}
    engine = JudgeEngine(lambda model, request: {"score": scores[model], "reason_codes": [model]})
    verdict = engine.judge(
        _candidate(),
        JudgePolicy(mode=JudgeMode.DOUBLE, personal_model="personal", specific_model="external", personal_model_certified=True),
    )
    assert verdict.decision == "quarantine"
    assert verdict.reason_codes == ("judge_disagreement",)


def test_judge_cache_avoids_duplicate_provider_call(tmp_path) -> None:
    calls = []
    engine = JudgeEngine(lambda model, request: calls.append(model) or {"score": 8, "reason_codes": ["ok"]}, JudgeCache(tmp_path / "cache.json"))
    policy = JudgePolicy(mode=JudgeMode.SPECIFIC, specific_model="external")
    exp = _candidate()
    assert engine.judge(exp, policy).decision == "accept"
    assert engine.judge(exp, policy).decision == "accept"
    assert calls == ["external"]


def test_service_updates_store_and_cloud_requires_separate_consent(tmp_path) -> None:
    store = ExperienceStore(tmp_path)
    exp = _candidate()
    store.append(exp)
    engine = JudgeEngine(lambda model, request: {"score": 8, "reason_codes": ["ok"]})
    governance = PersonalLearningPolicy(learning_enabled=True, local_capture_enabled=True, cloud_judge_enabled=False)
    service = PersonalJudgeService(store, engine, governance)
    with pytest.raises(PermissionError, match="cloud_judge_not_consented"):
        service.judge_experience("owner:local", exp.experience_id, JudgePolicy(mode=JudgeMode.SPECIFIC, specific_model="cloud", cloud_allowed=True))
    verdict = service.judge_experience("owner:local", exp.experience_id, JudgePolicy(mode=JudgeMode.SPECIFIC, specific_model="local"))
    assert verdict.decision == "accept"
    assert store.stats("owner:local")["counts"] == {"accepted": 1}
