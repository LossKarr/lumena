"""Lot 1.5 / Lot 4 — Preuve PHARE : « chat libre pendant la mission ».

Le worker app-lifetime exécute la mission en fond ; un appel chat concurrent revient
SANS attendre. + annulation sans impact + 2 missions en fond. `think_and_act_silent`
est mocké et distingue mission (a un `task_id`) vs chat (pas de `task_id`).
"""
from __future__ import annotations

import asyncio
import types

import pytest

from src.runtime.task_orchestrator import TaskOrchestrator
from src.subagents import manager as manager_mod
from src.subagents import queue as qmod
from src.subagents import runner as runner_mod
from src.subagents import worker as worker_mod


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("LUMENA_MISSION_CONCURRENCY", raising=False)
    qmod.reset_for_tests()
    worker_mod.reset_worker_for_tests()
    manager_mod._manager = None
    # registre isolé stubé (le silent stub ignore l'outillage)
    monkeypatch.setattr(runner_mod, "create_mission_registry", lambda core: object())
    yield
    qmod.reset_for_tests()
    worker_mod.reset_worker_for_tests()
    manager_mod._manager = None


def _make_core(tmp_path, gate):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))

    async def silent(objective, *, task_id=None, allow_when_busy=False, **kw):
        if task_id:  # mission → longue : attend le gate
            await gate.wait()
            return f"mission: {objective}"
        return f"chat: {objective}"  # chat → immédiat

    core = types.SimpleNamespace(task_orchestrator=orch)
    core.think_and_act_silent = silent
    return core, orch


async def _wait_until(pred, timeout=2.0):
    import time
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.mark.asyncio
async def test_chat_is_free_while_mission_runs(tmp_path):
    gate = asyncio.Event()
    core, orch = _make_core(tmp_path, gate)
    mgr = manager_mod.get_mission_manager(core)

    mid = mgr.create_mission("longue mission")
    mgr.launch(mid, "longue mission")
    worker_mod.start_mission_worker(core)

    # la mission démarre (le worker l'a prise et le runner a marqué running)
    assert await _wait_until(lambda: orch.get_task(mid)["state"] == "running")

    # ⭐ APPEL CHAT pendant que la mission tourne → revient SANS attendre
    chat = await asyncio.wait_for(
        core.think_and_act_silent("salut", allow_when_busy=True), timeout=1.0
    )
    assert chat == "chat: salut"
    assert orch.get_task(mid)["state"] == "running"  # mission toujours en cours

    gate.set()  # on libère → la mission se termine
    assert await _wait_until(lambda: orch.get_task(mid)["state"] == "done")


@pytest.mark.asyncio
async def test_cancel_mission_chat_unaffected(tmp_path):
    gate = asyncio.Event()
    core, orch = _make_core(tmp_path, gate)
    mgr = manager_mod.get_mission_manager(core)

    mid = mgr.create_mission("m")
    mgr.launch(mid, "m")
    worker_mod.start_mission_worker(core)
    assert await _wait_until(lambda: orch.get_task(mid)["state"] == "running")

    assert await core.think_and_act_silent("x", allow_when_busy=True) == "chat: x"

    out = mgr.cancel_mission(mid)
    assert out["success"] is True
    gate.set()
    assert await _wait_until(lambda: orch.get_task(mid)["state"] == "cancelled")


@pytest.mark.asyncio
async def test_two_missions_dont_block_chat(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_CONCURRENCY", "2")
    qmod.reset_for_tests()
    gate = asyncio.Event()
    core, orch = _make_core(tmp_path, gate)
    mgr = manager_mod.get_mission_manager(core)

    m1 = mgr.create_mission("a")
    m2 = mgr.create_mission("b")
    mgr.launch(m1, "a")
    mgr.launch(m2, "b")
    worker_mod.start_mission_worker(core)
    assert await _wait_until(lambda: orch.get_task(m1)["state"] == "running"
                             and orch.get_task(m2)["state"] == "running")

    assert await core.think_and_act_silent("hi", allow_when_busy=True) == "chat: hi"

    gate.set()
    assert await _wait_until(lambda: orch.get_task(m1)["state"] == "done"
                             and orch.get_task(m2)["state"] == "done")
