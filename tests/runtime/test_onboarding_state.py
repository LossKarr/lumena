from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.runtime.onboarding_state import OnboardingStateStore, normalize_onboarding_state


def test_absent_state_is_safe_and_versioned(tmp_path) -> None:
    state = OnboardingStateStore(tmp_path / "onboarding.json").load(setup_completed=True)
    assert state["schema_version"] == 1
    assert state["setup_completed"] is True
    assert state["tour_status"] == "not_started"
    assert state["current_step"] == "orientation"


def test_corrupt_state_is_quarantined_and_recreated(tmp_path) -> None:
    path = tmp_path / "onboarding.json"
    path.write_text("{not-json", encoding="utf-8")
    state = OnboardingStateStore(path).start(setup_completed=True)
    assert state["tour_status"] == "in_progress"
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1
    assert list((tmp_path / ".quarantine").glob("*"))


def test_unknown_values_are_ignored() -> None:
    state = normalize_onboarding_state({
        "tour_status": "invented",
        "current_step": "dangerous",
        "completed_steps": ["orientation", "unknown", "orientation"],
        "secret": "must disappear",
    })
    assert state["tour_status"] == "not_started"
    assert state["current_step"] == "orientation"
    assert state["completed_steps"] == ["orientation"]
    assert "secret" not in state


def test_proof_steps_cannot_be_marked_without_application_evidence(tmp_path) -> None:
    store = OnboardingStateStore(tmp_path / "state.json")
    with pytest.raises(ValueError, match="preuve applicative"):
        store.progress("first_message")
    state = store.progress("first_message", proven=True)
    assert "first_message" in state["completed_steps"]


def test_reset_never_resets_setup(tmp_path) -> None:
    store = OnboardingStateStore(tmp_path / "state.json")
    store.complete(setup_completed=True)
    state = store.reset(setup_completed=True)
    assert state["setup_completed"] is True
    assert state["tour_status"] == "not_started"


def test_concurrent_atomic_updates_leave_valid_json(tmp_path) -> None:
    store = OnboardingStateStore(tmp_path / "state.json")
    store.start(setup_completed=True)
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: store.progress("orientation", setup_completed=True), range(40)))
    state = json.loads(store.path.read_text(encoding="utf-8"))
    assert state["completed_steps"].count("orientation") == 1


def test_optional_step_skip_preserves_tour_resume(tmp_path) -> None:
    store = OnboardingStateStore(tmp_path / "onboarding.json")
    store.start(setup_completed=True)
    store.progress("orientation", setup_completed=True)
    store.select_goal("agent", setup_completed=True)
    store.progress("mode_choice", proven=True, setup_completed=True)
    store.progress("first_message", proven=True, setup_completed=True)

    state = store.skip(["work_progress"], dismiss=False, setup_completed=True)

    assert state["tour_status"] == "in_progress"
    assert state["current_step"] == "files"
    assert state["skipped_steps"] == ["work_progress"]
    assert state["dismissed_at"] is None
    reloaded = store.load(setup_completed=True)
    assert reloaded["tour_status"] == "in_progress"
    assert reloaded["current_step"] == "files"


def test_full_skip_keeps_backward_compatible_dismissal(tmp_path) -> None:
    store = OnboardingStateStore(tmp_path / "onboarding.json")
    store.start(setup_completed=True)
    state = store.skip(["orientation"], setup_completed=True)
    assert state["tour_status"] == "skipped"
    assert state["dismissed_at"]


@pytest.mark.parametrize(
    ("goal", "next_step"),
    [("chat", "mode_choice"), ("agent", "mode_choice"), ("file", "files")],
)
def test_first_goal_is_persisted_and_selects_the_next_real_step(tmp_path, goal, next_step) -> None:
    store = OnboardingStateStore(tmp_path / "onboarding.json")
    store.start(setup_completed=True)
    state = store.select_goal(goal, setup_completed=True)
    assert state["selected_goal"] == goal
    assert state["current_step"] == next_step
    assert "first_goal" in state["completed_steps"]
    assert store.load(setup_completed=True)["selected_goal"] == goal


def test_unknown_first_goal_is_rejected(tmp_path) -> None:
    store = OnboardingStateStore(tmp_path / "onboarding.json")
    with pytest.raises(ValueError, match="Objectif onboarding inconnu"):
        store.select_goal("do-everything")
