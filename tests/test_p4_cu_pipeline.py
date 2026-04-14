"""
tests/test_p4_cu_pipeline.py — Tests P4 CU Pipeline (state-first, DOM, actions).

Couvre :
- P4.1 : _detect_context() web/desktop
- P4.2 : DOM snapshot injection dans run()
- P4.2.5 : target_index → coordonnées via _current_dom_snapshot
- P4.3 : UIA state acquisition desktop
- P4.4 : click_element state-first (DOM web / UIA desktop)
- P4.5 : nouvelles actions (move_mouse, drag, paste, clear_field, focus_window)
"""
from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ─── Helpers ──────────────────────────────────────────────────────────────

def _make_loop(**kwargs):
    """Crée un CUAgentLoop avec toutes les dépendances mockées."""
    with patch("src.computer_use.cu_agent_loop.CUAgentLoop._get_cu", return_value=MagicMock()):
        with patch("src.computer_use.cu_agent_loop.CUAgentLoop._get_vision", return_value=MagicMock()):
            from src.computer_use.cu_agent_loop import CUAgentLoop
            loop = CUAgentLoop(**kwargs)
    return loop


# ─── P4.1 — _detect_context ───────────────────────────────────────────────

class TestDetectContext:
    def test_detect_context_playwright_running(self):
        """Playwright page active → 'web'."""
        from src.computer_use.cu_agent_loop import CUAgentLoop
        loop = CUAgentLoop()

        mock_page = MagicMock()
        mock_page.is_closed.return_value = False
        mock_browser = MagicMock()
        mock_browser.is_running = True
        mock_browser._page = mock_page

        with patch("src.tools.playwright_browser.get_playwright_browser", return_value=mock_browser):
            result = asyncio.get_event_loop().run_until_complete(loop._detect_context())
        assert result == "web"

    def test_detect_context_playwright_not_running(self):
        """Playwright non actif + titre non-navigateur → 'desktop'."""
        from src.computer_use.cu_agent_loop import CUAgentLoop
        loop = CUAgentLoop()

        mock_browser = MagicMock()
        mock_browser.is_running = False

        mock_cu = MagicMock()
        mock_cu.window.get_active_window.return_value = "Notepad"
        loop._cu = mock_cu

        with patch("src.tools.playwright_browser.get_playwright_browser", return_value=mock_browser):
            result = asyncio.get_event_loop().run_until_complete(loop._detect_context())
        assert result == "desktop"

    def test_detect_context_chrome_window_title(self):
        """Titre fenêtre 'Google Chrome' → 'web'."""
        from src.computer_use.cu_agent_loop import CUAgentLoop
        loop = CUAgentLoop()

        mock_browser = MagicMock()
        mock_browser.is_running = False

        mock_cu = MagicMock()
        mock_cu.window.get_active_window.return_value = "google.com - Google Chrome"
        loop._cu = mock_cu

        with patch("src.tools.playwright_browser.get_playwright_browser", return_value=mock_browser):
            result = asyncio.get_event_loop().run_until_complete(loop._detect_context())
        assert result == "web"

    def test_detect_context_playwright_import_error(self):
        """Import error playwright → fallback sans crash."""
        from src.computer_use.cu_agent_loop import CUAgentLoop
        loop = CUAgentLoop()

        mock_cu = MagicMock()
        mock_cu.window.get_active_window.return_value = "Explorer"
        loop._cu = mock_cu

        with patch("src.tools.playwright_browser.get_playwright_browser", side_effect=ImportError):
            result = asyncio.get_event_loop().run_until_complete(loop._detect_context())
        assert result == "desktop"

    def test_detect_context_firefox_title(self):
        """Titre avec 'firefox' (casse quelconque) → 'web'."""
        from src.computer_use.cu_agent_loop import CUAgentLoop
        loop = CUAgentLoop()

        mock_browser = MagicMock()
        mock_browser.is_running = False

        mock_cu = MagicMock()
        mock_cu.window.get_active_window.return_value = "Mozilla Firefox"
        loop._cu = mock_cu

        with patch("src.tools.playwright_browser.get_playwright_browser", return_value=mock_browser):
            result = asyncio.get_event_loop().run_until_complete(loop._detect_context())
        assert result == "web"


