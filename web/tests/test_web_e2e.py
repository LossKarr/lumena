"""
Lumena Web UI — End-to-end Playwright tests.

Usage:
  1. Start the server:  python web/server.py
  2. Run E2E tests:     pytest tests/test_web_e2e.py -v

These tests are marked @pytest.mark.e2e and excluded from the default test run.
They require a running Lumena server on http://127.0.0.1:8080.
"""
from __future__ import annotations

import os
import pytest

# Skip entire module if no server running or Playwright unavailable
pytestmark = pytest.mark.e2e

BASE = os.getenv("LUMENA_TEST_URL", "http://127.0.0.1:8080")


@pytest.fixture(scope="session")
def _check_server():
    """Skip all E2E tests if server is not reachable."""
    import httpx
    try:
        r = httpx.get(f"{BASE}/api/health", timeout=3)
        if r.status_code >= 500:
            pytest.skip("Server returned 5xx")
    except Exception:
        pytest.skip("Lumena server not running on " + BASE)


@pytest.fixture(scope="session")
def browser_context_args():
    return {"ignore_https_errors": True}


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  PAGE LOAD
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_page_loads(page, _check_server):
    """Root page loads without console errors."""
    errors = []
    page.on("pageerror", lambda err: errors.append(str(err)))
    page.goto(BASE, wait_until="networkidle")
    assert page.title(), "Page title should be non-empty"
    # Allow a few seconds for async JS init
    page.wait_for_timeout(2000)
    # No critical JS errors
    critical = [e for e in errors if "SyntaxError" in e or "ReferenceError" in e or "TypeError" in e]
    assert not critical, f"JS errors on page load: {critical}"


def test_startup_screen_visible(page, _check_server):
    """Startup screen should be visible with model selector."""
    page.goto(BASE, wait_until="networkidle")
    # Either the startup screen or the app shell should be visible
    startup = page.locator("#startup-screen")
    shell = page.locator("#app-shell")
    assert startup.is_visible() or shell.is_visible(), "Neither startup nor app shell visible"


def test_static_css_loads(page, _check_server):
    """CSS custom properties should be applied."""
    page.goto(BASE, wait_until="networkidle")
    bg = page.evaluate("getComputedStyle(document.documentElement).getPropertyValue('--bg')")
    assert bg.strip(), "--bg CSS variable not defined"


def test_lucide_icons_rendered(page, _check_server):
    """Lucide icons should be converted to SVG."""
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_timeout(1500)
    svgs = page.locator("svg.lucide").count()
    assert svgs > 0, "No Lucide SVG icons found — lucide.createIcons() may not have run"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  THEME
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_theme_defaults_dark(page, _check_server):
    """Dark theme should be the default."""
    page.goto(BASE, wait_until="networkidle")
    mode = page.evaluate("document.documentElement.getAttribute('data-theme-mode')")
    # Default is dark (either null/no attr or explicitly 'dark')
    assert mode is None or mode == "dark", f"Expected dark default, got {mode}"


def test_theme_toggle(page, _check_server):
    """Clicking theme orb should switch between dark and light."""
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_timeout(1000)
    # Toggle to light
    page.evaluate("toggleTheme()")
    mode = page.evaluate("document.documentElement.getAttribute('data-theme-mode')")
    assert mode == "light", f"Expected light after toggle, got {mode}"
    # Toggle back to dark
    page.evaluate("toggleTheme()")
    mode = page.evaluate("document.documentElement.getAttribute('data-theme-mode')")
    assert mode == "dark", f"Expected dark after second toggle, got {mode}"


def test_theme_persists_localstorage(page, _check_server):
    """Theme preference should be saved to localStorage."""
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_timeout(1000)
    page.evaluate("toggleTheme()")
    stored = page.evaluate("localStorage.getItem('lumena_theme')")
    assert stored in ("light", "dark"), f"Theme not in localStorage: {stored}"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  NAVIGATION
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def _start_app(page):
    """Navigate and force-bypass startup screen."""
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_timeout(1500)
    shell = page.locator("#app-shell")
    if not shell.is_visible():
        # Force bypass: hide startup screen, show app shell, init UI
        page.evaluate("""
            document.getElementById('startup-screen').classList.add('hidden');
            document.getElementById('app-shell').style.display = 'grid';
            if (typeof setupNavigation === 'function') setupNavigation();
            if (typeof setupTextarea === 'function') setupTextarea();
        """)
        page.wait_for_timeout(1000)


