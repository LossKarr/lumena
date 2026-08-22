"""Lot 1.2 — Runner de mission.

Vérifie : succès→`done`+artefacts ; exception→`failed` (jamais levée) ; annulation
(avant/pendant)→`cancelled` non écrasé ; le **registre ISOLÉ** (factory) est bien
passé à `think_and_act_silent` (et PAS celui du chat / PAS le CodeAgent).
"""
from __future__ import annotations

import asyncio
import types

import pytest

from src.runtime.task_orchestrator import TaskOrchestrator
from src.subagents import runner as runner_mod


@pytest.fixture
def _stub_registry(monkeypatch):
    """Évite de construire un vrai ToolRegistry ; sert de sentinelle vérifiable."""
    sentinel = object()
    monkeypatch.setattr(runner_mod, "create_mission_registry", lambda core: sentinel)
    return sentinel


def _core_with(orch, silent):
    core = types.SimpleNamespace(task_orchestrator=orch)
    core.think_and_act_silent = silent
    return core


def _new_mission(orch, objective="do X"):
    rec = orch.start_task(conversation_id="__missions__", channel="mission",
                          message_preview=objective, metadata={"kind": "mission"})
    return rec.task_id


@pytest.mark.asyncio
async def test_success_marks_done_with_artifacts(tmp_path, _stub_registry):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    captured = {}

    async def silent(objective, **kw):
        captured.update(kw)
        kw["artifacts_out"].append("workspace/inbound/f.txt")
        return "résultat X"

    core = _core_with(orch, silent)
    mid = _new_mission(orch)
    out = await runner_mod.run_mission(core, mission_id=mid, objective="do X")

    assert out["status"] == "done"
    assert out["result"] == "résultat X"
    assert out["artifacts"] == ["workspace/inbound/f.txt"]
    assert orch.get_task(mid)["state"] == "done"
    # registre ISOLÉ (sentinelle factory) passé — pas le registre du chat
    assert captured["tool_registry"] is _stub_registry
    assert captured["task_id"] == mid
    assert captured["allow_when_busy"] is True
    assert captured["task_orchestrator"] is orch


@pytest.mark.asyncio
async def test_exception_marks_failed_never_raises(tmp_path, _stub_registry):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))

    async def silent(objective, **kw):
        raise RuntimeError("boom")

    core = _core_with(orch, silent)
    mid = _new_mission(orch)
    out = await runner_mod.run_mission(core, mission_id=mid, objective="x")

    assert out["status"] == "failed"
    assert orch.get_task(mid)["state"] == "failed"


@pytest.mark.asyncio
async def test_provider_error_is_failed_with_authoritative_reason(
    tmp_path, _stub_registry
):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))

    async def silent(objective, **kw):
        raise RuntimeError(
            "llm_provider_error: [Erreur] Client error '402 Payment Required'"
        )

    core = _core_with(orch, silent)
    mid = _new_mission(orch)
    out = await runner_mod.run_mission(core, mission_id=mid, objective="x")

    task = orch.get_task(mid)
    assert out["status"] == "failed"
    assert task["state"] == "failed"
    assert task["metadata"]["terminal_reason_code"] == "provider_error"
    assert "402 Payment Required" in task["metadata"]["terminal_reason_detail"]


@pytest.mark.asyncio
async def test_cancelled_before_launch_does_not_run(tmp_path, _stub_registry):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    ran = {"called": False}

    async def silent(objective, **kw):
        ran["called"] = True
        return "ne devrait pas tourner"

    core = _core_with(orch, silent)
    mid = _new_mission(orch)
    orch.cancel_task(mid)  # annulée avant lancement
    out = await runner_mod.run_mission(core, mission_id=mid, objective="x")

    assert out["status"] == "cancelled"
    assert ran["called"] is False  # le worker n'a pas été lancé
    assert orch.get_task(mid)["state"] == "cancelled"