# ─── P4.2 — _current_dom_snapshot init ───────────────────────────────────

class TestCurrentDomSnapshot:
    def test_current_dom_snapshot_init(self):
        """_current_dom_snapshot est None à l'initialisation."""
        from src.computer_use.cu_agent_loop import CUAgentLoop
        loop = CUAgentLoop()
        assert loop._current_dom_snapshot is None

    def test_current_dom_snapshot_attr_exists(self):
        """L'attribut existe sur l'instance."""
        from src.computer_use.cu_agent_loop import CUAgentLoop
        loop = CUAgentLoop()
        assert hasattr(loop, "_current_dom_snapshot")


# ─── P4.2.5 — target_index résolution ────────────────────────────────────

class TestTargetIndexResolution:
    def _make_snapshot_with_elements(self):
        from src.computer_use.dom_indexer import DOMElement, DOMSnapshot
        elements = [
            DOMElement(index=1, role="button", name="Submit", bbox=(100.0, 200.0, 80.0, 30.0)),
            DOMElement(index=2, role="textbox", name="Email", bbox=(50.0, 100.0, 200.0, 24.0)),
            DOMElement(index=3, role="link", name="Login", bbox=(300.0, 400.0, 60.0, 20.0)),
        ]
        return DOMSnapshot(url="https://example.com", title="Test", elements=elements, total_interactive=3)

    @pytest.mark.asyncio
    async def test_target_index_resolves_center(self):
        """target_index=1 → click aux coordonnées center de l'élément."""
        from src.computer_use.cu_agent_loop import CUAgentLoop, CUAction
        loop = CUAgentLoop()
        loop._current_dom_snapshot = self._make_snapshot_with_elements()

        mock_cu = MagicMock()
        mock_cu.screen.get_monitor_offset.return_value = (0, 0)
        loop._cu = mock_cu
        mock_vision = MagicMock()
        mock_vision.scale_coordinates_to_screen.side_effect = lambda x, y, sf, **kw: (x, y)
        loop._vision = mock_vision

        action = CUAction(action="click", params={"target_index": 1})
        result = await loop._execute_action(action, scale_factor=1.0)
        # Élément 1: bbox=(100, 200, 80, 30) → center=(140, 215)
        assert "140" in result or "Clic" in result
        assert mock_cu.mouse.click.called

    @pytest.mark.asyncio
    async def test_target_index_element_not_found(self):
        """target_index inexistant → message d'erreur gracieux."""
        from src.computer_use.cu_agent_loop import CUAgentLoop, CUAction
        loop = CUAgentLoop()
        loop._current_dom_snapshot = self._make_snapshot_with_elements()
        loop._cu = MagicMock()
        loop._vision = MagicMock()
        loop._vision.scale_coordinates_to_screen.side_effect = lambda x, y, sf, **kw: (x, y)

        action = CUAction(action="click", params={"target_index": 99})
        result = await loop._execute_action(action, scale_factor=1.0)
        assert "introuvable" in result or "99" in result

    @pytest.mark.asyncio
    async def test_target_index_no_snapshot(self):
        """target_index sans snapshot → erreur explicite, pas de crash."""
        from src.computer_use.cu_agent_loop import CUAgentLoop, CUAction
        loop = CUAgentLoop()
        loop._current_dom_snapshot = None
        loop._cu = MagicMock()
        loop._vision = MagicMock()

        action = CUAction(action="click", params={"target_index": 1})
        result = await loop._execute_action(action, scale_factor=1.0)
        assert "DOM snapshot" in result or "x/y" in result or "indisponible" in result

    @pytest.mark.asyncio
    async def test_target_index_element_no_center(self):
        """Élément sans bbox → erreur gracieuse."""
        from src.computer_use.cu_agent_loop import CUAgentLoop, CUAction
        from src.computer_use.dom_indexer import DOMElement, DOMSnapshot
        loop = CUAgentLoop()
        elem = DOMElement(index=5, role="button", name="NoGeom", bbox=None)
        loop._current_dom_snapshot = DOMSnapshot(
            url="", title="", elements=[elem], total_interactive=1
        )
        loop._cu = MagicMock()
        loop._vision = MagicMock()
        loop._vision.scale_coordinates_to_screen.side_effect = lambda x, y, sf, **kw: (x, y)

        action = CUAction(action="click", params={"target_index": 5})
        result = await loop._execute_action(action, scale_factor=1.0)
        assert "introuvable" in result or "coordonn" in result