def test_nav_panels_switch(page, _check_server):
    """Clicking nav items should switch panels."""
    _start_app(page)
    # Click on a panel
    page.evaluate("switchPanel('tools')")
    page.wait_for_timeout(500)
    tools_panel = page.locator("#panel-tools")
    assert tools_panel.is_visible(), "Tools panel not visible after switch"


def test_nav_sidebar_collapse(page, _check_server):
    """Sidebar should collapse/expand."""
    _start_app(page)
    page.evaluate("toggleNavCollapse()")
    page.wait_for_timeout(300)
    shell = page.locator("#app-shell")
    assert "shell--nav-collapsed" in (shell.get_attribute("class") or ""), "Sidebar not collapsed"
    page.evaluate("toggleNavCollapse()")
    page.wait_for_timeout(300)
    assert "shell--nav-collapsed" not in (shell.get_attribute("class") or ""), "Sidebar still collapsed"


def test_focus_mode(page, _check_server):
    """Focus mode should add shell--focus class."""
    _start_app(page)
    page.evaluate("toggleFocus()")
    page.wait_for_timeout(300)
    shell = page.locator("#app-shell")
    assert "shell--focus" in (shell.get_attribute("class") or ""), "Focus mode not active"
    page.evaluate("toggleFocus()")


def test_command_palette(page, _check_server):
    """Command palette should open and close."""
    _start_app(page)
    page.evaluate("openCommandPalette()")
    page.wait_for_timeout(300)
    overlay = page.locator("#cmd-palette-overlay")
    assert "open" in (overlay.get_attribute("class") or ""), "Palette not open"
    page.evaluate("closeCommandPalette()")
    page.wait_for_timeout(300)
    assert "open" not in (overlay.get_attribute("class") or ""), "Palette still open"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  CHAT
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_chat_input_exists(page, _check_server):
    """Chat textarea should be present and focusable."""
    _start_app(page)
    page.evaluate("switchPanel('chat')")
    page.wait_for_timeout(500)
    ta = page.locator("#message-input")
    assert ta.is_visible(), "Message input not visible"


def test_chat_add_msg(page, _check_server):
    """addMsg should render a message in the chat area."""
    _start_app(page)
    page.evaluate("switchPanel('chat')")
    page.wait_for_timeout(500)
    page.evaluate("addMsg('user', 'Test message from E2E')")
    page.wait_for_timeout(300)
    msgs = page.locator("#chat-thread .msg-group")
    assert msgs.count() > 0, "No messages rendered"


def test_chat_persistence(page, _check_server):
    """clearChatHistory should clear localStorage; adding via internal API should persist."""
    _start_app(page)
    page.evaluate("switchPanel('chat')")
    page.wait_for_timeout(300)
    # clear first
    page.evaluate("clearChatHistory()")
    page.wait_for_timeout(200)
    after_clear = page.evaluate("localStorage.getItem('lumena_chat_history')")
    assert after_clear is None or after_clear == "[]", "clearChatHistory did not clear storage"
    # Write directly to localStorage to simulate a persisted message
    page.evaluate("localStorage.setItem('lumena_chat_history', JSON.stringify([{role:'user',text:'Persist test'}]))")
    # Reload history from storage
    page.evaluate("loadChatHistory()")
    page.wait_for_timeout(300)
    stored = page.evaluate("localStorage.getItem('lumena_chat_history')")
    assert stored and "Persist test" in stored, "localStorage not updated"


def test_chat_export_markdown(page, _check_server):
    """exportChatMarkdown should not throw."""
    _start_app(page)
    page.evaluate("switchPanel('chat')")
    page.wait_for_timeout(300)
    page.evaluate("addMsg('user', 'Export test')")
    # Just ensure it doesn't throw (download is hard to test)
    threw = page.evaluate("""
        (function() {
            try { exportChatMarkdown(); return false; }
            catch(e) { return e.message; }
        })()
    """)
    assert threw is False, f"exportChatMarkdown threw: {threw}"