@pytest.mark.asyncio
async def test_cancelled_during_run_not_overwritten(tmp_path, _stub_registry):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    mid_box = {}

    async def silent(objective, **kw):
        orch.cancel_task(mid_box["id"])  # annulation coopérative pendant le run
        return "partiel"

    core = _core_with(orch, silent)
    mid = _new_mission(orch)
    mid_box["id"] = mid
    out = await runner_mod.run_mission(core, mission_id=mid, objective="x")

    assert out["status"] == "cancelled"
    # l'état `cancelled` n'est PAS écrasé par `done`
    assert orch.get_task(mid)["state"] == "cancelled"


@pytest.mark.asyncio
async def test_no_orchestrator_is_non_fatal(_stub_registry):
    core = types.SimpleNamespace(task_orchestrator=None)

    async def silent(objective, **kw):
        return "ok"

    core.think_and_act_silent = silent
    out = await runner_mod.run_mission(core, mission_id="m1", objective="x")
    assert out["status"] == "done"
    assert out["result"] == "ok"


@pytest.mark.asyncio
async def test_recovery_run_enforces_hard_allowed_tools(tmp_path, _stub_registry):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    captured = {}

    async def silent(objective, **kw):
        captured.update(kw)
        return "récupéré"

    core = _core_with(orch, silent)
    mid = _new_mission(orch)
    orch.set_task_metadata(mid, recovery_required=True)
    out = await runner_mod.run_mission(
        core,
        mission_id=mid,
        objective="[RECUPERATION] vérifier le workspace",
        allowed_tools=["read_file", "run_command"],
    )

    assert out["status"] == "done"
    assert captured["allowed_tools_hard"] is True
    meta = orch.get_task(mid)["metadata"]
    assert meta["recovery_required"] is False
    assert meta["recovery_completed"] is True


@pytest.mark.asyncio
async def test_normal_run_keeps_soft_tool_filter(tmp_path, _stub_registry):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    captured = {}

    async def silent(objective, **kw):
        captured.update(kw)
        return "ok"

    core = _core_with(orch, silent)
    mid = _new_mission(orch)
    await runner_mod.run_mission(
        core, mission_id=mid, objective="normal", allowed_tools=["read_file"],
    )
    assert captured["allowed_tools_hard"] is False


@pytest.mark.asyncio
async def test_react_failure_state_is_not_overwritten_by_done(tmp_path, _stub_registry):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    mid_box = {}

    async def silent(objective, **kw):
        orch.mark_failed(mid_box["id"], "iteration_limit_reached_without_final_answer")
        return "J'ai atteint la limite d'iterations. Travail partiel disponible."

    core = _core_with(orch, silent)
    mid = _new_mission(orch)
    mid_box["id"] = mid
    out = await runner_mod.run_mission(core, mission_id=mid, objective="x")

    task = orch.get_task(mid)
    assert out["status"] == "failed"
    assert "Travail partiel" in out["result"]
    assert task["state"] == "failed"
    assert task["metadata"]["terminal_reason_code"] == "iteration_limit"
    assert task["metadata"]["completion_proven"] is False


@pytest.mark.asyncio
async def test_empty_result_is_failed_not_done(tmp_path, _stub_registry):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))

    async def silent(objective, **kw):
        return "   "

    core = _core_with(orch, silent)
    mid = _new_mission(orch)
    out = await runner_mod.run_mission(core, mission_id=mid, objective="x")

    task = orch.get_task(mid)
    assert out["status"] == "failed"
    assert task["state"] == "failed"
    assert task["metadata"]["terminal_reason_code"] == "empty_result"


@pytest.mark.asyncio
async def test_mission_timeout_has_authoritative_reason(tmp_path, _stub_registry):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))

    async def silent(objective, **kw):
        raise asyncio.TimeoutError("mission_timeout:600s")

    core = _core_with(orch, silent)
    mid = _new_mission(orch)
    out = await runner_mod.run_mission(core, mission_id=mid, objective="x")

    task = orch.get_task(mid)
    assert out["status"] == "failed"
    assert task["state"] == "failed"
    assert task["metadata"]["terminal_reason_code"] == "timeout"
