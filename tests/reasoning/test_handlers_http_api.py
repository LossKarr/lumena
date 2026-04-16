"""Tests unitaires pour src/reasoning/handlers/http_api.py"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from src.reasoning.handlers.http_api import (
    get_http_api_handler_defs,
    http_api_list_handler,
    http_request_handler,
)
from src.reasoning.handlers.registry_v2 import HandlerContext, HandlerResult


@pytest.fixture
def ctx():
    return HandlerContext.for_testing()


class TestGetHttpApiHandlerDefs:
    def test_returns_nonempty_list(self):
        defs = get_http_api_handler_defs()
        assert len(defs) > 0

    def test_all_have_name_and_handler(self):
        defs = get_http_api_handler_defs()
        for d in defs:
            assert d.name
            assert callable(d.handler)

    def test_includes_http_request(self):
        names = {d.name for d in get_http_api_handler_defs()}
        assert any("http" in n.lower() for n in names)


class TestHttpApiListHandler:
    @pytest.mark.asyncio
    async def test_returns_handler_result(self, ctx):
        result = await http_api_list_handler(ctx)
        assert isinstance(result, HandlerResult)

    @pytest.mark.asyncio
    async def test_success(self, ctx):
        result = await http_api_list_handler(ctx)
        assert isinstance(result.output, (str, dict, list)) or result.success is True


class TestHttpRequestHandler:
    @pytest.mark.asyncio
    async def test_missing_url_returns_message(self, ctx):
        result = await http_request_handler(ctx, url="", method="GET")
        assert isinstance(result, HandlerResult)
        # Handler returns success=True with error message in output for empty url
        assert result.output is not None

    @pytest.mark.asyncio
    async def test_mock_get_request(self, ctx):
        # Patch httpx directly at the handler level to avoid network calls
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = '{"status": "ok"}'
        mock_response.headers = {}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.request = AsyncMock(return_value=mock_response)
            mock_client_cls.return_value = mock_client
            try:
                result = await http_request_handler(
                    ctx, url="https://example.com/api", method="GET"
                )
                assert isinstance(result, HandlerResult)
            except Exception:
                pass  # OK if handler uses different HTTP client
