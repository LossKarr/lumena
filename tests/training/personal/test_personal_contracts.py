from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from src.training.personal.contracts import (
    EXPERIENCE_TRANSITIONS,
    RUN_TRANSITIONS,
    DatasetManifestV1,
    ExperienceState,
    LearningExperienceV1,
    TrainingRunState,
    new_id,
    sha256_json,
    utc_now,
    validate_transition,
)


def _experience(**changes):
    values = {
        "experience_id": new_id("exp"),
        "owner_scope": "owner:local",
        "source_surface": "web",
        "mode": "chat",
        "created_at": utc_now(),
        "goal": "répondre précisément",
        "public_context": {},
        "messages": ({"role": "user", "content": "bonjour"}, {"role": "assistant", "content": "bonjour"}),
    }
    values.update(changes)
    return LearningExperienceV1(**values)


def test_experience_roundtrip_computes_stable_hash() -> None:
    exp = _experience()
    payload = exp.to_dict()
    restored = LearningExperienceV1.from_dict(payload)
    assert payload["content_hash"] == restored.computed_content_hash()
    assert restored.to_dict() == payload


def test_experience_contract_is_frozen() -> None:
    exp = _experience()
    with pytest.raises(FrozenInstanceError):
        exp.goal = "changed"


def test_invalid_state_transition_is_rejected() -> None:
    validate_transition(ExperienceState.RAW, ExperienceState.CANDIDATE, EXPERIENCE_TRANSITIONS)
    validate_transition(TrainingRunState.RUNNING, TrainingRunState.COMPLETED, RUN_TRANSITIONS)
    with pytest.raises(ValueError, match="invalid_transition"):
        validate_transition(ExperienceState.RAW, ExperienceState.TRAINED, EXPERIENCE_TRANSITIONS)


def test_dataset_splits_are_disjoint_and_complete() -> None:
    manifest = DatasetManifestV1(
        manifest_id="dataset_1",
        owner_scope="owner:local",
        created_at=utc_now(),
        experience_ids=("a", "b", "c"),
        train_ids=("a",),
        eval_ids=("b",),
        holdout_ids=("c",),
        builder_config={},
        distribution={},
        source_hash=sha256_json(["a", "b", "c"]),
    )
    assert len(manifest.manifest_hash()) == 64
    with pytest.raises(ValueError, match="dataset_split_overlap"):
        DatasetManifestV1(
            manifest_id="dataset_2",
            owner_scope="owner:local",
            created_at=utc_now(),
            experience_ids=("a",),
            train_ids=("a",),
            eval_ids=("a",),
            holdout_ids=(),
            builder_config={},
            distribution={},
            source_hash=sha256_json(["a"]),
        )
