from __future__ import annotations

from src.training.personal.contracts import LearningExperienceV1, new_id, utc_now
from src.training.personal.experience_store import ExperienceStore
from src.training.personal.learning_cycle import PersonalLearningCycle
from src.training.personal.policy import PersonalLearningPolicy, PersonalLearningPolicyStore


def test_learning_cycle_curates_and_judges_without_chat_context(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_DEFAULT_MODEL", "judge-model")
    policy = PersonalLearningPolicy(learning_enabled=True, local_capture_enabled=True, judge_mode="default")
    PersonalLearningPolicyStore(tmp_path / "config" / "policy.json").save(policy)
    exp = LearningExperienceV1(
        experience_id=new_id("exp"), owner_scope="owner:local", source_surface="web", mode="chat",
        created_at=utc_now(), goal="expliquer", public_context={},
        messages=({"role": "user", "content": "explique"}, {"role": "assistant", "content": "une réponse suffisamment longue et claire"}),
        result={"success": True}, privacy_state="redacted", license_policy="user_owned",
    )
    ExperienceStore(tmp_path).append(exp)
    calls = []
    report = PersonalLearningCycle(tmp_path, judge_call=lambda model, request: calls.append((model, request)) or {"score": 8, "reason_codes": ["clear"]}).run()
    assert report.curated == 1 and report.accepted == 1
    assert calls[0][0] == "judge-model"
    assert "history" not in calls[0][1] and "memory" not in calls[0][1]


def test_learning_cycle_is_inert_without_consent(tmp_path):
    assert PersonalLearningCycle(tmp_path, judge_call=lambda *_: {"score": 10}).run().curated == 0
