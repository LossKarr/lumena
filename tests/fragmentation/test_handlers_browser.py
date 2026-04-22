"""
Tests unitaires pour src/reasoning/handlers/browser.py (35 handlers Playwright).

Pattern: patch.dict(sys.modules) pour bloquer les imports réels de
src.tools.playwright_browser et éviter d'ouvrir de vraies pages Chromium.

Phase 2.1+2.2+2.3+2.4+2.5: Migration Selenium → Playwright + DOM Index +
Smart Tab Manager + Handlers évolués + PDF/Upload/Network.
"""

import asyncio
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.reasoning.handlers.browser import (
    browser_accept_cookies,
    browser_back,
    browser_block_resources,
    browser_click,
    browser_click_at,
    browser_click_index,
    browser_close_all_tabs,
    browser_close_tab,
    browser_deep_research,
    browser_dom_state,
    browser_evaluate,
    browser_forward,
    browser_get_content,
    browser_hover,
    browser_keyboard_press,
    browser_navigate,
    browser_new_tab,
    browser_page_info,
    browser_refresh,
    browser_save_pdf,
    browser_screenshot,
    browser_scroll,
    browser_search_google,
    browser_select,
    browser_start,
    browser_stop,
    browser_switch_tab,
    browser_tab_find,
    browser_tab_switch,
    browser_tabs,
    browser_type,
    browser_type_index,
    browser_unblock_resources,
    browser_upload_file,
    browser_wait_for,
    get_browser_handler_defs,
)
from src.reasoning.handlers.context import HandlerContext


# ─── Helpers ───────────────────────────────────────────────────────────────

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


def _make_ctx() -> HandlerContext:
    return HandlerContext.for_testing()


def _make_browser_module() -> tuple:
    """Crée un module mock pour src.tools.playwright_browser + instance browser mock."""
    mock_browser = MagicMock()
    mock_module = ModuleType("src.tools.playwright_browser")
    mock_module.get_playwright_browser = MagicMock(return_value=mock_browser)
    mock_module.close_playwright_browser = AsyncMock()
    return mock_module, mock_browser


# ─── browser_start ─────────────────────────────────────────────────────────

class TestBrowserStart:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.start = AsyncMock(return_value=True)
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_start(_make_ctx(), headless=True))
        assert r.success
        assert "demarr" in r.output.lower()

    def test_failure(self):
        mod, browser = _make_browser_module()
        browser.start = AsyncMock(return_value=False)
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_start(_make_ctx()))
        assert not r.success

    def test_import_error(self):
        with patch.dict(sys.modules, {"src.tools.playwright_browser": None}):
            r = _run(browser_start(_make_ctx()))
        assert not r.success


# ─── browser_stop ──────────────────────────────────────────────────────────

class TestBrowserStop:
    def test_success(self):
        mod, browser = _make_browser_module()
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_stop(_make_ctx()))
        assert r.success
        mod.close_playwright_browser.assert_awaited_once()

    def test_exception(self):
        mod, browser = _make_browser_module()
        mod.close_playwright_browser = AsyncMock(side_effect=RuntimeError("boom"))
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_stop(_make_ctx()))
        assert not r.success


# ─── browser_navigate ──────────────────────────────────────────────────────

