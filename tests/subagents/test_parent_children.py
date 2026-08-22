"""Lot 5.0 — Lien parent↔enfant (collaboration).

Prouve : `TaskOrchestrator.get_children(parent_id)` (lecture seule, tri création) ;
`create_mission` au niveau chat (depth 0) ne pose PAS de parent ; depuis une mission
(depth 1, MAX_DEPTH=2) pose `parent_id = runtime_task_id`. Base du Lot 5 (le lead
retrouve ses workers pour les suivre/fusionner).
"""
from __future__ import annotations

import types

import pytest

from src.runtime.task_orchestrator import TaskOrchestrator
from src.reasoning.handlers import missions as M
from src.subagents import manager as manager_mod
from src.subagents import queue as qmod
from src.subagents import worker as worker_mod


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("LUMENA_MISSION_CONCURRENCY", raising=False)
    monkeypatch.delenv("LUMENA_MISSION_MAX_DEPTH", raising=False)
    qmod.reset_for_tests()
    worker_mod.reset_worker_for_tests()
    manager_mod._manager = None

    async def fake_run(core_arg, *, mission_id, objective, **k):
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    yield
    qmod.reset_for_tests()
    worker_mod.reset_worker_for_tests()
    manager_mod._manager = None


def _ctx(tmp_path, runtime_task_id=None):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    return types.SimpleNamespace(lumena=core, runtime_task_id=runtime_task_id), orch


async def _drain(ctx):
    await worker_mod.drain_for_tests(ctx.lumena)


# ── get_children (orchestrateur) ─────────────────────────────────────────────────

def test_get_children_filters_and_sorts(tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    lead = orch.start_task(conversation_id="__missions__", channel="mission",
                           message_preview="lead", metadata={"kind": "mission", "depth": 1})
    a = orch.start_task(conversation_id="__missions__", channel="mission",
                        message_preview="A", metadata={"kind": "mission", "depth": 2,
                                                       "parent_id": lead.task_id})
    b = orch.start_task(conversation_id="__missions__", channel="mission",
                        message_preview="B", metadata={"kind": "mission", "depth": 2,
                                                       "parent_id": lead.task_id})
    # un intrus sans le bon parent
    orch.start_task(conversation_id="__missions__", channel="mission",
                    message_preview="autre", metadata={"kind": "mission", "depth": 2,
                                                       "parent_id": "qqn-dautre"})
    kids = orch.get_children(lead.task_id)
    assert [k["task_id"] for k in kids] == [a.task_id, b.task_id]  # tri création ASC


def test_get_children_empty_cases(tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    assert orch.get_children("") == []
    assert orch.get_children("inconnu") == []


# ── parent_id posé par create_mission ────────────────────────────────────────────

@pytest.mark.asyncio
async def test_chat_depth0_has_no_parent(tmp_path):
    ctx, orch = _ctx(tmp_path, runtime_task_id=None)  # chat
    res = await M.create_mission_handler(ctx, "tâche")
    assert res.success
    m = orch.get_conversation_tasks("__missions__")[0]
    assert m["metadata"]["depth"] == 1
    assert "parent_id" not in m["metadata"]
    await _drain(ctx)


@pytest.mark.asyncio
async def test_mission_create_mission_refused_children_via_delegate(tmp_path, monkeypatch):
    # LOT 2.7 : create_mission DANS une mission = refus dur (poupée russe, run
    # NoteFlash). Le lien parent↔enfant vit désormais UNIQUEMENT via
    # delegate_and_wait (couvert par test_delegate_and_wait::test_delegate_fuses_two_workers).
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    lead = orch.start_task(conversation_id="__missions__", channel="mission",
                           message_preview="lead", metadata={"kind": "mission", "depth": 1})
    ctx.runtime_task_id = lead.task_id  # on tourne DANS le lead
    res = await M.create_mission_handler(ctx, "sous-tâche")
    assert not res.success and "delegate_and_wait" in res.error
    assert orch.get_children(lead.task_id) == []
