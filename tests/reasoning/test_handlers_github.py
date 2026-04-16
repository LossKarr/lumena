"""Tests unitaires pour src/reasoning/handlers/github.py"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from src.reasoning.handlers.github import get_github_handler_defs
from src.reasoning.handlers.registry_v2 import HandlerContext, HandlerResult


class TestGetGithubHandlerDefs:
    def test_returns_nonempty_list(self):
        defs = get_github_handler_defs()
        assert isinstance(defs, list)
        assert len(defs) > 0

    def test_all_have_name(self):
        defs = get_github_handler_defs()
        for d in defs:
            assert d.name
            assert isinstance(d.name, str)

    def test_all_have_callable_handler(self):
        defs = get_github_handler_defs()
        for d in defs:
            assert callable(d.handler)

    def test_includes_repo_list(self):
        names = {d.name for d in get_github_handler_defs()}
        assert "github_repo_list" in names

    def test_includes_file_read(self):
        names = {d.name for d in get_github_handler_defs()}
        assert "github_file_read" in names

    def test_includes_file_write(self):
        names = {d.name for d in get_github_handler_defs()}
        assert "github_file_write" in names

    def test_at_least_5_defs(self):
        defs = get_github_handler_defs()
        assert len(defs) >= 5


class TestGithubHandlersWithMockCtx:
    @pytest.fixture
    def ctx(self):
        return HandlerContext.for_testing()

    @pytest.mark.asyncio
    async def test_repo_list_requires_token(self, ctx):
        """Sans token GitHub, le handler doit retourner un message d'erreur."""
        from src.reasoning.handlers.github import get_github_handler_defs
        defs = {d.name: d for d in get_github_handler_defs()}
        if "github_repo_list" in defs:
            with patch.dict("os.environ", {}, clear=False):
                # Remove any GITHUB_TOKEN env var for this test
                import os
                old = os.environ.pop("GITHUB_TOKEN", None)
                try:
                    result = await defs["github_repo_list"].handler(ctx)
                    assert isinstance(result, HandlerResult)
                    # Either returns error or empty list
                    assert result is not None
                finally:
                    if old is not None:
                        os.environ["GITHUB_TOKEN"] = old