class TestBrowserNavigate:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.navigate = AsyncMock(return_value={"success": True, "title": "Google", "url": "https://google.com"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_navigate(_make_ctx(), url="https://google.com"))
        assert r.success
        assert "Google" in r.output

    def test_failure(self):
        mod, browser = _make_browser_module()
        browser.navigate = AsyncMock(return_value={"success": False, "error": "timeout"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_navigate(_make_ctx(), url="https://bad.com"))
        assert not r.success


# ─── browser_search_google ─────────────────────────────────────────────────

class TestBrowserSearchGoogle:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.search_google = AsyncMock(return_value={
            "success": True,
            "results_count": 2,
            "results": [
                {"position": 1, "title": "T1", "url": "https://t1.com", "description": "Desc1 long enough"},
                {"position": 2, "title": "T2", "url": "https://t2.com", "description": "Desc2 long enough"},
            ],
        })
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_search_google(_make_ctx(), query="test"))
        assert r.success
        assert "T1" in r.output

    def test_fail(self):
        mod, browser = _make_browser_module()
        browser.search_google = AsyncMock(return_value={"success": False, "error": "no results"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_search_google(_make_ctx(), query="test"))
        assert not r.success


# ─── browser_get_content ───────────────────────────────────────────────────

class TestBrowserGetContent:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.get_page_content = AsyncMock(return_value={
            "success": True, "title": "Page", "content": "Hello world content"
        })
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_get_content(_make_ctx()))
        assert r.success
        assert "Hello world" in r.output

    def test_with_url(self):
        mod, browser = _make_browser_module()
        browser.get_page_content = AsyncMock(return_value={"success": True, "title": "X", "content": "Y"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_get_content(_make_ctx(), url="https://x.com"))
        assert r.success
        browser.get_page_content.assert_awaited_once_with("https://x.com")


# ─── browser_click ─────────────────────────────────────────────────────────

class TestBrowserClick:
    def test_by_selector(self):
        mod, browser = _make_browser_module()
        browser.click_element = AsyncMock(return_value={"success": True})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_click(_make_ctx(), selector="#btn", by="css"))
        assert r.success
        browser.click_element.assert_awaited_once_with("#btn", "css")

    def test_by_text_overrides_css(self):
        mod, browser = _make_browser_module()
        browser.click_element = AsyncMock(return_value={"success": True})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_click(_make_ctx(), text="Accept"))
        assert r.success
        browser.click_element.assert_awaited_once_with("Accept", "partial_text")

    def test_empty_selector_fails(self):
        mod, browser = _make_browser_module()
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_click(_make_ctx(), selector="", by="css"))
        assert not r.success


# ─── browser_accept_cookies ────────────────────────────────────────────────

class TestBrowserAcceptCookies:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.accept_cookies = AsyncMock(return_value={"success": True, "method": "button", "selector": "#accept"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_accept_cookies(_make_ctx()))
        assert r.success
        assert "Cookies" in r.output

    def test_fail(self):
        mod, browser = _make_browser_module()
        browser.accept_cookies = AsyncMock(return_value={"success": False, "error": "no banner"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_accept_cookies(_make_ctx()))
        assert not r.success


# ─── browser_click_at ──────────────────────────────────────────────────────

class TestBrowserClickAt:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.click_at = AsyncMock(return_value={
            "success": True,
            "clicked_at": {"x": 100, "y": 200},
            "viewport": {"w": 1920, "h": 1080},
        })
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_click_at(_make_ctx(), x=100, y=200))
        assert r.success
        assert "100" in r.output


# ─── browser_type ──────────────────────────────────────────────────────────

class TestBrowserType:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.type_in_field = AsyncMock(return_value={"success": True})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_type(_make_ctx(), selector="#input", text="hello"))
        assert r.success
        browser.type_in_field.assert_awaited_once_with("#input", "hello", "css")


# ─── browser_screenshot ───────────────────────────────────────────────────

class TestBrowserScreenshot:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.screenshot = AsyncMock(return_value={"success": True, "path": "/tmp/shot.png"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_screenshot(_make_ctx()))
        assert r.success
        assert "shot.png" in r.output


# ─── browser_scroll ───────────────────────────────────────────────────────

class TestBrowserScroll:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.scroll = AsyncMock(return_value={"success": True})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_scroll(_make_ctx(), direction="down", amount=300))
        assert r.success
        browser.scroll.assert_awaited_once_with("down", 300)


# ─── browser_tabs ─────────────────────────────────────────────────────────

class TestBrowserTabs:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.get_tabs = AsyncMock(return_value={
            "success": True,
            "count": 2,
            "tabs": [
                {"index": 0, "title": "Tab1", "active": True},
                {"index": 1, "title": "Tab2", "active": False},
            ],
        })
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_tabs(_make_ctx()))
        assert r.success
        assert "Tab1" in r.output


# ─── browser_new_tab ──────────────────────────────────────────────────────

