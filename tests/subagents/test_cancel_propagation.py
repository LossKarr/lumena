"""Lot 5.3 — Annulation propagée : annuler un lead annule ses workers/sous-missions.

Avant : `cancel_task(lead)` ne touchait que le lead → les workers tournaient orphelins.
Maintenant : propagation transitive via `metadata.parent_id`, additive et bornée
(jamais de tâche terminale touchée, scopée par parent_id → zéro impact hors missions).
"""
from __future__ import annotations

from src.runtime.task_orchestrator import TaskOrchestrator


def _orch(tmp_path):
    return TaskOrchestrator(persistence_path=str(tmp_path / "state.json"))


def _task(orch, tid, parent=None, state="running"):
    meta = {"kind": "mission"}
    if parent:
        meta["parent_id"] = parent
    orch.start_task(conversation_id="__missions__", channel="web",
                    message_preview=tid, metadata=meta, task_id=tid)
    orch.update_state(tid, state)
    return tid


def _state(orch, tid):
    return (orch.get_task(tid) or {}).get("state")


def test_cancel_lead_cancels_workers(tmp_path):
    orch = _orch(tmp_path)
    _task(orch, "lead")
    _task(orch, "w1", parent="lead")
    _task(orch, "w2", parent="lead")
    out = orch.cancel_task("lead")
    assert out["success"] is True
    assert set(out["cancelled"]) == {"lead", "w1", "w2"}
    assert _state(orch, "lead") == "cancelled"
    assert _state(orch, "w1") == "cancelled"
    assert _state(orch, "w2") == "cancelled"
    assert orch.is_cancel_requested("w1") is True
    assert orch.is_cancel_requested("w2") is True


def test_cancel_is_transitive_grandchildren(tmp_path):
    orch = _orch(tmp_path)
    _task(orch, "lead")
    _task(orch, "w1", parent="lead")
    _task(orch, "gc", parent="w1")  # petit-enfant
    out = orch.cancel_task("lead")
    assert set(out["cancelled"]) == {"lead", "w1", "gc"}
    assert _state(orch, "gc") == "cancelled"


def test_terminal_children_not_touched(tmp_path):
    orch = _orch(tmp_path)
    _task(orch, "lead")
    _task(orch, "w_done", parent="lead", state="done")
    _task(orch, "w_run", parent="lead", state="running")
    out = orch.cancel_task("lead")
    # le worker DONE reste done (état non corrompu), mais reçoit quand même le flag…
    assert _state(orch, "w_done") == "done"
    assert _state(orch, "w_run") == "cancelled"
    assert "w_done" in out["cancelled"] and "w_run" in out["cancelled"]


def test_no_children_unchanged(tmp_path):
    orch = _orch(tmp_path)
    _task(orch, "solo")
    out = orch.cancel_task("solo")
    assert out["cancelled"] == ["solo"]
    assert _state(orch, "solo") == "cancelled"


def test_propagate_false_only_target(tmp_path):
    orch = _orch(tmp_path)
    _task(orch, "lead")
    _task(orch, "w1", parent="lead")
    out = orch.cancel_task("lead", propagate=False)
    assert out["cancelled"] == ["lead"]
    assert _state(orch, "lead") == "cancelled"
    assert _state(orch, "w1") == "running"          # worker épargné (échappatoire)
    assert orch.is_cancel_requested("w1") is False


def test_unrelated_mission_untouched(tmp_path):
    orch = _orch(tmp_path)
    _task(orch, "lead")
    _task(orch, "w1", parent="lead")
    _task(orch, "other")                              # mission indépendante
    orch.cancel_task("lead")
    assert _state(orch, "other") == "running"


def test_cancel_unknown_task(tmp_path):
    orch = _orch(tmp_path)
    out = orch.cancel_task("nope")
    assert out["success"] is False
    assert out["message"] == "task_not_found"


def test_cancel_survives_reload(tmp_path):
    # La propagation est persistée → survit au reboot.
    orch = _orch(tmp_path)
    _task(orch, "lead")
    _task(orch, "w1", parent="lead")
    orch.cancel_task("lead")
    reloaded = TaskOrchestrator(persistence_path=str(tmp_path / "state.json"))
    assert reloaded.is_cancel_requested("w1") is True
    assert (reloaded.get_task("w1") or {}).get("state") == "cancelled"
