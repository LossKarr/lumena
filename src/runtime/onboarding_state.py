"""Persistent, versioned state for Lumena's first-run product tour."""
from __future__ import annotations

import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.utils.paths import DATA_DIR
from src.utils.persistence import atomic_write_json, safe_read_json
from src.version import __version__

SCHEMA_VERSION = 1
TOUR_VERSION = 1
ONBOARDING_STATE_FILE = DATA_DIR / "onboarding" / "state.json"

STEP_IDS = (
    "orientation",
    "first_goal",
    "mode_choice",
    "first_message",
    "work_progress",
    "files",
    "next_destination",
    "complete",
)
STEP_SET = frozenset(STEP_IDS)
PROOF_STEPS = frozenset({"mode_choice", "first_message", "work_progress"})
FIRST_GOALS = frozenset({"chat", "agent", "file"})
TOUR_STATUSES = frozenset({"not_started", "in_progress", "skipped", "completed"})

_LOCK = threading.RLock()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def default_onboarding_state(*, user_id: str = "local:owner") -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "user_id": user_id,
        "setup_completed": False,
        "setup_completed_at": None,
        "tour_version": TOUR_VERSION,
        "tour_status": "not_started",
        "current_step": STEP_IDS[0],
        "selected_goal": None,
        "completed_steps": [],
        "skipped_steps": [],
        "dismissed_at": None,
        "last_seen_app_version": __version__,
        "updated_at": _now_iso(),
    }