class TestBrowserNewTab:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.new_tab = AsyncMock(return_value={"success": True, "url": "about:blank"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_new_tab(_make_ctx()))
        assert r.success

    def test_with_url(self):
        mod, browser = _make_browser_module()
        browser.new_tab = AsyncMock(return_value={"success": True, "url": "https://example.com"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_new_tab(_make_ctx(), url="https://example.com"))
        assert r.success


# ─── browser_back ─────────────────────────────────────────────────────────

class TestBrowserBack:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.go_back = AsyncMock(return_value={"success": True, "url": "https://prev.com"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_back(_make_ctx()))
        assert r.success


# ─── browser_refresh ──────────────────────────────────────────────────────

class TestBrowserRefresh:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.refresh = AsyncMock(return_value={"success": True, "url": "https://cur.com"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_refresh(_make_ctx()))
        assert r.success


# ─── browser_close_all_tabs ───────────────────────────────────────────────

class TestBrowserCloseAllTabs:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.close_all_tabs_except_main = AsyncMock(return_value={"success": True, "closed_tabs": 3})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_close_all_tabs(_make_ctx()))
        assert r.success
        assert "3" in r.output

    def test_fail(self):
        mod, browser = _make_browser_module()
        browser.close_all_tabs_except_main = AsyncMock(return_value={"success": False, "error": "no browser"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_close_all_tabs(_make_ctx()))
        assert not r.success


# ─── browser_switch_tab ───────────────────────────────────────────────────

class TestBrowserSwitchTab:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.switch_tab = AsyncMock(return_value={"success": True, "title": "Tab2", "url": "https://t2.com"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_switch_tab(_make_ctx(), index=1))
        assert r.success
        assert "Tab2" in r.output

    def test_fail(self):
        mod, browser = _make_browser_module()
        browser.switch_tab = AsyncMock(return_value={"success": False, "error": "index hors limites"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_switch_tab(_make_ctx(), index=99))
        assert not r.success


# ─── browser_close_tab ────────────────────────────────────────────────────

class TestBrowserCloseTab:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.close_tab = AsyncMock(return_value={"success": True})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_close_tab(_make_ctx()))
        assert r.success

    def test_fail(self):
        mod, browser = _make_browser_module()
        browser.close_tab = AsyncMock(return_value={"success": False, "error": "no tab"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_close_tab(_make_ctx()))
        assert not r.success


# ─── Phase 2.3 — Smart Tab Manager handlers ──────────────────────────────


class TestBrowserTabFind:
    def test_success_with_matches(self):
        mod, browser = _make_browser_module()
        browser.is_running = True
        browser.tab_find = AsyncMock(return_value={
            "success": True,
            "query": "google",
            "matches": [
                {"index": 0, "title": "Google", "url": "https://google.com", "active": True},
                {"index": 2, "title": "Google Maps", "url": "https://maps.google.com", "active": False},
            ],
            "count": 2,
        })
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_tab_find(_make_ctx(), query="google"))
        assert r.success
        assert "2 onglet" in r.output
        assert "Google" in r.output

    def test_no_matches(self):
        mod, browser = _make_browser_module()
        browser.is_running = True
        browser.tab_find = AsyncMock(return_value={
            "success": True, "query": "xyz", "matches": [], "count": 0,
        })
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_tab_find(_make_ctx(), query="xyz"))
        assert r.success
        assert "Aucun" in r.output

    def test_not_running(self):
        mod, browser = _make_browser_module()
        browser.is_running = False
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_tab_find(_make_ctx(), query="test"))
        assert not r.success


class TestBrowserTabSwitch:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.is_running = True
        browser.switch_tab_by_query = AsyncMock(return_value={
            "success": True, "active_tab": 1, "title": "GitHub", "url": "https://github.com",
        })
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_tab_switch(_make_ctx(), query="github"))
        assert r.success
        assert "GitHub" in r.output

    def test_no_match(self):
        mod, browser = _make_browser_module()
        browser.is_running = True
        browser.switch_tab_by_query = AsyncMock(return_value={
            "success": False, "error": "Aucun onglet ne correspond à: xyz",
        })
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_tab_switch(_make_ctx(), query="xyz"))
        assert not r.success

    def test_not_running(self):
        mod, browser = _make_browser_module()
        browser.is_running = False
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_tab_switch(_make_ctx(), query="test"))
        assert not r.success


# ─── Handler Defs ─────────────────────────────────────────────────────────


# ─── browser_dom_state ─────────────────────────────────────────────────

