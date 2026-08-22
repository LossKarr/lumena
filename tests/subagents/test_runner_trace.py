"""Lot 4.1 — Le runner lie le contexte de trace à la mission (streaming SSE).

Pendant `run_mission`, le contexte de trace porte `task_id=mission_id` + `mode=agent`
→ les steps d'outils seront taggés et streamables par carte. Après le run, le
contexte est restauré (pop).
"""
from __future__ import annotations

import types

import pytest

from src.runtime.task_orchestrator import TaskOrchestrator
from src.subagents import runner as runner_mod
from src.telemetry import trace_bus as tb


@pytest.fixture
def _stub_registry(monkeypatch):
    monkeypatch.setattr(runner_mod, "create_mission_registry", lambda core: object())


def _new_mission(orch):
    rec = orch.start_task(conversation_id="__missions__", channel="mission",
                          message_preview="x", metadata={"kind": "mission"})
    return rec.task_id


@pytest.mark.asyncio
async def test_trace_context_tagged_during_run_and_restored(tmp_path, _stub_registry):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    seen = {}

    async def silent(objective, **kw):
        seen["task_id"] = tb._task_id_var.get()
        seen["mode"] = tb._mode_var.get()
        seen["channel"] = tb._channel_var.get()
        return "ok"

    core = types.SimpleNamespace(task_orchestrator=orch)
    core.think_and_act_silent = silent
    mid = _new_mission(orch)

    before = tb._task_id_var.get()
    out = await runner_mod.run_mission(core, mission_id=mid, objective="x")

    assert out["status"] == "done"
    # pendant la mission : taggé avec son id, mode agent, canal mission
    assert seen["task_id"] == mid
    assert seen["mode"] == "agent"
    assert seen["channel"] == "mission"
    # après : contexte restauré (pop) — plus le task_id de la mission
    assert tb._task_id_var.get() == before


@pytest.mark.asyncio
async def test_trace_popped_even_on_exception(tmp_path, _stub_registry):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))

    async def silent(objective, **kw):
        raise RuntimeError("boom")

    core = types.SimpleNamespace(task_orchestrator=orch)
    core.think_and_act_silent = silent
    mid = _new_mission(orch)

    before = tb._task_id_var.get()
    out = await runner_mod.run_mission(core, mission_id=mid, objective="x")
    assert out["status"] == "failed"
    assert tb._task_id_var.get() == before  # restauré malgré l'exception
