"""Lot 4 (correctif) — Worker de missions app-lifetime.

Prouve que l'exécution est DÉCOUPLÉE de la requête : le worker consomme la file et
exécute la mission jusqu'au bout (le bug runtime était une mission lancée depuis la
requête chat qui mourait à la fin du `StreamingResponse`). + survie au crash + idempotence.
"""
from __future__ import annotations

import asyncio
import time
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
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if pred():
            return True
        await asyncio.sleep(0.01)
    return False


@pytest.mark.asyncio
async def test_worker_runs_queued_mission_to_completion(tmp_path, monkeypatch):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)

    async def fake_run(core_arg, *, mission_id, objective, **k):
        orch.mark_running(mission_id)
        await asyncio.sleep(0.02)  # simule un vrai travail
        orch.mark_done(mission_id, result_summary="ok")
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    mgr = manager_mod.get_mission_manager(core)
    mid = mgr.create_mission("x")
    mgr.launch(mid, "x")  # met en file — n'exécute PAS ici
    worker_mod.start_mission_worker(core)  # le worker (app-lifetime) exécute

    assert await _wait_until(lambda: orch.get_task(mid)["state"] == "done")
    assert await _wait_until(lambda: mgr.running_count() == 0)  # _inflight vidé


@pytest.mark.asyncio
async def test_worker_survives_a_failing_mission(tmp_path, monkeypatch):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    calls = []

    async def fake_run(core_arg, *, mission_id, objective, **k):
        calls.append(mission_id)
        if objective == "boom":
            raise RuntimeError("crash")
        orch.mark_done(mission_id)
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    mgr = manager_mod.get_mission_manager(core)
    bad = mgr.create_mission("boom")
    mgr.launch(bad, "boom")
    good = mgr.create_mission("ok")
    mgr.launch(good, "ok")
    worker_mod.start_mission_worker(core)

    # la mission qui crash ne tue PAS le worker → la suivante s'exécute
    assert await _wait_until(lambda: orch.get_task(good)["state"] == "done")
    assert bad in calls and good in calls


@pytest.mark.asyncio
async def test_start_mission_worker_is_idempotent(tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    t1 = worker_mod.start_mission_worker(core)
    t2 = worker_mod.start_mission_worker(core)
    assert t1 is t2  # une seule boucle worker


@pytest.mark.asyncio
async def test_run_item_survives_systemexit(tmp_path, monkeypatch):
    # BUG #1 : un cancel coopératif (raise SystemExit) ne doit JAMAIS tuer le worker/l'app
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    mgr = manager_mod.get_mission_manager(core)
    mid = mgr.create_mission("x", metadata={"depth": 1})
    mgr._inflight.add(mid)

    async def boom(core_arg, *, mission_id, objective, **k):
        raise SystemExit("task_orchestrator_cancel")

    monkeypatch.setattr(worker_mod, "run_mission", boom)
    await worker_mod._run_item(core, mgr, (mid, "x", 10.0, None))  # ne doit PAS lever
    assert mgr.running_count() == 0  # _inflight nettoyé