def _make_dom_modules():
    """Crée les modules mock pour playwright_browser + dom_indexer."""
    from src.computer_use.dom_indexer import DOMElement, DOMSnapshot, DOMIndexer

    mock_browser = MagicMock()
    mock_browser.is_running = True
    mock_browser._page = MagicMock()

    mock_pw_module = ModuleType("src.tools.playwright_browser")
    mock_pw_module.get_playwright_browser = MagicMock(return_value=mock_browser)

    mock_indexer = MagicMock(spec=DOMIndexer)
    snap = DOMSnapshot(
        url="https://test.com",
        title="Test",
        elements=[
            DOMElement(index=1, role="button", name="OK", bbox=(100, 100, 80, 30)),
            DOMElement(index=2, role="textbox", name="Email", bbox=(200, 200, 150, 30)),
            DOMElement(index=3, role="link", name="Home", bbox=(300, 50, 60, 20)),
        ],
        total_interactive=3,
    )
    mock_indexer.snapshot = AsyncMock(return_value=snap)
    mock_indexer.enrich_with_bboxes = AsyncMock(return_value=snap)

    mock_dom_module = ModuleType("src.computer_use.dom_indexer")
    mock_dom_module.get_dom_indexer = MagicMock(return_value=mock_indexer)
    mock_dom_module.render_set_of_mark = MagicMock(return_value=MagicMock())  # PIL Image mock

    return mock_pw_module, mock_dom_module, mock_browser, mock_indexer, snap


class TestBrowserDomState:
    def test_success(self):
        pw_mod, dom_mod, browser, indexer, snap = _make_dom_modules()
        with patch.dict(sys.modules, {
            "src.tools.playwright_browser": pw_mod,
            "src.computer_use.dom_indexer": dom_mod,
        }):
            r = _run(browser_dom_state(_make_ctx()))
        assert r.success
        assert '[1] button "OK"' in r.output
        assert '[2] textbox "Email"' in r.output

    def test_not_running(self):
        pw_mod, dom_mod, browser, indexer, snap = _make_dom_modules()
        browser.is_running = False
        with patch.dict(sys.modules, {
            "src.tools.playwright_browser": pw_mod,
            "src.computer_use.dom_indexer": dom_mod,
        }):
            r = _run(browser_dom_state(_make_ctx()))
        assert not r.success


# ─── browser_click_index ───────────────────────────────────────────────

class TestBrowserClickIndex:
    def test_success(self):
        pw_mod, dom_mod, browser, indexer, snap = _make_dom_modules()
        browser.click_at = AsyncMock(return_value={"success": True})
        with patch.dict(sys.modules, {
            "src.tools.playwright_browser": pw_mod,
            "src.computer_use.dom_indexer": dom_mod,
        }):
            r = _run(browser_click_index(_make_ctx(), index=1))
        assert r.success
        assert "OK" in r.output
        # Center of bbox (100, 100, 80, 30) = (140, 115)
        browser.click_at.assert_awaited_once_with(140, 115)

    def test_invalid_index(self):
        pw_mod, dom_mod, browser, indexer, snap = _make_dom_modules()
        with patch.dict(sys.modules, {
            "src.tools.playwright_browser": pw_mod,
            "src.computer_use.dom_indexer": dom_mod,
        }):
            r = _run(browser_click_index(_make_ctx(), index=99))
        assert not r.success
        assert "introuvable" in r.output

    def test_not_running(self):
        pw_mod, dom_mod, browser, indexer, snap = _make_dom_modules()
        browser.is_running = False
        with patch.dict(sys.modules, {
            "src.tools.playwright_browser": pw_mod,
            "src.computer_use.dom_indexer": dom_mod,
        }):
            r = _run(browser_click_index(_make_ctx(), index=1))
        assert not r.success


# ─── browser_type_index ────────────────────────────────────────────────

