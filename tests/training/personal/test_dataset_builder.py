from __future__ import annotations

from src.training.personal.contracts import ExperienceState, LearningExperienceV1, new_id, utc_now
from src.training.personal.dataset_builder import DatasetBuilder
from src.training.personal.experience_store import ExperienceStore
from src.training.personal.policy import PersonalLearningPolicy


def test_dataset_manifest_and_splits_are_reproducible(tmp_path) -> None:
    store = ExperienceStore(tmp_path)
    for index in range(40):
        exp = LearningExperienceV1(
            experience_id=new_id("exp"), owner_scope="owner:local", source_surface="web", mode="chat",
            created_at=utc_now(), goal=f"goal {index}", public_context={"project_id": f"project-{index // 2}"},
            messages=({"role": "user", "content": f"question {index}"}, {"role": "assistant", "content": f"answer {index}"}),
            quality_state=ExperienceState.ACCEPTED, privacy_state="redacted", license_policy="user_owned",
        )
        store.append(exp)
    builder = DatasetBuilder(store)
    policy = PersonalLearningPolicy(learning_enabled=True, local_capture_enabled=True)
    first = builder.build_sft("owner:local", policy)
    second = builder.build_sft("owner:local", policy)
    assert first.manifest_hash() == second.manifest_hash()
    assert set(first.train_ids).isdisjoint(first.eval_ids)
    assert set(first.train_ids).isdisjoint(first.holdout_ids)
    assert set(first.eval_ids).isdisjoint(first.holdout_ids)
    assert set(first.experience_ids) == set(first.train_ids + first.eval_ids + first.holdout_ids)
    dataset_dir = store.owner_root("owner:local") / "datasets" / first.manifest_hash()
    assert (dataset_dir / "manifest.json").is_file()
    assert (dataset_dir / "train.jsonl").is_file()
