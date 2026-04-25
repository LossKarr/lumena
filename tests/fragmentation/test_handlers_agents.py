"""
Tests unitaires pour handlers/agents.py — 12 handlers.

Convention: chaque handler reçoit un HandlerContext et retourne HandlerResult.
Les dépendances externes sont mockées via patch.dict(sys.modules).
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.agents import (
    delegate_task_handler,
    get_agents_status_handler,
    fork_analyze_handler,
    bg_start_handler,
    bg_status_handler,
    bg_list_handler,
    bg_cancel_handler,
    process_run_handler,
    process_status_handler,
    process_input_handler,
    process_kill_handler,
    process_list_handler,
    get_agents_handler_defs,
)


@pytest.fixture
def ctx(tmp_path):
    return HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=tmp_path / "workspace")


# ─── delegate_task ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_delegate_task_success(ctx):
    from src.agents.sub_agent import AgentResult, StatusCode
    _mock_result = AgentResult(
        task_id="t1", success=True, output="Done: analysis complete",
        status_code=StatusCode.SUCCESS, meta={"iterations": 3},
        artifacts=["file.py"], duration_ms=1500,
    )
    mock_mod = MagicMock()
    mock_mod.delegate_to_agent_full = AsyncMock(return_value=_mock_result)
    with patch.dict(sys.modules, {"src.agents.sub_agent": mock_mod}):
        r = await delegate_task_handler(ctx, description="analyze code", agent_type="code",
                                        project_path=str(ctx.runtime_root))
    assert r.success
    assert "Done" in r.output


@pytest.mark.asyncio
async def test_delegate_task_import_error(ctx):
    with patch.dict(sys.modules, {"src.agents.sub_agent": None}):
        r = await delegate_task_handler(ctx, description="test")
    assert not r.success
    assert "non disponible" in (r.error or r.output)


# ─── get_agents_status ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_agents_status_success(ctx):
    mock_orch = MagicMock()
    mock_orch.format_status.return_value = "All agents idle"
    mock_mod = MagicMock()
    mock_mod.get_orchestrator = MagicMock(return_value=mock_orch)
    with patch.dict(sys.modules, {"src.agents.sub_agent": mock_mod}):
        r = await get_agents_status_handler(ctx)
    assert r.success
    assert "idle" in r.output


@pytest.mark.asyncio
async def test_get_agents_status_import_error(ctx):
    with patch.dict(sys.modules, {"src.agents.sub_agent": None}):
        r = await get_agents_status_handler(ctx)
    assert not r.success


# ─── fork_analyze ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fork_analyze_success(ctx):
    mock_result = MagicMock()
    mock_result.meta = {"forks_succeeded": 4, "forks_total": 4}
    mock_result.output = "Consensus: go ahead"
    mock_agent = MagicMock()
    mock_agent.execute = AsyncMock(return_value=mock_result)
    mock_orch = MagicMock()
    mock_orch.get_agent_by_name.return_value = mock_agent

    mock_mod = MagicMock()
    mock_mod.get_orchestrator = MagicMock(return_value=mock_orch)
    mock_mod.AgentTask = MagicMock()
    mock_mod.AgentType = MagicMock()
    mock_mod.AgentType.GENERAL = "general"

    with patch.dict(sys.modules, {"src.agents.sub_agent": mock_mod}):
        r = await fork_analyze_handler(ctx, objective="Should we refactor?")
    assert r.success
    assert "Consensus" in r.output


@pytest.mark.asyncio
async def test_fork_analyze_no_agent(ctx):
    mock_orch = MagicMock()
    mock_orch.get_agent_by_name.return_value = None
    mock_mod = MagicMock()
    mock_mod.get_orchestrator = MagicMock(return_value=mock_orch)
    mock_mod.AgentTask = MagicMock()
    mock_mod.AgentType = MagicMock()
    mock_mod.AgentType.GENERAL = "general"

    with patch.dict(sys.modules, {"src.agents.sub_agent": mock_mod}):
        r = await fork_analyze_handler(ctx, objective="test")
    assert not r.success
    assert "ForkingAgent" in (r.error or r.output)


# ─── bg_start ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bg_start_success(ctx):
    mock_task = MagicMock()
    mock_task.id = "task_001"
    mock_task.name = "compile"
    mock_task.command = "make all"
    mock_task.status = MagicMock()
    mock_task.status.value = "running"
    mock_manager = MagicMock()
    mock_manager.start_command = AsyncMock(return_value=mock_task)
    mock_mod = MagicMock()
    mock_mod.get_task_manager = MagicMock(return_value=mock_manager)
    with patch.dict(sys.modules, {"src.background.manager": mock_mod}):
        r = await bg_start_handler(ctx, name="compile", command="make all")
    assert r.success
    assert "task_001" in r.output


@pytest.mark.asyncio
async def test_bg_start_import_error(ctx):
    with patch.dict(sys.modules, {"src.background.manager": None}):
        r = await bg_start_handler(ctx, name="test", command="echo hi")
    assert not r.success


# ─── bg_status ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bg_status_found(ctx):
    mock_manager = MagicMock()
    mock_manager.get_status = AsyncMock(return_value={
        "name": "compile", "status": "completed",
        "duration_seconds": 5.2, "output": "OK", "error": None,
    })
    mock_mod = MagicMock()
    mock_mod.get_task_manager = MagicMock(return_value=mock_manager)
    with patch.dict(sys.modules, {"src.background.manager": mock_mod}):
        r = await bg_status_handler(ctx, task_id="task_001")
    assert r.success
    assert "completed" in r.output


@pytest.mark.asyncio
async def test_bg_status_not_found(ctx):
    mock_manager = MagicMock()
    mock_manager.get_status = AsyncMock(return_value=None)
    mock_mod = MagicMock()
    mock_mod.get_task_manager = MagicMock(return_value=mock_manager)
    with patch.dict(sys.modules, {"src.background.manager": mock_mod}):
        r = await bg_status_handler(ctx, task_id="nope")
    assert not r.success
    assert "non trouvée" in (r.error or r.output)


# ─── bg_list ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bg_list_empty(ctx):
    mock_manager = MagicMock()
    mock_manager.get_all_tasks = AsyncMock(return_value=[])
    mock_mod = MagicMock()
    mock_mod.get_task_manager = MagicMock(return_value=mock_manager)
    with patch.dict(sys.modules, {"src.background.manager": mock_mod}):
        r = await bg_list_handler(ctx)
    assert r.success
    assert "Aucune" in r.output


@pytest.mark.asyncio
async def test_bg_list_with_tasks(ctx):
    mock_manager = MagicMock()
    mock_manager.get_all_tasks = AsyncMock(return_value=[
        {"id": "t1", "name": "scan", "status": "running"},
    ])
    mock_mod = MagicMock()
    mock_mod.get_task_manager = MagicMock(return_value=mock_manager)
    with patch.dict(sys.modules, {"src.background.manager": mock_mod}):
        r = await bg_list_handler(ctx)
    assert r.success
    assert "scan" in r.output


# ─── bg_cancel ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bg_cancel_success(ctx):
    mock_manager = MagicMock()
    mock_manager.cancel_task = AsyncMock(return_value=True)
    mock_mod = MagicMock()
    mock_mod.get_task_manager = MagicMock(return_value=mock_manager)
    with patch.dict(sys.modules, {"src.background.manager": mock_mod}):
        r = await bg_cancel_handler(ctx, task_id="t1")
    assert r.success
    assert "annulée" in r.output


@pytest.mark.asyncio
async def test_bg_cancel_failed(ctx):
    mock_manager = MagicMock()
    mock_manager.cancel_task = AsyncMock(return_value=False)
    mock_mod = MagicMock()
    mock_mod.get_task_manager = MagicMock(return_value=mock_manager)
    with patch.dict(sys.modules, {"src.background.manager": mock_mod}):
        r = await bg_cancel_handler(ctx, task_id="t1")
    assert not r.success


# ─── process_run ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_run_completed(ctx):
    mock_manager = MagicMock()
    mock_manager.run_background = AsyncMock(return_value=("Hello World", None))
    mock_mod = MagicMock()
    mock_mod.get_process_manager = MagicMock(return_value=mock_manager)
    with patch.dict(sys.modules, {"src.tools.process_manager": mock_mod}):
        r = await process_run_handler(ctx, command="echo hello")
    assert r.success
    assert "Hello World" in r.output


@pytest.mark.asyncio
async def test_process_run_background(ctx):
    mock_manager = MagicMock()
    mock_manager.run_background = AsyncMock(return_value=("Starting...", "proc_42"))
    mock_mod = MagicMock()
    mock_mod.get_process_manager = MagicMock(return_value=mock_manager)
    with patch.dict(sys.modules, {"src.tools.process_manager": mock_mod}):
        r = await process_run_handler(ctx, command="long_task")
    assert r.success
    assert "proc_42" in r.output


@pytest.mark.asyncio
async def test_process_run_import_error(ctx):
    with patch.dict(sys.modules, {"src.tools.process_manager": None}):
        r = await process_run_handler(ctx, command="echo hi")
    assert not r.success


# ─── process_status ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_status_success(ctx):
    mock_manager = MagicMock()
    mock_manager.get_status = AsyncMock(return_value="running (5s)")
    mock_mod = MagicMock()
    mock_mod.get_process_manager = MagicMock(return_value=mock_manager)
    with patch.dict(sys.modules, {"src.tools.process_manager": mock_mod}):
        r = await process_status_handler(ctx, process_id="p1")
    assert r.success


# ─── process_input ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_input_success(ctx):
    mock_manager = MagicMock()
    mock_manager.send_input = AsyncMock(return_value="Input sent")
    mock_mod = MagicMock()
    mock_mod.get_process_manager = MagicMock(return_value=mock_manager)
    with patch.dict(sys.modules, {"src.tools.process_manager": mock_mod}):
        r = await process_input_handler(ctx, process_id="p1", text="yes")
    assert r.success


# ─── process_kill ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_kill_success(ctx):
    mock_manager = MagicMock()
    mock_manager.terminate = AsyncMock(return_value="Terminated")
    mock_mod = MagicMock()
    mock_mod.get_process_manager = MagicMock(return_value=mock_manager)
    with patch.dict(sys.modules, {"src.tools.process_manager": mock_mod}):
        r = await process_kill_handler(ctx, process_id="p1")
    assert r.success


# ─── process_list ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_process_list_success(ctx):
    mock_manager = MagicMock()
    mock_manager.list_processes = AsyncMock(return_value="p1: running, p2: done")
    mock_mod = MagicMock()
    mock_mod.get_process_manager = MagicMock(return_value=mock_manager)
    with patch.dict(sys.modules, {"src.tools.process_manager": mock_mod}):
        r = await process_list_handler(ctx)
    assert r.success


# ─── HandlerDefs ───────────────────────────────────────────────────────────

def test_handler_defs_count():
    defs = get_agents_handler_defs()
    assert len(defs) == 13  # +1 delegate_task_bg


def test_handler_defs_names():
    defs = get_agents_handler_defs()
    names = [d.name for d in defs]
    assert len(names) == len(set(names))


def test_handler_defs_expected_names():
    expected = {
        "delegate_task", "delegate_task_bg", "get_agents_status", "fork_analyze",
        "bg_start", "bg_status", "bg_list", "bg_cancel",
        "process_run", "process_status", "process_input",
        "process_kill", "process_list",
    }
    defs = get_agents_handler_defs()
    actual = {d.name for d in defs}
    assert actual == expected


def test_handler_defs_have_handlers():
    for d in get_agents_handler_defs():
        assert callable(d.handler), f"{d.name} handler not callable"
