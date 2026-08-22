"""Reprise sûre au démarrage, sans rejeu d'effet externe ambigu."""
from __future__ import annotations

import pytest

from src.runtime.task_orchestrator import TaskOrchestrator
from src.subagents.resume_policy import reconcile_on_boot


def _orch(tmp_path):
    return TaskOrchestrator(persistence_path=str(tmp_path / "resume_state.json"))


def _mk(orch, state, conv="__missions__"):
    rec = orch.start_task(conversation_id=conv, channel="mission", message_preview="x",
                          metadata={"kind": "mission"})
    if state != "queued":
        orch.update_state(rec.task_id, state)
    return rec.task_id


def test_queued_stays_and_is_requeued(tmp_path):
    orch = _orch(tmp_path)
    tid = _mk(orch, "queued")
    out = reconcile_on_boot(orch)
    assert tid in out["requeued"]
    assert orch.get_task(tid)["state"] == "queued"  # inchangé
    assert tid not in out["needs_review"]


@pytest.mark.parametrize("state", ["running", "waiting_io", "checkpointed"])
def test_top_lead_in_flight_is_queued_for_safe_recovery(tmp_path, state):
    orch = _orch(tmp_path)
    tid = _mk(orch, state)
    out = reconcile_on_boot(orch)
    assert tid in out["requeued"]
    task = orch.get_task(tid)
    assert task["metadata"]["recovery_required"] is True
    assert task["metadata"]["recovery_attempts"] == 1
    assert task["metadata"]["recovery_original_state"] == state
    assert task["metadata"].get("needs_review") is False
    assert task["state"] == "queued"


def test_interrupted_worker_is_never_replayed_directly(tmp_path):
    orch = _orch(tmp_path)
    rec = orch.start_task(
        conversation_id="__missions__", channel="mission", message_preview="worker",
        metadata={"kind": "mission", "depth": 2, "parent_id": "task_parent"},
    )
    orch.mark_checkpoint(rec.task_id, {"phase": "tool", "action": "mail_send"})
    out = reconcile_on_boot(orch)
    task = orch.get_task(rec.task_id)
    assert rec.task_id not in out["requeued"]
    assert rec.task_id in out["needs_review"]
    assert task["state"] == "checkpointed"
    assert task["metadata"]["needs_review"] is True


@pytest.mark.parametrize("state", ["done", "failed", "cancelled"])
def test_terminal_untouched(tmp_path, state):
    orch = _orch(tmp_path)
    tid = _mk(orch, state)
    out = reconcile_on_boot(orch)
    assert tid not in out["needs_review"]
    assert tid not in out["requeued"]
    assert orch.get_task(tid)["metadata"].get("needs_review") is None


def test_recovery_attempt_is_bounded(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_AUTO_RECOVERY_MAX", "1")
    orch = _orch(tmp_path)
    tid = _mk(orch, "running")
    first = reconcile_on_boot(orch)
    orch.mark_checkpoint(tid, {"phase": "interrupted_again"})
    second = reconcile_on_boot(orch)
    assert tid in first["requeued"]
    assert tid in second["needs_review"]
    assert orch.get_task(tid)["metadata"]["needs_review"] is True


def test_recovery_preserves_original_deadline_without_immediate_expiry(tmp_path):
    orch = _orch(tmp_path)
    rec = orch.start_task(
        conversation_id="__missions__", channel="mission", message_preview="x",
        metadata={"kind": "mission", "deadline_ts": "2026-01-01T00:00:00"},
    )
    orch.mark_running(rec.task_id)
    reconcile_on_boot(orch)
    meta = orch.get_task(rec.task_id)["metadata"]
    assert meta["recovery_original_deadline_ts"] == "2026-01-01T00:00:00"
    assert meta["deadline_ts"] is None


def test_helpers_additifs(tmp_path):
    orch = _orch(tmp_path)
    tid = _mk(orch, "running")
    # list_all_tasks (existant) renvoie des dicts
    assert any(r.get("task_id") == tid for r in orch.list_all_tasks(limit=1000))
    # set_task_metadata (ajout 0.d) : additif, ne touche pas l'état
    orch.set_task_metadata(tid, foo="bar")
    assert orch.get_task(tid)["metadata"]["foo"] == "bar"
    assert orch.get_task(tid)["state"] == "running"


def test_reconcile_non_fatal_on_bad_orchestrator():
    class _Bad:
        def list_all_tasks(self):
            raise RuntimeError("boom")
    out = reconcile_on_boot(_Bad())
    assert out == {"requeued": [], "needs_review": []}
