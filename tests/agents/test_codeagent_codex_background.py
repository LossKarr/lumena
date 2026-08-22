from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.agents import codex_background
from src.agents.sub_agent import _bg_agent_tasks, cancel_bg_agent_task, is_bg_agent_active
from src.llm.codex_codeagent import CodexCodeAgentResult
from src.reasoning.handlers import agents
from src.reasoning.handlers.context import HandlerContext


class _FakeOrchestrator:
    def __init__(self) -> None:
        self.task_counter = 0
        self.pending_tasks: dict[str, dict] = {}
        self.save_count = 0

    def _save_to_disk(self) -> None:
        self.save_count += 1


async def _wait_until_finished(task_id: str) -> None:
    for _ in range(100):
        if not is_bg_agent_active(task_id):
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"background task {task_id} did not finish")


@pytest.fixture(autouse=True)
def _clean_bg_registry():
    _bg_agent_tasks.clear()
    yield
    for task in list(_bg_agent_tasks.values()):
        task.cancel()
    _bg_agent_tasks.clear()


@pytest.mark.asyncio
async def test_codex_background_uses_existing_registry_and_preserves_proof(monkeypatch):
    orchestrator = _FakeOrchestrator()
    monkeypatch.setattr(codex_background, "get_orchestrator", lambda: orchestrator)
    release = asyncio.Event()

    async def runner():
        await release.wait()
        return CodexCodeAgentResult(
            task_id="codex-inner",
            success=True,
            output="travail termine",
            meta={"model": "gpt-test", "green_test": True},
            artifacts=["C:/workspace/app.py"],
            duration_ms=42,
        )

    task_id = await codex_background.start_codex_codeagent_bg(
        "Construis le projet",
        agent_type="code",
        context={"workspace_path": "C:/workspace"},
        runner=runner,
    )

    assert task_id.startswith("ca_")
    assert orchestrator.pending_tasks[task_id]["status"] == "running"
    assert is_bg_agent_active(task_id)
    release.set()
    await _wait_until_finished(task_id)

    stored = orchestrator.pending_tasks[task_id]
    assert stored["status"] == "done"
    assert stored["engine"] == "codex_subscription"
    assert stored["model"] == "gpt-test"
    assert stored["meta"]["green_test"] is True
    assert stored["artifacts"] == ["C:/workspace/app.py"]
    assert stored["duration_ms"] == 42
    assert orchestrator.save_count >= 2


@pytest.mark.asyncio
async def test_codex_background_cancellation_uses_existing_cancel_registry(monkeypatch):
    orchestrator = _FakeOrchestrator()
    monkeypatch.setattr(codex_background, "get_orchestrator", lambda: orchestrator)
    cancelled = asyncio.Event()

    async def runner():
        try:
            await asyncio.sleep(100)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    task_id = await codex_background.start_codex_codeagent_bg(
        "Long travail",
        agent_type="code",
        context={},
        runner=runner,
    )
    assert cancel_bg_agent_task(task_id) is True
    await _wait_until_finished(task_id)

    assert cancelled.is_set()
    assert orchestrator.pending_tasks[task_id]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_delegate_task_bg_routes_codex_without_legacy_provider(
    tmp_path, monkeypatch
):
    workspace = tmp_path / "project"
    workspace.mkdir()
    ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=workspace)
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "chatgpt_codex")
    monkeypatch.setenv("LUMENA_CODEX_SURFACES", "codeagent")
    monkeypatch.setenv("LUMENA_CODEX_DEFAULT_MODEL", "gpt-test")
    monkeypatch.setattr(
        agents,
        "_mission_codeagent_scope",
        lambda *_args, **_kwargs: (str(workspace), ["app.py"]),
    )

    legacy = AsyncMock(return_value="ca-legacy")
    monkeypatch.setattr("src.agents.sub_agent.delegate_to_agent_bg", legacy)
    supervisor = SimpleNamespace(is_running=True)
    monkeypatch.setattr(
        "src.llm.codex_app_server.get_shared_codex_app_server",
        lambda: supervisor,
    )
    codex_run = AsyncMock(
        return_value=CodexCodeAgentResult(
            task_id="codex-inner",
            success=True,
            output="ok",
            meta={"model": "gpt-test", "engine": "codex_subscription"},
        )
    )
    monkeypatch.setattr(
        "src.llm.codex_codeagent.run_codeagent_with_codex_subscription",
        codex_run,
    )
    captured: dict = {}

    async def fake_start(description, **kwargs):
        captured.update(kwargs)
        captured["result"] = await kwargs["runner"]()
        return "ca-codex"

    monkeypatch.setattr(codex_background, "start_codex_codeagent_bg", fake_start)

    result = await agents.delegate_task_bg_handler(
        ctx,
        description="Implémente app.py",
        agent_type="code",
        project_path=str(workspace),
    )

    assert result.success is True
    assert "ca-codex" in result.output
    legacy.assert_not_awaited()
    codex_run.assert_awaited_once()
    call = codex_run.await_args
    assert call.kwargs["workspace_path"] == workspace
    assert call.kwargs["allowed_files"] == ["app.py"]
    assert call.kwargs["supervisor"] is supervisor
    assert captured["agent_type"] == "code"
    assert captured["result"].meta["engine"] == "codex_subscription"


@pytest.mark.asyncio
async def test_delegate_task_bg_fails_closed_without_codex_session(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=workspace)
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "chatgpt_codex")
    monkeypatch.setenv("LUMENA_CODEX_SURFACES", "codeagent")
    monkeypatch.setattr(
        "src.llm.codex_app_server.get_shared_codex_app_server", lambda: None
    )
    legacy = AsyncMock(return_value="ca-legacy")
    monkeypatch.setattr("src.agents.sub_agent.delegate_to_agent_bg", legacy)

    result = await agents.delegate_task_bg_handler(
        ctx,
        description="Implémente app.py",
        agent_type="code",
        project_path=str(workspace),
    )

    assert result.success is False
    assert result.status_code == "codex_not_connected"
    assert "Aucun fallback API" in result.output
    legacy.assert_not_awaited()


@pytest.mark.asyncio
async def test_delegate_task_bg_api_mode_keeps_historical_path(tmp_path, monkeypatch):
    workspace = tmp_path / "project"
    workspace.mkdir()
    ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=workspace)
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "api")
    legacy = AsyncMock(return_value="ca-legacy")
    monkeypatch.setattr("src.agents.sub_agent.delegate_to_agent_bg", legacy)

    result = await agents.delegate_task_bg_handler(
        ctx,
        description="Implémente app.py",
        agent_type="code",
        project_path=str(workspace),
    )

    assert result.success is True
    assert "ca-legacy" in result.output
    legacy.assert_awaited_once()


@pytest.mark.asyncio
async def test_bg_status_displays_codex_engine_and_model(monkeypatch):
    orchestrator = _FakeOrchestrator()
    orchestrator.pending_tasks["ca_codex"] = {
        "status": "done",
        "description": "Construis le projet",
        "output": "ok",
        "engine": "codex_subscription",
        "model": "gpt-test",
    }
    monkeypatch.setattr("src.agents.sub_agent.get_orchestrator", lambda: orchestrator)

    result = await agents.bg_status_handler(HandlerContext(), task_id="ca_codex")

    assert result.success is True
    assert "abonnement ChatGPT Codex" in result.output
    assert "gpt-test" in result.output