def _valid_steps(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    result: list[str] = []
    for value in values:
        if isinstance(value, str) and value in STEP_SET and value not in result:
            result.append(value)
    return result


def normalize_onboarding_state(
    value: object,
    *,
    user_id: str = "local:owner",
    setup_completed: bool | None = None,
) -> dict[str, Any]:
    """Return a defensive state while ignoring unknown or corrupt values."""
    state = default_onboarding_state(user_id=user_id)
    if isinstance(value, dict):
        completed = _valid_steps(value.get("completed_steps"))
        skipped = [step for step in _valid_steps(value.get("skipped_steps")) if step not in completed]
        status = value.get("tour_status")
        current = value.get("current_step")
        state.update({
            "setup_completed": bool(value.get("setup_completed", False)),
            "setup_completed_at": value.get("setup_completed_at") if isinstance(value.get("setup_completed_at"), str) else None,
            "tour_status": status if status in TOUR_STATUSES else "not_started",
            "current_step": current if current in STEP_SET else STEP_IDS[0],
            "selected_goal": value.get("selected_goal") if value.get("selected_goal") in FIRST_GOALS else None,
            "completed_steps": completed,
            "skipped_steps": skipped,
            "dismissed_at": value.get("dismissed_at") if isinstance(value.get("dismissed_at"), str) else None,
            "last_seen_app_version": str(value.get("last_seen_app_version") or __version__),
        })
    if setup_completed is not None:
        state["setup_completed"] = bool(setup_completed)
        if setup_completed and not state["setup_completed_at"]:
            state["setup_completed_at"] = _now_iso()
    state["schema_version"] = SCHEMA_VERSION
    state["tour_version"] = TOUR_VERSION
    state["user_id"] = user_id
    state["updated_at"] = _now_iso()
    return state


class OnboardingStateStore:
    """Thread-safe state store with atomic writes and corruption recovery."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = Path(path or ONBOARDING_STATE_FILE)

    def load(self, *, user_id: str = "local:owner", setup_completed: bool | None = None) -> dict[str, Any]:
        with _LOCK:
            raw = safe_read_json(self.path, default={}, quarantine=True)
            return normalize_onboarding_state(raw, user_id=user_id, setup_completed=setup_completed)

    def save(self, state: dict[str, Any], *, user_id: str = "local:owner") -> dict[str, Any]:
        with _LOCK:
            normalized = normalize_onboarding_state(state, user_id=user_id)
            atomic_write_json(self.path, normalized)
            return deepcopy(normalized)

    def _update(self, mutator, *, user_id: str, setup_completed: bool | None = None) -> dict[str, Any]:
        with _LOCK:
            state = self.load(user_id=user_id, setup_completed=setup_completed)
            mutator(state)
            state["updated_at"] = _now_iso()
            atomic_write_json(self.path, state)
            return deepcopy(state)

    def start(self, *, user_id: str = "local:owner", setup_completed: bool | None = None) -> dict[str, Any]:
        def mutate(state: dict[str, Any]) -> None:
            state["tour_status"] = "in_progress"
            state["dismissed_at"] = None
            remaining = [step for step in STEP_IDS if step not in state["completed_steps"] and step != "complete"]
            state["current_step"] = remaining[0] if remaining else "complete"

        return self._update(mutate, user_id=user_id, setup_completed=setup_completed)

    def progress(
        self,
        step: str,
        *,
        user_id: str = "local:owner",
        proven: bool = False,
        setup_completed: bool | None = None,
    ) -> dict[str, Any]:
        if step not in STEP_SET:
            raise ValueError(f"Etape onboarding inconnue: {step}")
        if step in PROOF_STEPS and not proven:
            raise ValueError(f"L'etape {step} exige une preuve applicative")

        def mutate(state: dict[str, Any]) -> None:
            if step not in state["completed_steps"]:
                state["completed_steps"].append(step)
            state["skipped_steps"] = [item for item in state["skipped_steps"] if item != step]
            remaining = [item for item in STEP_IDS if item not in state["completed_steps"] and item != "complete"]
            state["current_step"] = remaining[0] if remaining else "complete"
            state["tour_status"] = "completed" if not remaining else "in_progress"
            if not remaining and "complete" not in state["completed_steps"]:
                state["completed_steps"].append("complete")

        return self._update(mutate, user_id=user_id, setup_completed=setup_completed)

    def select_goal(
        self,
        goal: str,
        *,
        user_id: str = "local:owner",
        setup_completed: bool | None = None,
    ) -> dict[str, Any]:
        if goal not in FIRST_GOALS:
            raise ValueError(f"Objectif onboarding inconnu: {goal}")

        def mutate(state: dict[str, Any]) -> None:
            state["selected_goal"] = goal
            if "first_goal" not in state["completed_steps"]:
                state["completed_steps"].append("first_goal")
            state["skipped_steps"] = [item for item in state["skipped_steps"] if item != "first_goal"]
            state["current_step"] = "files" if goal == "file" else "mode_choice"
            state["tour_status"] = "in_progress"
            state["dismissed_at"] = None

        return self._update(mutate, user_id=user_id, setup_completed=setup_completed)

    def skip(
        self,
        steps: Iterable[str] | None = None,
        *,
        dismiss: bool = True,
        user_id: str = "local:owner",
        setup_completed: bool | None = None,
    ) -> dict[str, Any]:
        requested = list(steps or [self.load(user_id=user_id)["current_step"]])
        if any(step not in STEP_SET for step in requested):
            raise ValueError("Une etape onboarding est inconnue")

        def mutate(state: dict[str, Any]) -> None:
            for step in requested:
                if step not in state["completed_steps"] and step not in state["skipped_steps"]:
                    state["skipped_steps"].append(step)
            if dismiss:
                state["tour_status"] = "skipped"
                state["dismissed_at"] = _now_iso()
                return
            remaining = [
                step for step in STEP_IDS
                if step not in state["completed_steps"]
                and step not in state["skipped_steps"]
                and step != "complete"
            ]
            state["current_step"] = remaining[0] if remaining else "complete"
            state["tour_status"] = "in_progress" if remaining else "completed"
            state["dismissed_at"] = None
            if not remaining and "complete" not in state["completed_steps"]:
                state["completed_steps"].append("complete")

        return self._update(mutate, user_id=user_id, setup_completed=setup_completed)

    def complete(self, *, user_id: str = "local:owner", setup_completed: bool | None = None) -> dict[str, Any]:
        def mutate(state: dict[str, Any]) -> None:
            state["tour_status"] = "completed"
            state["current_step"] = "complete"
            if "complete" not in state["completed_steps"]:
                state["completed_steps"].append("complete")
            state["dismissed_at"] = None

        return self._update(mutate, user_id=user_id, setup_completed=setup_completed)

    def reset(self, *, user_id: str = "local:owner", setup_completed: bool | None = None) -> dict[str, Any]:
        with _LOCK:
            state = default_onboarding_state(user_id=user_id)
            if setup_completed is not None:
                state["setup_completed"] = bool(setup_completed)
                state["setup_completed_at"] = _now_iso() if setup_completed else None
            atomic_write_json(self.path, state)
            return deepcopy(state)


__all__ = [
    "ONBOARDING_STATE_FILE",
    "OnboardingStateStore",
    "FIRST_GOALS",
    "PROOF_STEPS",
    "SCHEMA_VERSION",
    "STEP_IDS",
    "TOUR_VERSION",
    "default_onboarding_state",
    "normalize_onboarding_state",
]