class TestBrowserTypeIndex:
    def test_success_textbox(self):
        pw_mod, dom_mod, browser, indexer, snap = _make_dom_modules()
        browser.click_at = AsyncMock(return_value={"success": True})
        browser._page = MagicMock()
        browser._page.keyboard = MagicMock()
        browser._page.keyboard.type = AsyncMock()
        with patch.dict(sys.modules, {
            "src.tools.playwright_browser": pw_mod,
            "src.computer_use.dom_indexer": dom_mod,
        }):
            r = _run(browser_type_index(_make_ctx(), index=2, text="hello@test.com"))
        assert r.success
        assert "hello@test.com" in r.output
        browser._page.keyboard.type.assert_awaited_once_with("hello@test.com", delay=30)

    def test_wrong_role_fails(self):
        pw_mod, dom_mod, browser, indexer, snap = _make_dom_modules()
        with patch.dict(sys.modules, {
            "src.tools.playwright_browser": pw_mod,
            "src.computer_use.dom_indexer": dom_mod,
        }):
            # index=1 is a button, not a textbox
            r = _run(browser_type_index(_make_ctx(), index=1, text="hello"))
        assert not r.success
        assert "champ de texte" in r.output

    def test_invalid_index(self):
        pw_mod, dom_mod, browser, indexer, snap = _make_dom_modules()
        with patch.dict(sys.modules, {
            "src.tools.playwright_browser": pw_mod,
            "src.computer_use.dom_indexer": dom_mod,
        }):
            r = _run(browser_type_index(_make_ctx(), index=99, text="hello"))
        assert not r.success
        assert "introuvable" in r.output


class TestBrowserHandlerDefs:
    def test_count(self):
        defs = get_browser_handler_defs()
        assert len(defs) == 66

    def test_names_unique(self):
        defs = get_browser_handler_defs()
        names = [d.name for d in defs]
        assert len(names) == len(set(names))

    def test_all_start_with_browser(self):
        defs = get_browser_handler_defs()
        for d in defs:
            assert d.name.startswith("browser_"), f"{d.name} ne commence pas par browser_"

    def test_all_have_handler(self):
        defs = get_browser_handler_defs()
        for d in defs:
            assert callable(d.handler), f"{d.name} handler non callable"


# ─── Phase 2.4 + 2.5 — Nouveaux handlers ──────────────────────────────────────

class TestBrowserEvaluate:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.evaluate = AsyncMock(return_value={"success": True, "result": 42})
        browser.is_running = True
        browser._page = MagicMock()
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_evaluate(_make_ctx(), script="1+1"))
        assert r.success
        assert "42" in r.output

    def test_failure(self):
        mod, browser = _make_browser_module()
        browser.evaluate = AsyncMock(return_value={"success": False, "error": "SyntaxError"})
        browser.is_running = True
        browser._page = MagicMock()
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_evaluate(_make_ctx(), script="{{invalid"))
        assert not r.success


