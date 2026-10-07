from __future__ import annotations

import json

import pytest

from src.training.personal.preference_pipeline import PreferenceDatasetBuilder, make_preference_pair


def _pair(owner="owner:local"):
    return make_preference_pair(
        owner_scope=owner,
        prompt=[{"role": "user", "content": "solve"}],
        chosen=[{"role": "assistant", "content": "correct"}],
        rejected=[{"role": "assistant", "content": "wrong"}],
        evidence=[{"test": "passed"}],
        tool_schemas=[{"name": "read_file"}],
    )


def test_dpo_dataset_consumes_rejected_and_preserves_tools(tmp_path) -> None:
    manifest = PreferenceDatasetBuilder(tmp_path).build("owner:local", [_pair()])
    row = json.loads((tmp_path / "datasets" / f"dpo_{manifest['dataset_hash']}" / "train.jsonl").read_text(encoding="utf-8"))
    assert row["chosen"][0]["content"] == "correct"
    assert row["rejected"][0]["content"] == "wrong"
    assert row["tools"] == [{"name": "read_file"}]


def test_preference_requires_different_answers_and_evidence() -> None:
    with pytest.raises(ValueError, match="identical"):
        make_preference_pair(
            owner_scope="owner:local", prompt=[{"role": "user", "content": "x"}],
            chosen=[{"role": "assistant", "content": "same"}], rejected=[{"role": "assistant", "content": "same"}],
            evidence=[{"ok": True}],
        )
    with pytest.raises(ValueError, match="evidence"):
        make_preference_pair(
            owner_scope="owner:local", prompt=[{"role": "user", "content": "x"}],
            chosen=[{"role": "assistant", "content": "a"}], rejected=[{"role": "assistant", "content": "b"}],
            evidence=[],
        )
