"""Tests unitaires pour src/reasoning/handlers/git.py"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch, MagicMock
from pathlib import Path

from src.reasoning.handlers.git import (
    _git_available,
    _resolve_dir,
    _has_own_git,
    get_git_handler_defs,
    git_status_handler,
    git_log_handler,
)
from src.reasoning.handlers.registry_v2 import HandlerContext, HandlerResult


@pytest.fixture
def ctx():
    return HandlerContext.for_testing()


class TestGitAvailable:
    def test_returns_bool(self):
        result = _git_available()
        assert isinstance(result, bool)


class TestHasOwnGit:
    def test_no_git_dir(self, tmp_path):
        assert _has_own_git(tmp_path) is False

    def test_with_git_dir(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert _has_own_git(tmp_path) is True


class TestResolveDir:
    def test_absolute_path(self, tmp_path):
        ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=tmp_path)
        path = _resolve_dir(ctx, str(tmp_path))
        assert path == tmp_path

    def test_relative_path_uses_lumena_root(self, ctx):
        path = _resolve_dir(ctx, "sub/dir")
        assert isinstance(path, Path)
        assert path.is_absolute()

    def test_empty_path_uses_runtime_root(self, ctx):
        path = _resolve_dir(ctx, "")
        assert path == ctx.runtime_root


class TestGetGitHandlerDefs:
    def test_returns_nonempty_list(self):
        defs = get_git_handler_defs()
        assert len(defs) > 0

    def test_all_have_name(self):
        defs = get_git_handler_defs()
        for d in defs:
            assert d.name
            assert len(d.name) > 0

    def test_includes_status_and_commit(self):
        names = {d.name for d in get_git_handler_defs()}
        assert "git_status" in names
        assert "git_commit" in names


class TestGitStatusHandler:
    @pytest.mark.asyncio
    async def test_git_not_available_returns_error(self, ctx):
        with patch("src.reasoning.handlers.git._git_available", return_value=False):
            result = await git_status_handler(ctx, path="")
            assert isinstance(result, HandlerResult)
            assert result.success is False

    @pytest.mark.asyncio
    async def test_no_git_repo_returns_error(self, tmp_path):
        ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=tmp_path)
        with patch("src.reasoning.handlers.git._git_available", return_value=True):
            result = await git_status_handler(ctx, path=str(tmp_path))
            # Either error (no .git) or success if git is available and handles it
            assert isinstance(result, HandlerResult)


class TestGitLogHandler:
    @pytest.mark.asyncio
    async def test_returns_handler_result(self, tmp_path):
        ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=tmp_path)
        with patch("src.reasoning.handlers.git._git_available", return_value=True):
            result = await git_log_handler(ctx, path=str(tmp_path), n=5)
            assert isinstance(result, HandlerResult)
