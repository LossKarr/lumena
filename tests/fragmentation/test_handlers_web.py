"""
test_handlers_web.py - Tests fonctionnels des handlers web fragmentés.

Teste chaque handler de web.py avec un HandlerContext de test.
"""

import sys

import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.web import (
    web_search_real_handler,
    web_search_brave_handler,
    web_fetch_handler,
    deep_research_handler,
    web_crawl_campaign_handler,
    web_crawl_campaign_status_handler,
    web_crawl_campaign_pro_report_handler,
    web_crawl_campaign_explain_handler,
    _resolve_web_crawl_profile,
    get_web_handler_defs,
)


@pytest.fixture
def ctx(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return HandlerContext.for_testing(
        lumena_root=tmp_path,
        runtime_root=workspace,
    )


# ─── _resolve_web_crawl_profile ───────────────────────────────────────────

class TestResolveProfile:
    def test_fast(self):
        p = _resolve_web_crawl_profile("fast")
        assert p["max_depth"] == 1
        assert p["request_retries"] == 0

    def test_balanced(self):
        p = _resolve_web_crawl_profile("balanced")
        assert p["max_depth"] == 2
        assert p["request_retries"] == 1

    def test_deep(self):
        p = _resolve_web_crawl_profile("deep")
        assert p["max_depth"] == 3
        assert p["request_retries"] == 2

    def test_default_is_balanced(self):
        p = _resolve_web_crawl_profile("")
        assert p["max_depth"] == 2

    def test_case_insensitive(self):
        p = _resolve_web_crawl_profile("FAST")
        assert p["max_depth"] == 1


# ─── web_search_real ───────────────────────────────────────────────────────

class TestWebSearchReal:
    @pytest.mark.asyncio
    async def test_browser_success(self, ctx):
        mock_browser = MagicMock()
        mock_browser.search_google = AsyncMock(return_value={
            "success": True,
            "source": "Google",
            "results_count": 2,
            "results": [
                {"position": 1, "title": "Result One", "url": "https://example.com", "description": "Desc one"},
                {"position": 2, "title": "Result Two", "url": "https://two.com", "description": "Desc two"},
            ],
        })
        mock_module = MagicMock()
        mock_module.get_playwright_browser.return_value = mock_browser
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mock_module}):
            r = await web_search_real_handler(ctx, query="python async")
            assert r.success
            assert "Result One" in r.output
            assert "Result Two" in r.output
            assert r.handler_name == "web_search"

    @pytest.mark.asyncio
    async def test_browser_import_error_fallback(self, ctx):
        """Si get_playwright_browser n'est pas importable, fallback webbrowser."""
        with patch.dict(sys.modules, {"src.tools.playwright_browser": None}):
            with patch("webbrowser.open") as mock_open:
                r = await web_search_real_handler(ctx, query="test query")
                assert r.success
                assert "Recherche lancée" in r.output or "Recherche ouverte" in r.output
                mock_open.assert_called_once()


# ─── web_search_brave ──────────────────────────────────────────────────────

class TestWebSearchBrave:
    @pytest.mark.asyncio
    async def test_success(self, ctx):
        mock_hub = AsyncMock()
        mock_hub.web_search.return_value = {"results": [{"title": "Brave R1"}]}
        ctx._search_hub = mock_hub
        # Mock le module search_hub pour bloquer le vrai import
        mock_sh_module = MagicMock()
        mock_sh_module.SearchHub.format_results.return_value = "Brave R1 formatted"
        with patch.dict(sys.modules, {"src.tools.search_hub": mock_sh_module}):
            r = await web_search_brave_handler(ctx, query="python", count=3)
            assert r.success
            assert "Brave R1" in r.output

    @pytest.mark.asyncio
    async def test_error(self, ctx):
        mock_hub = AsyncMock()
        mock_hub.web_search.side_effect = RuntimeError("No API key")
        ctx._search_hub = mock_hub
        r = await web_search_brave_handler(ctx, query="test")
        assert not r.success
        assert "Erreur" in r.output


# ─── web_fetch ─────────────────────────────────────────────────────────────

class TestWebFetch:
    @pytest.mark.asyncio
    async def test_success(self, ctx):
        html = "<html><body><p>Hello World</p></body></html>"
        mock_response = MagicMock()
        mock_response.read.return_value = html.encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            r = await web_fetch_handler(ctx, url="https://example.com")
            assert r.success
            assert "Hello World" in r.output
            assert "example.com" in r.output

    @pytest.mark.asyncio
    async def test_url_error(self, ctx):
        import urllib.error
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("timeout")):
            r = await web_fetch_handler(ctx, url="https://bad.example.com")
            assert not r.success
            assert "Erreur" in r.output

    @pytest.mark.asyncio
    async def test_truncation(self, ctx):
        big_text = "word " * 1000  # >2000 chars
        html = f"<html><body><p>{big_text}</p></body></html>"
        mock_response = MagicMock()
        mock_response.read.return_value = html.encode("utf-8")
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=False)

        with patch("urllib.request.urlopen", return_value=mock_response):
            r = await web_fetch_handler(ctx, url="https://example.com/big")
            assert r.success
            assert "..." in r.output


