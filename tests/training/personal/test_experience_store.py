from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.training.personal.contracts import ExperienceState, LearningExperienceV1, new_id, utc_now
from src.training.personal.experience_store import ExperienceStore


def _experience(owner: str, *, text: str, experience_id: str | None = None) -> LearningExperienceV1:
    return LearningExperienceV1(
        experience_id=experience_id or new_id("exp"),
        owner_scope=owner,
        source_surface="web",
        mode="chat",
        created_at=utc_now(),
        goal="test store",
        public_context={},
        messages=({"role": "user", "content": text}, {"role": "assistant", "content": f"reply {text}"}),
        privacy_state="redacted",
    )


def test_append_is_deduplicated_and_isolated_by_owner(tmp_path) -> None:
    store = ExperienceStore(tmp_path)
    first = store.append(_experience("owner:a", text="same"))
    duplicate = store.append(_experience("owner:a", text="same"))
    other = store.append(_experience("owner:b", text="same"))
    assert first.appended is True
    assert duplicate.appended is False
    assert duplicate.duplicate_of == first.experience_id
    assert other.appended is True
    assert store.stats("owner:a")["total"] == 1
    assert store.stats("owner:b")["total"] == 1


def test_state_events_are_validated_and_rebuilt(tmp_path) -> None:
    store = ExperienceStore(tmp_path)
    exp = _experience("owner:local", text="state")
    store.append(exp)
    store.transition("owner:local", exp.experience_id, ExperienceState.CANDIDATE, reason_code="eligible")
    with pytest.raises(ValueError, match="invalid_transition"):
        store.transition("owner:local", exp.experience_id, ExperienceState.TRAINED, reason_code="skip")
    store._paths("owner:local")["index"].unlink()
    report = store.reconcile("owner:local")
    assert report == {"records": 1, "duplicates": 0, "corrupt": 0}
    assert store.stats("owner:local")["counts"] == {"candidate": 1}


def test_corrupt_tail_is_quarantined_without_copying_content(tmp_path) -> None:
    store = ExperienceStore(tmp_path)
    store.append(_experience("owner:local", text="valid"))
    paths = store._paths("owner:local")
    secret = "SECRET_CORRUPT_LINE_MUST_NOT_LEAK"
    with paths["events"].open("a", encoding="utf-8") as handle:
        handle.write(secret + "\n")
    report = store.reconcile("owner:local")
    quarantine = paths["quarantine"].read_text(encoding="utf-8")
    assert report["corrupt"] == 1
    assert secret not in quarantine
    assert json.loads(quarantine.splitlines()[-1])["raw_hash"]


def test_concurrent_writers_keep_every_unique_record(tmp_path) -> None:
    store = ExperienceStore(tmp_path)
    experiences = [_experience("owner:local", text=f"item-{index}") for index in range(40)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(store.append, experiences))
    assert all(result.appended for result in results)
    assert store.stats("owner:local")["total"] == 40
    assert store.reconcile("owner:local")["records"] == 40


def test_store_refuses_unredacted_sensitive_data(tmp_path) -> None:
    store = ExperienceStore(tmp_path)
    exp = _experience("owner:local", text="api_key=SECRET_API_LEAK_123456789")
    with pytest.raises(ValueError, match="unredacted"):
        store.append(exp)
