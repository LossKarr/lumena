"""
Tests unitaires pour le Smart Tab Manager (Phase 2.3).

Teste les méthodes de PlaywrightBrowser:
- _enforce_max_tabs()
- tab_find(query)
- switch_tab_by_query(query)
- MAX_TABS constant

Utilise des mocks pour éviter de lancer un vrai browser Chromium.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock, PropertyMock, patch

import pytest


def _run(coro):
    """Utilise le loop de session (set par conftest.py event_loop autouse).
    Si appelé avant la fixture (rare), crée un loop et le réutilise.
    """
    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            raise RuntimeError("loop fermé")
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


# ─── Helper pour créer un PlaywrightBrowser avec mock context ──────────────


def _make_browser_with_tabs(tab_infos: list[dict]) -> "PlaywrightBrowser":  # noqa: F821
    """Crée un PlaywrightBrowser avec des onglets mockés.

    Args:
        tab_infos: Liste de dicts {'title': str, 'url': str}.
    """
    from src.tools.playwright_browser import PlaywrightBrowser

    browser = PlaywrightBrowser.__new__(PlaywrightBrowser)
    browser.headless = True
    browser.profile_name = "test"
    browser._playwright = MagicMock()
    browser._browser = MagicMock()
    browser._browser.is_connected.return_value = True
    browser._context = MagicMock()
    browser._screenshots_dir = MagicMock()
    browser._profiles_dir = MagicMock()
    browser._session_start = None
    browser._pages_visited = 0
    browser._cookies_count = 0
    browser._active_tab_index = 0

    pages = []
    for info in tab_infos:
        page = MagicMock()
        page.url = info.get("url", "about:blank")
        page.title = AsyncMock(return_value=info.get("title", ""))
        page.close = AsyncMock()
        page.bring_to_front = AsyncMock()
        pages.append(page)

    browser._context.pages = pages
    browser._page = pages[0] if pages else None

    return browser


# ─── Tests MAX_TABS ──────────────────────────────────────────────────────


class TestMaxTabsConstant:
    def test_max_tabs_default_or_env(self):
        from src.tools.playwright_browser import MAX_TABS
        import os
        expected = int(os.getenv("LUMENA_BROWSER_MAX_TABS", "10"))
        assert MAX_TABS == expected

    def test_max_tabs_positive(self):
        from src.tools.playwright_browser import MAX_TABS
        assert MAX_TABS > 0


# ─── Tests _enforce_max_tabs ─────────────────────────────────────────────


class TestEnforceMaxTabs:
    def test_no_close_when_under_limit(self):
        """Si on a 3 onglets (< MAX_TABS), aucun ne doit être fermé."""
        browser = _make_browser_with_tabs([
            {"title": "Tab1", "url": "https://t1.com"},
            {"title": "Tab2", "url": "https://t2.com"},
            {"title": "Tab3", "url": "https://t3.com"},
        ])
        closed = _run(browser._enforce_max_tabs())
        assert closed == 0

    def test_close_oldest_when_at_limit(self):
        """Si on a MAX_TABS onglets, le plus ancien (index 1) doit être fermé."""
        from src.tools.playwright_browser import MAX_TABS

        tabs = [{"title": f"Tab{i}", "url": f"https://t{i}.com"} for i in range(MAX_TABS)]
        browser = _make_browser_with_tabs(tabs)
        # Simuler que close retire bien la page de la liste
        pages_list = list(browser._context.pages)
        original_close = pages_list[1].close

        async def close_and_remove():
            await original_close()
            browser._context.pages.remove(pages_list[1])

        pages_list[1].close = AsyncMock(side_effect=close_and_remove)

        closed = _run(browser._enforce_max_tabs())
        assert closed == 1
        pages_list[1].close.assert_called_once()

    def test_never_closes_first_tab(self):
        """L'onglet 0 (principal) ne doit jamais être fermé."""
        from src.tools.playwright_browser import MAX_TABS

        tabs = [{"title": f"Tab{i}", "url": f"https://t{i}.com"} for i in range(MAX_TABS)]
        browser = _make_browser_with_tabs(tabs)
        pages = list(browser._context.pages)

        async def simulate_close(victim_page):
            _orig = victim_page.close
            async def do_close():
                await _orig()
                browser._context.pages.remove(victim_page)
            return do_close

        # Patch close to remove from pages
        for p in pages[1:]:
            _p = p
            async def make_closer():
                orig = _p.close
                async def closer():
                    await orig()
                    if _p in browser._context.pages:
                        browser._context.pages.remove(_p)
                return closer
            p.close = AsyncMock(side_effect=lambda _p=_p: (
                browser._context.pages.remove(_p) if _p in browser._context.pages else None
            ))

        _run(browser._enforce_max_tabs())
        # Tab 0 must not have been closed
        pages[0].close.assert_not_called()

    def test_does_not_close_active_tab(self):
        """L'onglet actif ne doit pas être fermé même si c'est le plus ancien non-0."""
        from src.tools.playwright_browser import MAX_TABS

        tabs = [{"title": f"Tab{i}", "url": f"https://t{i}.com"} for i in range(MAX_TABS)]
        browser = _make_browser_with_tabs(tabs)
        browser._active_tab_index = 1  # L'onglet 1 est actif

        pages = list(browser._context.pages)
        # Patch close pour index 2 (premier candidat après 0 et actif 1)
        pages[2].close = AsyncMock(side_effect=lambda: browser._context.pages.remove(pages[2]))

        _run(browser._enforce_max_tabs())
        # Tab 1 (actif) ne doit pas avoir été fermé
        pages[1].close.assert_not_called()