# ─── deep_research ─────────────────────────────────────────────────────────

class TestDeepResearch:
    @pytest.mark.asyncio
    async def test_success(self, ctx):
        mock_browser = MagicMock()
        mock_browser.deep_research = AsyncMock(return_value={
            "success": True,
            "pages_analyzed": 3,
            "tabs_opened": 3,
            "sources": [{"title": "Source One Long Title Here", "url": "https://src1.com"}],
            "synthesis": "Python est super.",
        })
        mock_module = MagicMock()
        mock_module.get_playwright_browser.return_value = mock_browser
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mock_module}):
            r = await deep_research_handler(ctx, query="python async", max_pages=3)
            assert r.success
            assert "Recherche approfondie" in r.output
            assert "Python est super" in r.output
            assert r.handler_name == "deep_research"

    @pytest.mark.asyncio
    async def test_failure(self, ctx):
        mock_browser = MagicMock()
        mock_browser.deep_research = AsyncMock(return_value={"success": False, "error": "timeout"})
        mock_module = MagicMock()
        mock_module.get_playwright_browser.return_value = mock_browser
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mock_module}):
            r = await deep_research_handler(ctx, query="test")
            assert not r.success
            assert "timeout" in r.output

    @pytest.mark.asyncio
    async def test_import_error(self, ctx):
        with patch.dict(sys.modules, {"src.tools.playwright_browser": None}):
            r = await deep_research_handler(ctx, query="test")
            assert not r.success


# ─── web_crawl_campaign ────────────────────────────────────────────────────

class TestWebCrawlCampaign:
    @pytest.mark.asyncio
    async def test_success(self, ctx):
        mock_crawler = AsyncMock()
        mock_crawler.crawl_campaign.return_value = {
            "success": True,
            "campaign_id": "camp_001",
            "run_id": "run_1",
            "run_visited": 50,
            "run_interesting": 12,
            "run_errors": 2,
            "pages_crawled_total": 50,
            "max_total_pages": 5000,
            "done": False,
            "next": "continue",
        }
        ctx._web_crawler = mock_crawler
        r = await web_crawl_campaign_handler(ctx, start_url="https://example.com")
        assert r.success
        assert "camp_001" in r.output
        assert "50" in r.output

    @pytest.mark.asyncio
    async def test_failure(self, ctx):
        mock_crawler = AsyncMock()
        mock_crawler.crawl_campaign.return_value = {"success": False, "error": "banned"}
        ctx._web_crawler = mock_crawler
        r = await web_crawl_campaign_handler(ctx, start_url="https://blocked.com")
        assert not r.success
        assert "banned" in r.output


# ─── web_crawl_campaign_status ─────────────────────────────────────────────

class TestWebCrawlCampaignStatus:
    @pytest.mark.asyncio
    async def test_success(self, ctx):
        mock_crawler = MagicMock()
        mock_crawler.campaign_status.return_value = {
            "success": True,
            "campaign_id": "camp_001",
            "seed_url": "https://example.com",
            "runs": 3,
            "pages_crawled_total": 150,
            "max_total_pages": 5000,
            "interesting_total": 30,
            "errors_total": 5,
            "queue_remaining": 200,
            "done": False,
        }
        ctx._web_crawler = mock_crawler
        r = await web_crawl_campaign_status_handler(ctx, campaign_id="camp_001")
        assert r.success
        assert "camp_001" in r.output
        assert "150" in r.output


# ─── web_crawl_campaign_explain ────────────────────────────────────────────

class TestWebCrawlCampaignExplain:
    @pytest.mark.asyncio
    async def test_success(self, ctx):
        mock_crawler = MagicMock()
        mock_crawler.campaign_explain_page.return_value = {
            "success": True,
            "explanation": {
                "page_url": "https://example.com/product",
                "title": "Product Page",
                "what_page_offers": "A great product",
                "target_audience": "developers",
                "where_or_contact": {
                    "location": "Paris",
                    "emails": ["info@example.com"],
                    "phones": ["+33123"],
                },
                "pricing_signals": ["29.99€"],
                "cta_signals": ["Buy now"],
            },
        }
        ctx._web_crawler = mock_crawler
        r = await web_crawl_campaign_explain_handler(ctx, campaign_id="camp_001")
        assert r.success
        assert "Product Page" in r.output
        assert "Paris" in r.output
        assert "29.99" in r.output


# ─── handler_defs ──────────────────────────────────────────────────────────

class TestHandlerDefs:
    def test_count(self):
        defs = get_web_handler_defs()
        assert len(defs) == 10

    def test_names(self):
        defs = get_web_handler_defs()
        names = {d.name for d in defs}
        expected = {
            "web_search",
            "web_search_brave",
            "web_fetch",
            "deep_research",
            "web_crawl_campaign",
            "web_crawl_campaign_status",
            "web_crawl_campaign_pro_report",
            "web_crawl_campaign_explain",
            "web_crawl",
            "web_crawl_campaign_export",
        }
        assert names == expected

    def test_all_have_category(self):
        for d in get_web_handler_defs():
            assert d.category == "web"

    def test_all_callable(self):
        for d in get_web_handler_defs():
            assert callable(d.handler)