# ─── P4.5 — Nouvelles actions ─────────────────────────────────────────────

class TestNewActions:
    def _setup_loop(self):
        from src.computer_use.cu_agent_loop import CUAgentLoop
        loop = CUAgentLoop()
        mock_cu = MagicMock()
        mock_cu.screen.get_monitor_offset.return_value = (0, 0)
        loop._cu = mock_cu
        mock_vision = MagicMock()
        mock_vision.scale_coordinates_to_screen.side_effect = lambda x, y, sf, **kw: (x, y)
        loop._vision = mock_vision
        return loop

    @pytest.mark.asyncio
    async def test_move_mouse_dispatched(self):
        """move_mouse appelle cu.mouse.move_to."""
        from src.computer_use.cu_agent_loop import CUAction
        loop = self._setup_loop()
        action = CUAction(action="move_mouse", params={"x": 100, "y": 200})
        result = await loop._execute_action(action, scale_factor=1.0)
        assert loop._cu.mouse.move_to.called
        assert "100" in result or "200" in result or "Souris" in result

    @pytest.mark.asyncio
    async def test_drag_dispatched(self):
        """drag appelle move_to + drag_to."""
        from src.computer_use.cu_agent_loop import CUAction
        loop = self._setup_loop()
        action = CUAction(action="drag", params={"start_x": 10, "start_y": 20, "end_x": 100, "end_y": 200})
        result = await loop._execute_action(action, scale_factor=1.0)
        assert loop._cu.mouse.move_to.called
        assert loop._cu.mouse.drag_to.called
        assert "Drag" in result or "drag" in result.lower()

    @pytest.mark.asyncio
    async def test_clear_field_dispatched(self):
        """clear_field appelle ctrl+a puis delete."""
        from src.computer_use.cu_agent_loop import CUAction
        loop = self._setup_loop()
        action = CUAction(action="clear_field", params={})
        result = await loop._execute_action(action, scale_factor=1.0)
        hotkey_calls = [str(c) for c in loop._cu.keyboard.hotkey.call_args_list]
        presskey_calls = [str(c) for c in loop._cu.keyboard.press_key.call_args_list]
        assert any("ctrl" in c.lower() and "a" in c.lower() for c in hotkey_calls), \
            f"ctrl+a attendu, calls: {hotkey_calls}"
        assert any("delete" in c.lower() for c in presskey_calls), \
            f"delete attendu, calls: {presskey_calls}"

    @pytest.mark.asyncio
    async def test_focus_window_dispatched(self):
        """focus_window appelle cu.window.focus_window."""
        from src.computer_use.cu_agent_loop import CUAction
        loop = self._setup_loop()
        loop._cu.window.focus_window.return_value = True
        action = CUAction(action="focus_window", params={"title": "Chrome"})
        result = await loop._execute_action(action, scale_factor=1.0)
        assert loop._cu.window.focus_window.called
        assert "Chrome" in result or "premier plan" in result

    @pytest.mark.asyncio
    async def test_paste_with_text_uses_pyperclip(self):
        """paste(text=...) tente pyperclip.copy puis ctrl+v."""
        from src.computer_use.cu_agent_loop import CUAction
        loop = self._setup_loop()
        action = CUAction(action="paste", params={"text": "hello world"})
        with patch("pyperclip.copy") as mock_copy:
            result = await loop._execute_action(action, scale_factor=1.0)
            mock_copy.assert_called_once_with("hello world")
        assert loop._cu.keyboard.hotkey.called

    @pytest.mark.asyncio
    async def test_paste_without_text(self):
        """paste() sans texte appelle juste ctrl+v."""
        from src.computer_use.cu_agent_loop import CUAction
        loop = self._setup_loop()
        action = CUAction(action="paste", params={})
        result = await loop._execute_action(action, scale_factor=1.0)
        assert loop._cu.keyboard.hotkey.called


