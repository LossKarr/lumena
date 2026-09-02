"""Lot 4.2 — Endpoints missions (logique appelée directement, sans HTTP)."""
from __future__ import annotations

import types

import pytest
from fastapi import HTTPException

from web.routes import missions as EP
from web.routes import deps
from src.runtime.task_orchestrator import TaskOrchestrator
from src.subagents import manager as manager_mod
from src.subagents import queue as qmod


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("LUMENA_MISSION_CONCURRENCY", raising=False)
    qmod.reset_for_tests()
    manager_mod._manager = None
    yield
    qmod.reset_for_tests()
    manager_mod._manager = None


def _bind_core(monkeypatch, tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    monkeypatch.setattr(deps, "lumena", core, raising=False)
    return core, orch


@pytest.mark.asyncio
async def test_list_get_cancel(tmp_path, monkeypatch):
    core, orch = _bind_core(monkeypatch, tmp_path)
    mgr = manager_mod.get_mission_manager(core)
    mid = mgr.create_mission("obj-A", metadata={"depth": 1})

    res = await EP.list_missions()
    assert res["success"] and res["count"] == 1
    assert res["missions"][0]["task_id"] == mid
    assert res["missions"][0]["runtime_active"] is False

    g = await EP.get_mission(mid)
    assert g["success"] and g["mission"]["state"] == "queued"
    assert g["mission"]["runtime_active"] is False

    c = await EP.cancel_mission(mid)
    assert c["success"]
    assert orch.get_task(mid)["state"] == "cancelled"


@pytest.mark.asyncio
async def test_get_unknown_is_404(tmp_path, monkeypatch):
    _bind_core(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as e:
        await EP.get_mission("nope")
    assert e.value.status_code == 404


@pytest.mark.asyncio
async def test_no_core_is_503(monkeypatch):
    monkeypatch.setattr(deps, "lumena", None, raising=False)
    with pytest.raises(HTTPException) as e:
        await EP.list_missions()
    assert e.value.status_code == 503


def test_router_registered():
    # le router est bien inclus dans l'app
    import web.server as srv
    paths = {r.path for r in srv.app.routes}
    assert "/api/missions" in paths
    assert "/api/missions/{mission_id}" in paths