# ─── Tests tab_find ──────────────────────────────────────────────────────


class TestTabFind:
    def test_find_by_title(self):
        browser = _make_browser_with_tabs([
            {"title": "Google", "url": "https://google.com"},
            {"title": "GitHub", "url": "https://github.com"},
            {"title": "Google Maps", "url": "https://maps.google.com"},
        ])
        result = _run(browser.tab_find("google"))
        assert result["success"]
        assert result["count"] == 2
        assert result["matches"][0]["title"] == "Google"
        assert result["matches"][1]["title"] == "Google Maps"

    def test_find_by_url(self):
        browser = _make_browser_with_tabs([
            {"title": "Page 1", "url": "https://example.com/abc"},
            {"title": "Page 2", "url": "https://test.com/xyz"},
        ])
        result = _run(browser.tab_find("test.com"))
        assert result["count"] == 1
        assert result["matches"][0]["index"] == 1

    def test_find_case_insensitive(self):
        browser = _make_browser_with_tabs([
            {"title": "My GitHub Page", "url": "https://github.com/user"},
        ])
        result = _run(browser.tab_find("GITHUB"))
        assert result["count"] == 1

    def test_find_no_matches(self):
        browser = _make_browser_with_tabs([
            {"title": "Google", "url": "https://google.com"},
        ])
        result = _run(browser.tab_find("youtube"))
        assert result["success"]
        assert result["count"] == 0
        assert result["matches"] == []

    def test_find_empty_tabs(self):
        browser = _make_browser_with_tabs([])
        result = _run(browser.tab_find("anything"))
        assert result["count"] == 0

    def test_find_marks_active(self):
        browser = _make_browser_with_tabs([
            {"title": "Tab 1", "url": "https://t1.com"},
            {"title": "Tab 2", "url": "https://t2.com"},
        ])
        browser._active_tab_index = 1
        result = _run(browser.tab_find("tab"))
        assert result["count"] == 2
        assert result["matches"][0]["active"] is False  # index 0
        assert result["matches"][1]["active"] is True   # index 1


# ─── Tests switch_tab_by_query ───────────────────────────────────────────


class TestSwitchTabByQuery:
    def test_switch_to_matching_tab(self):
        browser = _make_browser_with_tabs([
            {"title": "Home", "url": "https://home.com"},
            {"title": "GitHub", "url": "https://github.com"},
            {"title": "Docs", "url": "https://docs.com"},
        ])
        result = _run(browser.switch_tab_by_query("github"))
        assert result["success"]
        assert result["active_tab"] == 1
        assert browser._active_tab_index == 1

    def test_switch_no_match(self):
        browser = _make_browser_with_tabs([
            {"title": "Home", "url": "https://home.com"},
        ])
        result = _run(browser.switch_tab_by_query("youtube"))
        assert not result["success"]
        assert "Aucun" in result["error"]

    def test_switch_takes_first_match(self):
        browser = _make_browser_with_tabs([
            {"title": "Page A", "url": "https://example.com/a"},
            {"title": "Page B test", "url": "https://example.com/b"},
            {"title": "Page C test", "url": "https://example.com/c"},
        ])
        result = _run(browser.switch_tab_by_query("test"))
        assert result["success"]
        assert result["active_tab"] == 1  # Premier match = index 1

    def test_switch_by_url(self):
        browser = _make_browser_with_tabs([
            {"title": "Home", "url": "https://home.com"},
            {"title": "Search", "url": "https://google.com/search?q=test"},
        ])
        result = _run(browser.switch_tab_by_query("google.com"))
        assert result["success"]
        assert result["active_tab"] == 1