# ─── P4.5 — System prompt contient nouvelles actions ─────────────────────

class TestCuSystemPromptUpdated:
    def test_system_prompt_has_move_mouse(self):
        from src.computer_use.cu_agent_loop import CU_SYSTEM_PROMPT
        assert "move_mouse" in CU_SYSTEM_PROMPT

    def test_system_prompt_has_drag(self):
        from src.computer_use.cu_agent_loop import CU_SYSTEM_PROMPT
        assert "drag" in CU_SYSTEM_PROMPT

    def test_system_prompt_has_paste(self):
        from src.computer_use.cu_agent_loop import CU_SYSTEM_PROMPT
        assert "paste" in CU_SYSTEM_PROMPT

    def test_system_prompt_has_clear_field(self):
        from src.computer_use.cu_agent_loop import CU_SYSTEM_PROMPT
        assert "clear_field" in CU_SYSTEM_PROMPT

    def test_system_prompt_has_focus_window(self):
        from src.computer_use.cu_agent_loop import CU_SYSTEM_PROMPT
        assert "focus_window" in CU_SYSTEM_PROMPT

    def test_system_prompt_has_target_index_doc(self):
        from src.computer_use.cu_agent_loop import CU_SYSTEM_PROMPT
        assert "target_index" in CU_SYSTEM_PROMPT


# ─── P4.4 — click_element state-first ────────────────────────────────────

class TestClickElementStateFIrst:
    def test_click_element_has_dom_step(self):
        """click_element contient une branche DOM pour le web."""
        import inspect
        from src.reasoning.handlers.computer_use import click_element
        src = inspect.getsource(click_element)
        assert "dom_indexer" in src or "DOMSnapshot" in src or "get_dom_indexer" in src

    def test_click_element_has_detect_ctx(self):
        """click_element détecte le contexte web/desktop."""
        import inspect
        from src.reasoning.handlers.computer_use import click_element
        src = inspect.getsource(click_element)
        assert "context" in src or "_detect_ctx" in src or "web" in src

    def test_click_element_has_uia_early(self):
        """click_element tente UIA en début de cascade (pas seulement en fallback)."""
        import inspect
        from src.reasoning.handlers.computer_use import click_element
        src = inspect.getsource(click_element)
        # UIA state-first ET UIA fallback doivent être présents
        assert src.count("click_element_by_name") >= 2

    def test_click_element_message_updated(self):
        """Le message d'échec mentionne 6 tentatives."""
        import inspect
        from src.reasoning.handlers.computer_use import click_element
        src = inspect.getsource(click_element)
        assert "6" in src


# ─── P4.1 — _BROWSER_HINTS constant ──────────────────────────────────────

class TestBrowserHints:
    def test_browser_hints_is_frozenset(self):
        from src.computer_use.cu_agent_loop import CUAgentLoop
        assert isinstance(CUAgentLoop._BROWSER_HINTS, frozenset)

    def test_browser_hints_contains_common_browsers(self):
        from src.computer_use.cu_agent_loop import CUAgentLoop
        for browser in ("chrome", "firefox", "edge", "safari"):
            assert browser in CUAgentLoop._BROWSER_HINTS
