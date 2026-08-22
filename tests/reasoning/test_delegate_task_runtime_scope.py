from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.reasoning.handlers import agents
from src.reasoning.handlers.context import HandlerContext


def _agent_result():
    return SimpleNamespace(
        success=True,
        output="CodeAgent terminé",
        artifacts=[],
        meta={"iterations": 1},
        duration_ms=1000,
        status_code="success",
    )


@pytest.mark.asyncio
async def test_model_supplied_project_path_outside_runtime_is_rejected(
    tmp_path, monkeypatch
):
    runtime = tmp_path / "workspace" / "2026-08-16"
    runtime.mkdir(parents=True)
    outside = tmp_path / "workspace" / "wrong-project"
    outside.mkdir(parents=True)
    ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=runtime)
    ctx.original_user_query = "Crée-moi un nouveau site de cinéma"
    legacy = AsyncMock(return_value=_agent_result())
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "api")
    monkeypatch.setattr("src.agents.sub_agent.delegate_to_agent_full", legacy)

    result = await agents.delegate_task_handler(
        ctx,
        description="Crée le site dans le dossier indiqué",
        agent_type="code",
        project_path=str(outside),
    )

    assert result.success is False
    assert "hors scope" in result.output
    legacy.assert_not_awaited()


@pytest.mark.asyncio
async def test_user_supplied_project_path_outside_runtime_remains_allowed(
    tmp_path, monkeypatch
):
    runtime = tmp_path / "workspace" / "2026-08-16"
    runtime.mkdir(parents=True)
    outside = tmp_path / "workspace" / "existing-project"
    outside.mkdir(parents=True)
    ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=runtime)
    ctx.original_user_query = f"Modifie le projet dans {outside}"
    legacy = AsyncMock(return_value=_agent_result())
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "api")
    monkeypatch.setattr("src.agents.sub_agent.delegate_to_agent_full", legacy)

    result = await agents.delegate_task_handler(
        ctx,
        description="Modifie le projet demandé",
        agent_type="code",
        project_path=str(outside),
    )

    assert result.success is True
    legacy.assert_awaited_once()

