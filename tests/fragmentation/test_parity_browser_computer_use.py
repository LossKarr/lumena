"""
Tests de parité pour browser.py (23 handlers) et computer_use.py (26 handlers).

Vérifie que chaque handler fragmenté a une définition dans les HandlerDefs
et que les noms/descriptions correspondent exactement aux enregistrements
de react.py (sections _register_browser_tools et _register_computer_use_tools).
"""

import pytest

from src.reasoning.handlers.browser import get_browser_handler_defs
from src.reasoning.handlers.computer_use import get_computer_use_handler_defs
from src.reasoning.handlers.parity_tools import assert_parity, batch_parity_check


# ─── Noms attendus (extraits de react.py _register_browser_tools) ─────────

EXPECTED_BROWSER_NAMES = [
    "browser_start",
    "browser_stop",
    "browser_navigate",
    "browser_search_google",
    "browser_get_content",
    "browser_click",
    "browser_accept_cookies",
    "browser_click_at",
    "browser_type",
    "browser_screenshot",
    "browser_scroll",
    "browser_tabs",
    "browser_new_tab",
    "browser_back",
    "browser_refresh",
    "browser_close_all_tabs",
    "browser_switch_tab",
    "browser_close_tab",
    "browser_tab_find",
    "browser_tab_switch",
    "browser_dom_state",
    "browser_click_index",
    "browser_type_index",
    "browser_dismiss_popups",
    # Phase 2.4 — Handlers évolués Playwright 1.58+
    "browser_evaluate",
    "browser_forward",
    "browser_wait_for",
    "browser_page_info",
    "browser_deep_research",
    "browser_hover",
    "browser_select",
    "browser_select_index",
    "browser_keyboard_press",
    # Phase 2.5 — PDF, Upload, Network interception
    "browser_save_pdf",
    "browser_upload_file",
    "browser_block_resources",
    "browser_unblock_resources",
    # Phase 3 — Tracing, Network, Device, Cookies, Storage, Batch, Labels
    "browser_trace_start",
    "browser_trace_stop",
    "browser_network_requests",
    "browser_network_clear",
    "browser_emulate_device",
    "browser_set_geolocation",
    "browser_emulate_media",
    "browser_cookies_get",
    "browser_cookies_clear",
    "browser_storage_get",
    "browser_storage_set",
    "browser_storage_clear",
    "browser_batch",
    "browser_screenshot_labels",
    # Aliases legacy
    "browser_get_text",
    "browser_list_tabs",
    "browser_open_tab",
    # Phase 4 — Dialog, Drag, Download, Frames, Metrics, SmartClick
    "browser_handle_dialog",
    "browser_dialog_log",
    "browser_drag",
    "browser_drag_at",
    "browser_wait_for_download",
    "browser_list_downloads",
    "browser_verify",
    "browser_save_login",
    "browser_list_logins",
    "browser_login",
    "browser_find",
    "browser_check_challenge",
    "browser_solve_challenge",
    "browser_frames",
    "browser_frame_click",
    "browser_frame_type",
    "browser_frame_content",
    "browser_frame_evaluate",
    "browser_metrics",
    "browser_click_smart",
    # Fix D — Extraction complète des messages chat
    "browser_get_chat_messages",
    # Fix G — Recherche Google Maps structurée
    "browser_search_maps",
]

EXPECTED_COMPUTER_USE_NAMES = [
    "click",
    "type_text",
    "open_app",
    "close_app",
    "cursor_ide_local",
    "hotkey",
    "get_active_window",
    "double_click",
    "scroll",
    "move_mouse",
    "press_key",
    "close_window",
    "wait",
    "spotify_play",
    "open_url",
    "list_windows",
    "cu_readiness",
    "drag",
    "screenshot_analyze",
    "click_element",
    "find_element",
    "zoom",
    "computer_task",
    "list_screens",
    "set_screen",
    "ui_click",
    "ui_type",
    "ui_list_controls",
    "mouse_pattern",
]


# ─── Browser parity ───────────────────────────────────────────────────────

class TestBrowserParity:
    def test_count(self):
        defs = get_browser_handler_defs()
        assert len(defs) == 76  # +1 : browser_select_index (LOT Z19)

    def test_names_match(self):
        defs = get_browser_handler_defs()
        actual = [d.name for d in defs]
        assert actual == EXPECTED_BROWSER_NAMES

    def test_all_callable(self):
        defs = get_browser_handler_defs()
        for d in defs:
            assert callable(d.handler)

    def test_all_have_description(self):
        defs = get_browser_handler_defs()
        for d in defs:
            assert d.description, f"{d.name} manque une description"

    @pytest.mark.parametrize("name", EXPECTED_BROWSER_NAMES)
    def test_handler_exists(self, name):
        defs = get_browser_handler_defs()
        names = {d.name for d in defs}
        assert name in names, f"Handler manquant: {name}"


# ─── Computer Use parity ──────────────────────────────────────────────────

class TestComputerUseParity:
    def test_count(self):
        defs = get_computer_use_handler_defs()
        assert len(defs) == 29

    def test_names_match(self):
        defs = get_computer_use_handler_defs()
        actual = [d.name for d in defs]
        assert actual == EXPECTED_COMPUTER_USE_NAMES

    def test_all_callable(self):
        defs = get_computer_use_handler_defs()
        for d in defs:
            assert callable(d.handler)

    def test_all_have_description(self):
        defs = get_computer_use_handler_defs()
        for d in defs:
            assert d.description, f"{d.name} manque une description"

    @pytest.mark.parametrize("name", EXPECTED_COMPUTER_USE_NAMES)
    def test_handler_exists(self, name):
        defs = get_computer_use_handler_defs()
        names = {d.name for d in defs}
        assert name in names, f"Handler manquant: {name}"


# ─── Cross-module parity ──────────────────────────────────────────────────

class TestCrossModuleParity:
    def test_no_name_collision(self):
        """Aucun nom ne doit être dupliqué entre browser et computer_use."""
        b_names = {d.name for d in get_browser_handler_defs()}
        cu_names = {d.name for d in get_computer_use_handler_defs()}
        collision = b_names & cu_names
        assert not collision, f"Noms en collision: {collision}"

    def test_total_count(self):
        """browser(76) + computer_use(29) = 105 handlers."""
        total = len(get_browser_handler_defs()) + len(get_computer_use_handler_defs())
        assert total == 105
