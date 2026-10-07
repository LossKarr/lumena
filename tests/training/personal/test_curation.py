from __future__ import annotations

from src.training.personal.contracts import LearningExperienceV1, new_id, utc_now
from src.training.personal.curation import PersonalCurationService
from src.training.personal.experience_store import ExperienceStore
from src.training.personal.policy import PersonalLearningPolicy


def _exp(text: str, *, success=True, feedback=None):
    return LearningExperienceV1(
        experience_id=new_id("exp"), owner_scope="owner:local", source_surface="web", mode="agent",
        created_at=utc_now(), goal=text, public_context={},
        messages=({"role": "user", "content": text}, {"role": "assistant", "content": "réponse complète avec suffisamment de détails"}),
        result={"success": success}, feedback=feedback or {}, evidence=({"kind": "test", "ok": success},),
        privacy_state="redacted", license_policy="user_owned",
    )


def test_curation_separates_candidates_failures_and_duplicates(tmp_path) -> None:
    store = ExperienceStore(tmp_path)
    good = _exp("make a stable thing")
    duplicate = _exp("Make a stable thing!!!")
    failed = _exp("different failed task", success=False, feedback={"quality_flag": "incomplete"})
    for item in (good, duplicate, failed):
        store.append(item)
    service = PersonalCurationService(store, PersonalLearningPolicy(learning_enabled=True, local_capture_enabled=True))
    report = service.curate("owner:local")
    assert report.examined == 3
    assert report.candidates == 1
    assert report.rejected == 2
    assert store.stats("owner:local")["counts"] == {"candidate": 1, "rejected": 2}


def test_semantic_duplicate_is_remembered_between_curation_runs(tmp_path) -> None:
    store = ExperienceStore(tmp_path)
    store.append(_exp("same durable task"))
    service = PersonalCurationService(store, PersonalLearningPolicy(learning_enabled=True, local_capture_enabled=True))
    assert service.curate("owner:local").candidates == 1
    store.append(_exp("Same durable task!!!"))
    second = service.curate("owner:local")
    assert second.rejected == 1
    store.reconcile("owner:local")
    assert store.stats("owner:local")["counts"] == {"candidate": 1, "rejected": 1}
