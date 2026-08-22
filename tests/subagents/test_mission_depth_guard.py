"""Lot 3 — Garde de profondeur (anti-récursion).

chat (depth 0) → autorisé, crée une mission depth 1 ; mission depth 1 → refusé (max=1) ;
avec MAX_DEPTH=2 → mission depth 1 peut créer (depth 2). Future-proof Lot 5.
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


@pytest.mark.asyncio
async def test_chat_depth0_allowed(tmp_path):
    ctx, orch = _ctx(tmp_path, runtime_task_id=None)  # chat
    res = await M.create_mission_handler(ctx, "x")
    assert res.success
    assert orch.get_conversation_tasks("__missions__")[0]["metadata"]["depth"] == 1
    await _drain(ctx)


@pytest.mark.asyncio
async def test_mission_depth1_blocked_by_default(tmp_path):
    ctx, orch = _ctx(tmp_path)
    # on simule qu'on tourne DANS une mission de profondeur 1
    rec = orch.start_task(conversation_id="__missions__", channel="mission",
                          message_preview="parent", metadata={"kind": "mission", "depth": 1})
    ctx.runtime_task_id = rec.task_id
    res = await M.create_mission_handler(ctx, "sous-mission")
    assert not res.success
    # LOT 2.7 : refus DIRIGÉ vers le bon chemin (delegate_and_wait)
    assert "delegate_and_wait" in res.error
    # aucune sous-mission créée (juste la parent)
    assert len(orch.get_conversation_tasks("__missions__")) == 1


@pytest.mark.asyncio
async def test_mission_refused_even_when_max_depth_2(tmp_path, monkeypatch):
    # LOT 2.7 (run NoteFlash 2026-07-02) — REFUS DUR : même avec MAX_DEPTH=2, une
    # mission ne crée JAMAIS de mission via l'outil (« poupée russe » : objectif
    # réécrit mensonger + lead tué par l'ACK). Le chemin workers = delegate_and_wait
    # (qui passe par le manager interne, non affecté — cf. test_delegate_and_wait).
    monkeypatch.setenv("LUMENA_MISSION_MAX_DEPTH", "2")
    ctx, orch = _ctx(tmp_path)
    rec = orch.start_task(conversation_id="__missions__", channel="mission",
                          message_preview="lead", metadata={"kind": "mission", "depth": 1})
    ctx.runtime_task_id = rec.task_id
    res = await M.create_mission_handler(ctx, "worker")
    assert not res.success
    assert "delegate_and_wait" in res.error and "write_mission_contract" in res.error
    assert len(orch.get_conversation_tasks("__missions__")) == 1