class TestBrowserForward:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.go_forward = AsyncMock(return_value={"success": True, "url": "https://next.com"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_forward(_make_ctx()))
        assert r.success
        assert "next.com" in r.output

    def test_failure(self):
        mod, browser = _make_browser_module()
        browser.go_forward = AsyncMock(return_value={"success": False, "error": "no history"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_forward(_make_ctx()))
        assert not r.success


class TestBrowserWaitFor:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.wait_for_selector = AsyncMock(return_value={"success": True, "found": "#modal"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_wait_for(_make_ctx(), selector="#modal"))
        assert r.success
        assert "#modal" in r.output

    def test_timeout(self):
        mod, browser = _make_browser_module()
        browser.wait_for_selector = AsyncMock(return_value={"success": False, "error": "Timeout"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_wait_for(_make_ctx(), selector="#missing", timeout=1000))
        assert not r.success


class TestBrowserPageInfo:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.get_page_info = AsyncMock(return_value={"success": True, "title": "Accueil", "url": "https://x.com"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_page_info(_make_ctx()))
        assert r.success
        assert "Accueil" in r.output
        assert "https://x.com" in r.output

    def test_failure(self):
        mod, browser = _make_browser_module()
        browser.get_page_info = AsyncMock(return_value={"success": False, "error": "no page"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_page_info(_make_ctx()))
        assert not r.success


class TestBrowserDeepResearch:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.deep_research = AsyncMock(return_value={
            "success": True,
            "pages_analyzed": 3,
            "synthesis": "Résumé test",
            "sources": [{"title": "Source 1", "url": "https://s1.com"}],
        })
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_deep_research(_make_ctx(), query="test query"))
        assert r.success
        assert "Résumé test" in r.output
        assert "3 pages" in r.output

    def test_failure(self):
        mod, browser = _make_browser_module()
        browser.deep_research = AsyncMock(return_value={"success": False, "error": "network error"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_deep_research(_make_ctx(), query="test"))
        assert not r.success


class TestBrowserHover:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.hover = AsyncMock(return_value={"success": True, "hovered": "#menu"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_hover(_make_ctx(), selector="#menu"))
        assert r.success
        assert "#menu" in r.output

    def test_failure(self):
        mod, browser = _make_browser_module()
        browser.hover = AsyncMock(return_value={"success": False, "error": "not found"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_hover(_make_ctx(), selector="#ghost"))
        assert not r.success


class TestBrowserSelect:
    def test_by_value(self):
        mod, browser = _make_browser_module()
        browser.select_option = AsyncMock(return_value={"success": True, "selected": ["fr"]})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_select(_make_ctx(), selector="#lang", value="fr"))
        assert r.success
        assert "fr" in r.output

    def test_by_label(self):
        mod, browser = _make_browser_module()
        browser.select_option = AsyncMock(return_value={"success": True, "selected": ["Français"]})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_select(_make_ctx(), selector="#lang", label="Français"))
        assert r.success

    def test_failure(self):
        mod, browser = _make_browser_module()
        browser.select_option = AsyncMock(return_value={"success": False, "error": "not found"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_select(_make_ctx(), selector="#nope", value="x"))
        assert not r.success


class TestBrowserKeyboardPress:
    def test_enter(self):
        mod, browser = _make_browser_module()
        browser.keyboard_press = AsyncMock(return_value={"success": True, "key_pressed": "Enter"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_keyboard_press(_make_ctx(), key="Enter"))
        assert r.success
        assert "Enter" in r.output

    def test_failure(self):
        mod, browser = _make_browser_module()
        browser.keyboard_press = AsyncMock(return_value={"success": False, "error": "no page"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_keyboard_press(_make_ctx(), key="Enter"))
        assert not r.success


class TestBrowserSavePdf:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.save_pdf = AsyncMock(return_value={"success": True, "path": "data/screenshots/page.pdf"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_save_pdf(_make_ctx(), filename="page.pdf"))
        assert r.success
        assert "page.pdf" in r.output

    def test_headless_required(self):
        mod, browser = _make_browser_module()
        browser.save_pdf = AsyncMock(return_value={"success": False, "error": "L'export PDF nécessite headless=True"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_save_pdf(_make_ctx()))
        assert not r.success


class TestBrowserUploadFile:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.upload_file = AsyncMock(return_value={"success": True, "uploaded": ["/tmp/cv.pdf"]})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_upload_file(_make_ctx(), selector="input[type=file]", file_paths=["/tmp/cv.pdf"]))
        assert r.success
        assert "cv.pdf" in r.output

    def test_file_not_found(self):
        mod, browser = _make_browser_module()
        browser.upload_file = AsyncMock(return_value={"success": False, "error": "Aucun fichier trouvé"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_upload_file(_make_ctx(), selector="#f", file_paths=["/ghost.pdf"]))
        assert not r.success


class TestBrowserBlockResources:
    def test_default_block(self):
        mod, browser = _make_browser_module()
        browser.block_resources = AsyncMock(return_value={
            "success": True,
            "blocked_types": [],
            "blocked_url_patterns": ["doubleclick.net", "google-analytics.com"],
        })
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_block_resources(_make_ctx()))
        assert r.success
        assert "2" in r.output

    def test_with_types(self):
        mod, browser = _make_browser_module()
        browser.block_resources = AsyncMock(return_value={
            "success": True,
            "blocked_types": ["image", "font"],
            "blocked_url_patterns": [],
        })
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_block_resources(_make_ctx(), resource_types=["image", "font"]))
        assert r.success
        assert "image" in r.output


class TestBrowserUnblockResources:
    def test_success(self):
        mod, browser = _make_browser_module()
        browser.unblock_resources = AsyncMock(return_value={"success": True})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_unblock_resources(_make_ctx()))
        assert r.success

    def test_failure(self):
        mod, browser = _make_browser_module()
        browser.unblock_resources = AsyncMock(return_value={"success": False, "error": "not init"})
        with patch.dict(sys.modules, {"src.tools.playwright_browser": mod}):
            r = _run(browser_unblock_resources(_make_ctx()))
        assert not r.success

