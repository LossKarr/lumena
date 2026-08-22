from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers import project


def _ctx(tmp_path: Path, *, model_name: str = "deepseek-v3") -> HandlerContext:
    runtime_root = tmp_path / "workspace" / "2026-08-16"
    runtime_root.mkdir(parents=True)
    ctx = HandlerContext.for_testing(
        lumena_root=tmp_path,
        runtime_root=runtime_root,
    )
    ctx.lumena = SimpleNamespace(
        llm=SimpleNamespace(model_name=model_name),
    )
    return ctx


def test_direct_codeagent_relative_output_is_anchored_to_runtime(tmp_path):
    ctx = _ctx(tmp_path)

    target = project._resolve_direct_codeagent_output(
        ctx,
        output_dir="cinema-motion-studio",
        project_slug="cinema-motion-studio",
    )

    assert target == (
        ctx.runtime_root / "cinema-motion-studio"
    ).resolve()
    assert target != (ctx.lumena_root / "cinema-motion-studio").resolve()


def test_direct_codeagent_relative_output_cannot_escape_runtime(tmp_path):
    ctx = _ctx(tmp_path)

    with pytest.raises(ValueError, match="hors du workspace"):
        project._resolve_direct_codeagent_output(
            ctx,
            output_dir="../../outside",
            project_slug="outside",
        )


def test_model_supplied_absolute_output_outside_runtime_is_rejected(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.original_user_query = "Crée-moi un nouveau site de cinéma"
    invented = ctx.lumena_root / "invented-project"

    with pytest.raises(ValueError, match="hors du workspace courant"):
        project._resolve_direct_codeagent_output(
            ctx,
            output_dir=str(invented),
            project_slug="invented-project",
        )


def test_user_supplied_absolute_output_remains_allowed(tmp_path):
    ctx = _ctx(tmp_path)
    requested = ctx.lumena_root / "explicit-project"
    ctx.original_user_query = f"Crée le projet dans {requested}"

    target = project._resolve_direct_codeagent_output(
        ctx,
        output_dir=str(requested),
        project_slug="explicit-project",
    )

    assert target == requested.resolve()


@pytest.mark.asyncio
async def test_create_project_codex_never_calls_legacy_deepseek(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    legacy = AsyncMock(return_value="legacy deepseek")
    codex = AsyncMock(
        return_value=HandlerResult.ok(
            "Codex terminé",
            handler_name="delegate_task",
            status_code="success",
        )
    )
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "chatgpt_codex")
    monkeypatch.setenv("LUMENA_CODEX_SURFACES", "codeagent")
    monkeypatch.setenv("LUMENA_CODEX_API_FALLBACK", "never")
    monkeypatch.setattr(project, "_CODEAGENT_AVAILABLE", True)
    monkeypatch.setattr(project, "_delegate_to_agent", legacy)
    monkeypatch.setattr(
        "src.reasoning.handlers.agents.delegate_task_handler", codex
    )
    monkeypatch.setattr(
        "src.utils.project_registry.register_project", MagicMock()
    )

    result = await project.create_project_handler(
        ctx,
        description="Un site de cinéma complet",
        project_name="cinema-motion-studio",
        output_dir="cinema-motion-studio",
    )

    assert result.success is True
    legacy.assert_not_awaited()
    codex.assert_awaited_once()
    assert Path(codex.await_args.kwargs["project_path"]) == (
        ctx.runtime_root / "cinema-motion-studio"
    ).resolve()
    assert not (ctx.lumena_root / "cinema-motion-studio").exists()


@pytest.mark.asyncio
async def test_create_project_codex_does_not_require_api_llm(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    ctx.lumena = SimpleNamespace()
    legacy = AsyncMock(return_value="legacy deepseek")
    codex = AsyncMock(
        return_value=HandlerResult.ok(
            "Codex termine",
            handler_name="delegate_task",
            status_code="success",
        )
    )
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "chatgpt_codex")
    monkeypatch.setenv("LUMENA_CODEX_SURFACES", "codeagent")
    monkeypatch.setenv("LUMENA_CODEX_API_FALLBACK", "never")
    monkeypatch.setattr(project, "_CODEAGENT_AVAILABLE", True)
    monkeypatch.setattr(project, "_delegate_to_agent", legacy)
    monkeypatch.setattr(
        "src.reasoning.handlers.agents.delegate_task_handler", codex
    )
    monkeypatch.setattr(
        "src.utils.project_registry.register_project", MagicMock()
    )

    result = await project.create_project_handler(
        ctx,
        description="Un site de cinema complet",
        project_name="cinema-motion-studio",
        output_dir="cinema-motion-studio",
    )

    assert result.success is True
    legacy.assert_not_awaited()
    codex.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_project_codex_failure_is_fail_closed(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    ctx.lumena.llm.chat = AsyncMock()
    legacy = AsyncMock(return_value="legacy deepseek")
    codex = AsyncMock(
        return_value=HandlerResult.fail(
            "Session Codex indisponible",
            handler_name="delegate_task",
            status_code="codex_not_connected",
        )
    )
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "chatgpt_codex")
    monkeypatch.setenv("LUMENA_CODEX_SURFACES", "codeagent")
    monkeypatch.setenv("LUMENA_CODEX_API_FALLBACK", "never")
    monkeypatch.setattr(project, "_CODEAGENT_AVAILABLE", True)
    monkeypatch.setattr(project, "_delegate_to_agent", legacy)
    monkeypatch.setattr(
        "src.reasoning.handlers.agents.delegate_task_handler", codex
    )

    result = await project.create_project_handler(
        ctx,
        description="Un site de cinéma complet",
        project_name="cinema-motion-studio",
        output_dir="cinema-motion-studio",
    )

    assert result.success is False
    assert result.status_code == "codex_not_connected"
    assert "Aucun fallback DeepSeek" in result.output
    legacy.assert_not_awaited()
    ctx.lumena.llm.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_project_api_keeps_legacy_deepseek_route(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    legacy = AsyncMock(return_value="DeepSeek CodeAgent terminé")
    codex = AsyncMock()
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "api")
    monkeypatch.setattr(project, "_CODEAGENT_AVAILABLE", True)
    monkeypatch.setattr(project, "_delegate_to_agent", legacy)
    monkeypatch.setattr(
        "src.reasoning.handlers.agents.delegate_task_handler", codex
    )
    monkeypatch.setattr(
        "src.utils.project_registry.register_project", MagicMock()
    )

    result = await project.create_project_handler(
        ctx,
        description="Un site de cinéma complet",
        project_name="cinema-motion-studio",
        output_dir="cinema-motion-studio",
    )

    assert result.success is True
    legacy.assert_awaited_once()
    codex.assert_not_awaited()
    assert legacy.await_args.kwargs["context"]["_best_model"] == "deepseek-v3"
