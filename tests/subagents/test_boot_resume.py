"""Lot 2.3 / Lot 4 — Reprise au boot + singleton.

`relaunch_queued` remet en file les missions normales et les récupérations préparées ;
jamais les `needs_review` ni les terminaux.
"""
from __future__ import annotations

import types

import pytest

from src.runtime.task_orchestrator import TaskOrchestrator
from src.subagents import manager as manager_mod
from src.subagents import queue as qmod
from src.subagents import worker as worker_mod
from src.subagents.manager import MissionManager


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("LUMENA_MISSION_CONCURRENCY", raising=False)
    qmod.reset_for_tests()
    worker_mod.reset_worker_for_tests()
    manager_mod._manager = None

    async def fake_run(core_arg, *, mission_id, objective, **k):
        core_arg.task_orchestrator.mark_done(mission_id, result_summary="ok")
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    yield
    qmod.reset_for_tests()
    worker_mod.reset_worker_for_tests()
    manager_mod._manager = None


@pytest.mark.asyncio
async def test_relaunch_only_queued(tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    mgr = manager_mod.get_mission_manager(core)

    q1 = mgr.create_mission("obj-queued")                 # queued → à relancer
    nr = mgr.create_mission("obj-needs-review")           # ex-running → needs_review
    orch.update_state(nr, "running")
    orch.set_task_metadata(nr, needs_review=True)
    done = mgr.create_mission("obj-done")                 # terminal
    orch.mark_done(done)

    relaunched = manager_mod.relaunch_queued(mgr)

    assert q1 in relaunched
    assert nr not in relaunched        # interrompue → JAMAIS rejouée
    assert done not in relaunched      # terminal → ignorée

    await worker_mod.drain_for_tests(core)
    assert orch.get_task(q1)["state"] == "done"


@pytest.mark.asyncio
async def test_relaunch_interrupted_lead_uses_hard_local_recovery(tmp_path, monkeypatch):
    from src.subagents.resume_policy import RECOVERY_ALLOWED_TOOLS, reconcile_on_boot

    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    mgr = manager_mod.get_mission_manager(core)
    lead = mgr.create_mission(
        "Créer puis envoyer un rapport",
        metadata={"mission_workspace": "missions/task_recovery"},
    )
    child = orch.start_task(
        conversation_id="__missions__", channel="mission", message_preview="worker",
        metadata={
            "kind": "mission", "parent_id": lead, "depth": 2,
            "artifacts": ["missions/task_recovery/report.md"],
        },
    )
    orch.mark_done(child.task_id, result_summary="rapport écrit")
    orch.mark_checkpoint(lead, {
        "phase": "iteration", "ledger": {"recent": [
            {"action": "mail_send", "success": True, "target": "client@example.test"},
        ]},
    })

    reconcile_on_boot(orch)
    relaunched = manager_mod.relaunch_queued(mgr)
    assert lead in relaunched
    item = await mgr.queue().get()
    mission_id, objective, timeout, allowed_tools = item
    assert mission_id == lead
    assert "RECUPERATION SURE APRES INTERRUPTION" in objective
    assert "ne répète AUCUNE action externe" in objective
    assert "mail_send" in objective  # preuve persistée visible, pas outil autorisé
    assert "rapport écrit" in objective
    assert set(allowed_tools) == set(RECOVERY_ALLOWED_TOOLS)
    assert "mail_send" not in allowed_tools
    assert "stripe_create_payment_link" not in allowed_tools
    assert "close_app" not in allowed_tools
    assert "run_command" in allowed_tools
    assert "browser_verify_local_project" in allowed_tools
    assert 120 <= timeout <= 1800


@pytest.mark.asyncio
async def test_relaunch_skips_missing_objective(tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    rec = orch.start_task(conversation_id="__missions__", channel="mission",
                          message_preview="", metadata={"kind": "mission"})
    mgr = manager_mod.get_mission_manager(core)
    relaunched = manager_mod.relaunch_queued(mgr)
    assert rec.task_id not in relaunched  # rien à relancer sans objectif


def test_get_mission_manager_singleton():
    core = types.SimpleNamespace(task_orchestrator=TaskOrchestrator())
    m1 = manager_mod.get_mission_manager(core)
    m2 = manager_mod.get_mission_manager(core)
    assert m1 is m2


def test_get_mission_manager_rebinds_on_new_core():
    core1 = types.SimpleNamespace(task_orchestrator=TaskOrchestrator())
    core2 = types.SimpleNamespace(task_orchestrator=TaskOrchestrator())
    m1 = manager_mod.get_mission_manager(core1)
    m2 = manager_mod.get_mission_manager(core2)
    assert m1 is not m2 and m2.core is core2
