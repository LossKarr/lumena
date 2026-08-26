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
async def test_delegate_task_bg_routes_codex_through_historic_loop(
    tmp_path, monkeypatch
):
    """L'abonnement Codex passe par la boucle CodeAgent HISTORIQUE.

    Comportement precedent — desormais retire : `delegate_task_bg` quittait la
    boucle historique pour `run_codeagent_with_codex_subscription`, un tour Codex
    autonome. Prompts, outils, tests, reprises et garde-fous de Lumena etaient
    contournes, et chaque delegation rouvrait une session (`account/read` puis
    `model/list`, 30 s chacun au timeout sur un run reel).

    Ce test decrit la garantie qui remplace : un seul moteur, l'abonnement comme
    simple cerveau, et le marqueur transmis par le contexte de tache — un dict
    SERIALISABLE, seul moyen de survivre au lancement en arriere-plan.
    """
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
    supervisor = SimpleNamespace(is_running=True)
    monkeypatch.setattr(
        "src.llm.codex_app_server.get_shared_codex_app_server",
        lambda: supervisor,
    )

    historique = AsyncMock(return_value="ca-legacy")
    monkeypatch.setattr("src.agents.sub_agent.delegate_to_agent_bg", historique)
    rail_autonome = AsyncMock()
    monkeypatch.setattr(
        "src.llm.codex_codeagent.run_codeagent_with_codex_subscription",
        rail_autonome,
    )

    result = await agents.delegate_task_bg_handler(
        ctx,
        description="Implémente app.py",
        agent_type="code",
        project_path=str(workspace),
    )

    assert result.success is True
    # La boucle historique est le SEUL moteur.
    historique.assert_awaited_once()
    # Et le rail autonome n'est plus jamais emprunte.
    rail_autonome.assert_not_awaited()

    # Le marqueur voyage dans le contexte : serialisable, donc il survit au
    # lancement en arriere-plan.
    contexte = historique.await_args.args[2]
    assert contexte["_codex_brain"] is True


@pytest.mark.asyncio
async def test_delegate_task_sync_routes_codex_through_historic_loop(
    tmp_path, monkeypatch
):
    """Le chemin synchrone utilise lui aussi le moteur historique, sans rail autonome."""
    workspace = tmp_path / "project"
    workspace.mkdir()
    ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=workspace)
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "chatgpt_codex")
    monkeypatch.setenv("LUMENA_CODEX_SURFACES", "codeagent")
    monkeypatch.setattr(
        agents,
        "_mission_codeagent_scope",
        lambda *_args, **_kwargs: (str(workspace), ["app.py"]),
    )
    monkeypatch.setattr(
        "src.llm.codex_app_server.get_shared_codex_app_server",
        lambda: SimpleNamespace(is_running=True),
    )
    historique = AsyncMock(
        return_value=SimpleNamespace(
            success=True,
            output="travail termine",
            artifacts=[],
            meta={
                "iterations": 1,
                "engine": "codex_subscription",
                "model": "gpt-5.6-sol",
                "fallback_used": False,
            },
            duration_ms=10,
            status_code="success",
        )
    )
    monkeypatch.setattr("src.agents.sub_agent.delegate_to_agent_full", historique)
    rail_autonome = AsyncMock()
    monkeypatch.setattr(
        "src.llm.codex_codeagent.run_codeagent_with_codex_subscription",
        rail_autonome,
    )

    result = await agents.delegate_task_handler(
        ctx,
        description="Implemente app.py",
        agent_type="code",
        project_path=str(workspace),
    )

    assert result.success is True
    assert "**Moteur** : abonnement ChatGPT Codex" in result.output
    assert "**Modèle réel** : `gpt-5.6-sol`" in result.output
    assert "**Fallback API** : aucun" in result.output
    historique.assert_awaited_once()
    rail_autonome.assert_not_awaited()
    assert historique.await_args.args[2]["_codex_brain"] is True


@pytest.mark.asyncio
async def test_delegate_task_bg_marker_is_serialisable(tmp_path, monkeypatch):
    """Le marqueur DOIT rester serialisable : un objet en memoire serait perdu.

    C'est la contrainte qui a dicte la conception. Un cerveau passe comme objet
    ne franchit pas la frontiere du lancement background ; un booleen dans le
    contexte, si.
    """
    import json

    workspace = tmp_path / "project"
    workspace.mkdir()
    ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=workspace)
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "chatgpt_codex")
    monkeypatch.setenv("LUMENA_CODEX_SURFACES", "codeagent")
    monkeypatch.setattr(
        agents,
        "_mission_codeagent_scope",
        lambda *_args, **_kwargs: (str(workspace), ["app.py"]),
    )
    monkeypatch.setattr(
        "src.llm.codex_app_server.get_shared_codex_app_server",
        lambda: SimpleNamespace(is_running=True),
    )
    historique = AsyncMock(return_value="ca-legacy")
    monkeypatch.setattr("src.agents.sub_agent.delegate_to_agent_bg", historique)

    await agents.delegate_task_bg_handler(
        ctx,
        description="Implémente app.py",
        agent_type="code",
        project_path=str(workspace),
    )

    contexte = historique.await_args.args[2]
    json.dumps(contexte)  # leve si un objet non serialisable s'y est glisse


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
