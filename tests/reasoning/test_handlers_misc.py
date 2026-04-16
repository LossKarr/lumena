"""Tests unitaires pour les handlers handlers.codebase, heartbeat_self, context, osint, lsp, perception"""
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from pathlib import Path

from src.reasoning.handlers.registry_v2 import HandlerContext, HandlerResult


@pytest.fixture
def ctx(tmp_path):
    mock = MagicMock(spec=HandlerContext)
    mock.workspace = tmp_path
    mock.memory = MagicMock()
    return mock


# ─── codebase handler ─────────────────────────────────────────────────────────

class TestCodebaseHandlerDefs:
    def test_returns_list(self):
        from src.reasoning.handlers.codebase import get_codebase_handler_defs
        defs = get_codebase_handler_defs()
        assert len(defs) > 0

    def test_all_have_names(self):
        from src.reasoning.handlers.codebase import get_codebase_handler_defs
        for d in get_codebase_handler_defs():
            assert d.name


class TestCodebaseStatsHandler:
    @pytest.mark.asyncio
    async def test_returns_handler_result(self, ctx, tmp_path):
        from src.reasoning.handlers.codebase import codebase_stats_handler
        with patch("src.reasoning.handlers.codebase._get_index") as mock_get:
            mock_index = MagicMock()
            mock_index.collection = None
            mock_get.return_value = mock_index
            result = await codebase_stats_handler(ctx)
            assert isinstance(result, HandlerResult)


# ─── heartbeat_self handler ───────────────────────────────────────────────────

class TestHeartbeatSelfHandlerDefs:
    def test_returns_list(self):
        from src.reasoning.handlers.heartbeat_self import get_heartbeat_self_handler_defs
        defs = get_heartbeat_self_handler_defs()
        assert len(defs) > 0

    def test_includes_heartbeat_manage(self):
        from src.reasoning.handlers.heartbeat_self import get_heartbeat_self_handler_defs
        names = {d.name for d in get_heartbeat_self_handler_defs()}
        assert "heartbeat_manage" in names or any("heartbeat" in n for n in names)


# ─── context handler ──────────────────────────────────────────────────────────

class TestContextHandlerDefs:
    def test_handler_context_importable(self):
        from src.reasoning.handlers.context import HandlerContext
        assert HandlerContext is not None


# ─── osint handler ────────────────────────────────────────────────────────────

class TestOsintHandlerDefs:
    def test_returns_list(self):
        try:
            from src.reasoning.handlers.osint import get_osint_handler_defs
            defs = get_osint_handler_defs()
            assert isinstance(defs, list)
        except ImportError:
            pytest.skip("osint handler not available")


# ─── perception handler ───────────────────────────────────────────────────────

class TestPerceptionHandlerDefs:
    def test_returns_list(self):
        try:
            from src.reasoning.handlers.perception import get_perception_handler_defs
            defs = get_perception_handler_defs()
            assert isinstance(defs, list)
        except ImportError:
            pytest.skip("perception handler not available")


# ─── custom handler ───────────────────────────────────────────────────────────

class TestCustomHandlerDefs:
    def test_returns_list(self):
        from src.reasoning.handlers.custom import get_custom_tool_handler_defs
        defs = get_custom_tool_handler_defs()
        assert isinstance(defs, list)


# ─── lsp handler ─────────────────────────────────────────────────────────────

class TestLspHandlerDefs:
    def test_returns_list(self):
        try:
            from src.reasoning.handlers.lsp import get_lsp_handler_defs
            defs = get_lsp_handler_defs()
            assert isinstance(defs, list)
        except ImportError:
            pytest.skip("lsp handler not available")