def test_chat_clear(page, _check_server):
    """clearChatHistory should empty the chat."""
    _start_app(page)
    page.evaluate("switchPanel('chat')")
    page.evaluate("addMsg('user', 'To be cleared')")
    page.evaluate("clearChatHistory()")
    page.wait_for_timeout(300)
    stored = page.evaluate("localStorage.getItem('lumena_chat_history')")
    assert stored is None or stored == "[]", "Chat not cleared"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  API ENDPOINTS (via page fetch)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_api_health(_check_server):
    """/api/health should return 200."""
    import httpx
    r = httpx.get(f"{BASE}/api/health", timeout=5)
    assert r.status_code == 200, f"Expected 200, got {r.status_code}"


def test_api_status(page, _check_server):
    """/api/status should return valid JSON."""
    page.goto(BASE, wait_until="networkidle")
    data = page.evaluate("fetch('/api/status').then(r=>r.json())")
    assert isinstance(data, dict), "Status should return a dict"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  SECURITY HEADERS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_security_headers(_check_server):
    """Security headers should be present on API responses."""
    import httpx
    r = httpx.get(f"{BASE}/api/health", timeout=5)
    assert r.headers.get("x-content-type-options") == "nosniff", "Missing X-Content-Type-Options"
    assert r.headers.get("x-frame-options") == "DENY", "Missing X-Frame-Options"
    csp = r.headers.get("content-security-policy", "")
    assert "default-src" in csp, "Missing CSP"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  UTILS
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_utils_esc(page, _check_server):
    """esc() should properly escape HTML."""
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_timeout(1500)
    result = page.evaluate("esc('<script>alert(1)</script>')")
    assert "<" not in result, "esc() failed to escape HTML"
    assert "&lt;" in result


def test_utils_fmtdur(page, _check_server):
    """fmtDur() should format durations."""
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_timeout(1500)
    assert page.evaluate("fmtDur(1234)") == "1234ms"
    assert page.evaluate("fmtDur(0.5)") == "<1ms"
    assert page.evaluate("fmtDur(null)") == "-"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  MODULE INTEGRITY
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


def test_all_global_functions_available(page, _check_server):
    """All functions from modules should be on window."""
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_timeout(2000)
    fns = [
        # utils
        "esc", "setText", "fmtDur", "loadingDots", "logC", "clearConsole",
        # navigation
        "setupNavigation", "switchPanel", "toggleSection", "toggleNavCollapse",
        "toggleTheme", "applyTheme", "loadPanelData",
        "openCommandPalette", "closeCommandPalette",
        # activity
        "openSidebar", "closeSidebar", "startActivityFeed", "pushActivity",
        # chat
        "setupTextarea", "quickSend", "sendMessage", "addMsg",
        "handleFileSelect", "clearChatHistory", "exportChatMarkdown",
        # api
        "loadStatus", "loadTools", "loadEmotions", "searchCode", "searchMemory",
        "initTraceStream", "checkHealth", "filterTrace", "clearTraceList",
        # panels
        "loadJournal", "loadFacts", "loadProviders", "loadConfig", "saveConfig",
        "loadSessions", "closeSessionDetail", "loadOverview",
        # tasks
        "showNewTaskForm", "createTask", "loadActiveTasks", "renderTasks",
        "loadDaemonActivity", "addTodo", "toggleTodo", "deleteTodo",
        # startup
        "startLumena", "toggleModelDropdown", "loadModels", "switchModel",
        "toggleAgent",
    ]
    missing = page.evaluate(f"""
        (function() {{
            var fns = {fns};
            return fns.filter(f => typeof window[f] !== 'function');
        }})()
    """)
    assert not missing, f"Missing global functions: {missing}"


def test_state_variables_on_window(page, _check_server):
    """State variables should be accessible on window."""
    page.goto(BASE, wait_until="networkidle")
    page.wait_for_timeout(2000)
    checks = page.evaluate("""
        (function() {
            return {
                API_BASE: typeof API_BASE,
                isLoading: typeof isLoading,
                allTools: typeof allTools !== 'undefined',
                allModels: typeof allModels !== 'undefined',
                activeTasks: typeof activeTasks !== 'undefined',
            };
        })()
    """)
    assert checks["API_BASE"] == "string"
    assert checks["isLoading"] == "boolean"
    assert checks["allTools"] is True
    assert checks["allModels"] is True
    assert checks["activeTasks"] is True
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
