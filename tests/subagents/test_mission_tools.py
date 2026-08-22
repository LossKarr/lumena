"""Lot 3 — Outils de mission (handlers + enregistrement + capabilities/gardes).

Vérifie : enregistrement + catégorie always-include ; capabilities (create/cancel
MUTATION pour passer le ledger/FINAL, lectures READONLY) ; handlers (create_and_launch,
list, status, result, cancel) ; échec propre sans core.
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


# ── enregistrement + gardes ─────────────────────────────────────────────────────

def test_tools_registered_and_always_included():
    from src.reasoning.tool_registry import ToolRegistry
    reg = ToolRegistry(lumena=None)
    for t in ["create_mission", "list_missions", "mission_status", "mission_result", "cancel_mission"]:
        assert t in reg.tools
        assert reg._tool_modules[t] == "missions"
    assert "missions" in reg._ALWAYS_INCLUDE_CATEGORIES


def test_capabilities_pass_final_guard():
    from src.reasoning.plan_evidence import get_tool_capabilities, ProofCapability
    # create/cancel = MUTATION → comptent au ledger → FINAL autorisé après
    assert ProofCapability.GENERIC_MUTATION in get_tool_capabilities("create_mission", "missions", "missions")
    assert ProofCapability.GENERIC_MUTATION in get_tool_capabilities("cancel_mission", "missions", "missions")
    # lectures = READONLY
    assert get_tool_capabilities("list_missions", "missions", "missions") == frozenset({ProofCapability.GENERIC_READONLY})
    assert get_tool_capabilities("mission_status", "missions", "missions") == frozenset({ProofCapability.GENERIC_READONLY})


def test_anti_fallback_still_green():
    # le test global doit rester vert avec la nouvelle catégorie
    from tests.reasoning.test_plan_evidence_phase2 import test_no_runtime_category_falls_back_silently as t
    t()


# ── handlers ────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_mission_creates_and_launches(tmp_path, monkeypatch):
    async def fake_run(core_arg, *, mission_id, objective, **k):
        core_arg.task_orchestrator.mark_done(mission_id, result_summary="fini")
        return {"status": "done"}
    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    ctx, orch = _ctx(tmp_path)
    res = await M.create_mission_handler(ctx, "faire X")
    assert res.success
    items = orch.get_conversation_tasks("__missions__")
    assert len(items) == 1
    assert items[0]["metadata"]["depth"] == 1  # chat → mission depth 1
    await _drain(ctx)


@pytest.mark.asyncio
async def test_create_mission_no_core_fails():
    ctx = types.SimpleNamespace(lumena=None, runtime_task_id=None)
    res = await M.create_mission_handler(ctx, "x")
    assert not res.success


@pytest.mark.asyncio
async def test_create_mission_observation_steers_to_final_not_poll(tmp_path, monkeypatch):
    # C (sous-agents) : l'observation doit pousser vers FINAL, PAS vers le polling
    async def fake_run(core_arg, *, mission_id, objective, **k):
        return {"status": "done"}
    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    ctx, _ = _ctx(tmp_path)
    res = await M.create_mission_handler(ctx, "faire X")
    assert res.success
    low = res.output.lower()
    assert "final" in low  # invite à terminer le tour
    assert "ne lance pas" in low and "mission_status" in low  # interdit le poll immédiat
    await _drain(ctx)


@pytest.mark.asyncio
async def test_list_status_result_cancel(tmp_path, monkeypatch):
    async def fake_run(core_arg, *, mission_id, objective, **k):
        return {"status": "done"}  # ne marque pas done → reste running
    monkeypatch.setattr(worker_mod, "run_mission", fake_run)
    ctx, orch = _ctx(tmp_path)
    mgr = manager_mod.get_mission_manager(ctx.lumena)
    mid = mgr.create_mission("obj", metadata={"depth": 1})

    lst = await M.list_missions_handler(ctx)
    assert lst.success and mid in lst.output

    st = await M.mission_status_handler(ctx, mid)
    assert st.success and "queued" in st.output

    # résultat : pas encore terminée
    rs = await M.mission_result_handler(ctx, mid)
    assert rs.success and "pas encore" in rs.output.lower()

    # annulation
    cancel = await M.cancel_mission_handler(ctx, mid)
    assert cancel.success
    assert orch.get_task(mid)["state"] == "cancelled"


@pytest.mark.asyncio
async def test_status_result_checkpointed_steer_no_relaunch(tmp_path):
    # Bug doublon : checkpointed = EN COURS → le chat ne doit PAS relancer une finalisation
    ctx, orch = _ctx(tmp_path)
    mgr = manager_mod.get_mission_manager(ctx.lumena)
    mid = mgr.create_mission("obj", metadata={"depth": 1})
    orch.mark_checkpoint(mid, {"step": 3})
    assert orch.get_task(mid)["state"] == "checkpointed"

    st = await M.mission_status_handler(ctx, mid)
    assert st.success
    low = st.output.lower()
    assert "en cours" in low and "ne la relance pas" in low

    rs = await M.mission_result_handler(ctx, mid)
    assert rs.success
    low = rs.output.lower()
    assert "en cours" in low and "ne la relance pas" in low


@pytest.mark.asyncio
async def test_mission_result_failed_says_no_result(tmp_path):
    # un état terminal NON-done (failed/cancelled) ≠ « en cours » → message distinct
    ctx, orch = _ctx(tmp_path)
    mgr = manager_mod.get_mission_manager(ctx.lumena)
    mid = mgr.create_mission("obj", metadata={"depth": 1})
    orch.mark_failed(mid, error="boom")
    res = await M.mission_result_handler(ctx, mid)
    assert res.success
    low = res.output.lower()
    assert "sans résultat" in low and "failed" in low
    assert "en cours" not in low


@pytest.mark.asyncio
async def test_mission_result_with_artifacts(tmp_path):
    ctx, orch = _ctx(tmp_path)
    mgr = manager_mod.get_mission_manager(ctx.lumena)
    mid = mgr.create_mission("obj", metadata={"depth": 1})
    orch.set_task_metadata(mid, artifacts=["workspace/inbound/f.txt"])
    orch.mark_done(mid, result_summary="rapport prêt")
    res = await M.mission_result_handler(ctx, mid)
    assert res.success
    assert "rapport prêt" in res.output
    assert "f.txt" in res.output
