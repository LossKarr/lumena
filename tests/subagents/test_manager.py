"""Lot 1.3 / Lot 4 — MissionManager : création + mise en file + suivi.

Depuis le correctif Lot 4, `launch` MET EN FILE (le worker app-lifetime exécute).
Les tests utilisent `worker.drain_for_tests` pour exécuter la file de façon déterministe.
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
    yield
    qmod.reset_for_tests()
    worker_mod.reset_worker_for_tests()
    manager_mod._manager = None


def _core(tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    return types.SimpleNamespace(task_orchestrator=orch), orch


def test_create_mission_is_queued_with_metadata(tmp_path):
    core, orch = _core(tmp_path)
    mgr = MissionManager(core)
    mid = mgr.create_mission("Rédiger un rapport", deadline="ce soir")
    task = orch.get_task(mid)
    assert task["state"] == "queued"
    assert task["metadata"]["kind"] == "mission"
    assert task["metadata"]["deadline"] == "ce soir"
    assert task["conversation_id"] == "__missions__"
    # Lot 5.7.1 — l'échéance est normalisée au point central (manager) si parsable.
    assert task["metadata"]["deadline_ts"].endswith("T20:00:00")  # « ce soir » → 20:00


def test_create_mission_deadline_unparseable_keeps_raw_only(tmp_path):
    core, orch = _core(tmp_path)
    mgr = MissionManager(core)
    mid = mgr.create_mission("X", deadline="quand tu peux")
    md = orch.get_task(mid)["metadata"]
    assert md["deadline"] == "quand tu peux"   # texte brut conservé
    assert "deadline_ts" not in md             # non reconnu → aucune échéance imposée


def test_create_mission_no_deadline_no_ts(tmp_path):
    core, orch = _core(tmp_path)
    mgr = MissionManager(core)
    md = orch.get_task(mgr.create_mission("X"))["metadata"]
    assert "deadline" not in md and "deadline_ts" not in md


@pytest.mark.asyncio
async def test_create_does_not_launch(tmp_path, monkeypatch):
    core, orch = _core(tmp_path)
    called = {"n": 0}

    async def fake_run(*a, **k):
        called["n"] += 1
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    mgr = MissionManager(core)
    mgr.create_mission("x")
    await worker_mod.drain_for_tests(core)
    assert called["n"] == 0  # création seule → rien en file


@pytest.mark.asyncio
async def test_launch_enqueues_and_worker_runs(tmp_path, monkeypatch):
    core, orch = _core(tmp_path)
    seen = {}

    async def fake_run(core_arg, *, mission_id, objective, **k):
        seen["mission_id"] = mission_id
        seen["objective"] = objective
        orch.mark_done(mission_id, result_summary="ok")
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    mgr = manager_mod.get_mission_manager(core)
    mid = mgr.create_mission("faire X")
    assert mgr.launch(mid, "faire X") is True
    await worker_mod.drain_for_tests(core)
    assert seen["mission_id"] == mid
    assert seen["objective"] == "faire X"
    assert orch.get_task(mid)["state"] == "done"
    assert mgr.running_count() == 0  # _inflight vidé après exécution


@pytest.mark.asyncio
async def test_launch_is_idempotent(tmp_path, monkeypatch):
    core, orch = _core(tmp_path)
    starts = {"n": 0}

    async def fake_run(core_arg, *, mission_id, objective, **k):
        starts["n"] += 1
        orch.mark_done(mission_id)
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    mgr = manager_mod.get_mission_manager(core)
    mid = mgr.create_mission("x")
    assert mgr.launch(mid, "x") is True
    assert mgr.launch(mid, "x") is False  # déjà en file → ignoré
    await worker_mod.drain_for_tests(core)
    assert starts["n"] == 1  # enfilé/exécuté une seule fois


@pytest.mark.asyncio
async def test_create_and_launch(tmp_path, monkeypatch):
    core, orch = _core(tmp_path)

    async def fake_run(core_arg, *, mission_id, objective, **k):
        orch.mark_done(mission_id, result_summary="ok")
        return {"status": "done"}

    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    mgr = manager_mod.get_mission_manager(core)
    mid = mgr.create_and_launch("faire Y")
    await worker_mod.drain_for_tests(core)
    assert orch.get_task(mid)["state"] == "done"


def test_list_and_cancel(tmp_path):
    core, orch = _core(tmp_path)
    mgr = MissionManager(core)
    mid = mgr.create_mission("a")
    mgr.create_mission("b")
    items = mgr.list_missions()
    assert len(items) == 2
    assert all(item["runtime_active"] is False for item in items)
    out = mgr.cancel_mission(mid)
    assert out["success"] is True
    assert mgr.get_mission(mid)["state"] == "cancelled"


def test_runtime_active_est_projete_sans_modifier_le_checkpoint(tmp_path):
    core, orch = _core(tmp_path)
    mgr = MissionManager(core)
    mid = mgr.create_mission("active")
    orch.mark_checkpoint(mid, {"step": "llm"})
    mgr._inflight.add(mid)

    item = mgr.get_mission(mid)
    assert item["state"] == "checkpointed"
    assert item["runtime_active"] is True
    assert orch.get_task(mid).get("runtime_active") is None


def test_guard_when_no_orchestrator():
    core = types.SimpleNamespace(task_orchestrator=None)
    mgr = MissionManager(core)
    with pytest.raises(RuntimeError):
        mgr.create_mission("x")
