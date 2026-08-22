"""Lot 2.2 / Lot 4 — Le worker borne l'exécution par le créneau (file 2.1).

Avec concurrence 1 : N missions enfilées → **une seule s'exécute** à la fois (les
autres restent `queued`), puis toutes `done`. Le worker app-lifetime est démarré
dans le test puis arrêté.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from src.runtime.task_orchestrator import TaskOrchestrator
from src.subagents import manager as manager_mod
from src.subagents import queue as qmod
from src.subagents import worker as worker_mod


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("LUMENA_MISSION_CONCURRENCY", raising=False)
    qmod.reset_for_tests()
    worker_mod.reset_worker_for_tests()
    manager_mod._manager = None
    yield
    qmod.reset_for_tests()
    worker_mod.reset_worker_for_tests()
    manager_mod._manager = None


async def _wait_until(pred, timeout=2.0):
    import time
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.mark.asyncio
async def test_concurrency_caps_parallel_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_CONCURRENCY", "1")
    qmod.reset_for_tests()
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    peak = {"cur": 0, "max": 0}
    gate = asyncio.Event()

    async def fake_run(core_arg, *, mission_id, objective, **k):
        orch.mark_running(mission_id)
        peak["cur"] += 1
        peak["max"] = max(peak["max"], peak["cur"])
        await gate.wait()
        peak["cur"] -= 1
        orch.mark_done(mission_id)
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    mgr = manager_mod.get_mission_manager(core)
    ids = [mgr.create_mission(f"m{i}") for i in range(3)]
    for mid in ids:
        mgr.launch(mid, "obj")
    worker_mod.start_mission_worker(core)

    assert await _wait_until(lambda: peak["cur"] == 1)  # une mission tourne
    await asyncio.sleep(0.05)
    assert peak["max"] == 1  # concurrence 1 → jamais 2 en parallèle
    states = [orch.get_task(i)["state"] for i in ids]
    assert states.count("queued") == 2  # les autres attendent

    gate.set()
    assert await _wait_until(lambda: all(orch.get_task(i)["state"] == "done" for i in ids))


@pytest.mark.asyncio
async def test_concurrency_two_allows_two(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_CONCURRENCY", "2")
    qmod.reset_for_tests()
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    peak = {"cur": 0, "max": 0}
    gate = asyncio.Event()

    async def fake_run(core_arg, *, mission_id, objective, **k):
        orch.mark_running(mission_id)
        peak["cur"] += 1
        peak["max"] = max(peak["max"], peak["cur"])
        await gate.wait()
        peak["cur"] -= 1
        orch.mark_done(mission_id)
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    mgr = manager_mod.get_mission_manager(core)
    ids = [mgr.create_mission(f"m{i}") for i in range(3)]
    for mid in ids:
        mgr.launch(mid, "obj")
    worker_mod.start_mission_worker(core)

    assert await _wait_until(lambda: peak["cur"] == 2)  # deux en parallèle
    await asyncio.sleep(0.05)
    assert peak["max"] == 2

    gate.set()
    assert await _wait_until(lambda: all(orch.get_task(i)["state"] == "done" for i in ids))
