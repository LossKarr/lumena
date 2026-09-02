import contextlib
import functools
import json
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from src.utils.paths import DATA_DIR


ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "web" / "index.html"
NAVIGATION = ROOT / "web" / "static" / "js" / "navigation.js"
COMPONENTS = ROOT / "web" / "static" / "css" / "components.css"
ARTIFACTS = DATA_DIR / "test_artifacts" / "command-palette"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        return


@contextlib.contextmanager
def _web_server():
    handler = functools.partial(_QuietHandler, directory=str(ROOT / "web"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def _route_api(route) -> None:
    url = route.request.url
    if "/api/setup/status" in url:
        payload = {"needs_setup": False, "setup_complete": True}
    elif "/api/auth/config" in url:
        payload = {"auth_required": False, "admin_token": ""}
    elif "/api/models" in url:
        payload = {"current_model": "deepseek-v3", "models": []}
    elif "/api/status" in url:
        payload = {"status": "ok", "tool_count": 733, "memory_count": 2137}
    else:
        payload = {}
    route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))


def _open_shell(page, base: str) -> None:
    page.route("**/api/**", _route_api)
    page.route(
        "https://unpkg.com/**",
        lambda route: route.fulfill(
            status=200,
            content_type="application/javascript",
            body="window.lucide={createIcons:function(){}};",
        ),
    )
    page.goto(base, wait_until="domcontentloaded")
    page.wait_for_function("typeof window.openCommandPalette === 'function'")
    page.evaluate("""
      document.getElementById('startup-screen')?.setAttribute('style','display:none!important');
      document.querySelectorAll('.setup-overlay,.onboarding-layer').forEach(el=>el.setAttribute('style','display:none!important'));
      document.getElementById('app-shell').style.display='grid';
    """)


def _assert_inside_viewport(page, selector: str) -> None:
    box = page.locator(selector).bounding_box()
    viewport = page.viewport_size
    assert box is not None and viewport is not None
    assert box["x"] >= -1 and box["y"] >= -1
    assert box["x"] + box["width"] <= viewport["width"] + 1
    assert box["y"] + box["height"] <= viewport["height"] + 1


def test_palette_markup_is_accessible_and_has_no_inline_search_handler():
    index = INDEX.read_text(encoding="utf-8")

    assert 'class="cmd-palette" role="dialog" aria-modal="true"' in index
    assert 'id="cmd-results" role="listbox"' in index
    assert 'id="cmd-input"' in index
    assert 'aria-controls="cmd-results"' in index
    assert 'oninput="filterCommands()"' not in index
    assert "/static/css/components.css?v=7" in index
    assert "/static/js/main.js?v=52" in index


def test_palette_only_targets_panels_that_exist():
    index = INDEX.read_text(encoding="utf-8")
    navigation = NAVIGATION.read_text(encoding="utf-8")

    import re

    targets = set(re.findall(r"panel:'([^']+)'", navigation))
    assert len(targets) >= 35
    missing = sorted(panel for panel in targets if f'id="panel-{panel}"' not in index)
    assert missing == []


def test_palette_has_complete_product_navigation_and_keyboard_contract():
    navigation = NAVIGATION.read_text(encoding="utf-8")

    for category in (
        "Essentiel",
        "Intelligence",
        "Supervision",
        "Connexions",
        "Commerce",
        "Systeme",
        "Actions",
    ):
        assert category in navigation
    for key in ("ArrowDown", "ArrowUp", "Home", "End", "Enter", "Escape"):
        assert f"event.key==='{key}'" in navigation
    assert "normalize('NFD')" in navigation
    assert "CMD_RECENTS_KEY" in navigation
    assert "localStorage.setItem(CMD_RECENTS_KEY" in navigation
    assert "Aucun acces trouve" in navigation
    assert "window._cmdItems=cmdItems" in navigation
    assert "if(command.panel&&!command.action)command.action=()=>switchPanel(command.panel)" in navigation


def test_palette_styles_are_bounded_and_responsive():
    css = COMPONENTS.read_text(encoding="utf-8")

    assert "width:min(680px,100%)" in css
    assert "max-height:min(740px,calc(100vh - 104px))" in css
    assert ".cmd-palette-results{min-height:220px;max-height:520px;overflow-y:auto" in css
    assert ".cmd-palette-overlay{padding:58px 10px 10px}" in css
    assert "\\n.cmd-palette-item" not in css


def test_palette_runtime_keyboard_search_and_responsive_visuals():
    playwright = pytest.importorskip("playwright.sync_api")
    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    with _web_server() as base, playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
        errors: list[str] = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        _open_shell(page, base)

        page.keyboard.press("Control+k")
        page.locator("#cmd-palette-overlay.open").wait_for()
        assert page.locator("#cmd-input").evaluate("el=>el===document.activeElement")
        assert page.locator(".cmd-palette-item").count() >= 40
        assert page.locator(".cmd-palette-group-title").all_text_contents() == [
            "Essentiel", "Intelligence", "Supervision", "Connexions",
            "Commerce", "Systeme", "Actions",
        ]
        _assert_inside_viewport(page, ".cmd-palette")
        page.screenshot(path=str(ARTIFACTS / "palette-desktop.png"), full_page=True)

        search = page.locator("#cmd-input")
        search.fill("memoire")
        assert page.locator(".cmd-palette-item").count() == 1
        assert "Memoire" in page.locator(".cmd-palette-item").inner_text()
        search.press("Enter")
        page.locator("#panel-memory.active").wait_for()
        assert page.locator("#cmd-palette-overlay").get_attribute("aria-hidden") == "true"

        page.keyboard.press("Control+k")
        search.fill("studio modeles")
        assert page.locator(".cmd-palette-item").count() == 1
        assert "Documents" in page.locator(".cmd-palette-item").inner_text()
        search.fill("aucune commande xyz")
        assert page.locator(".cmd-palette-empty").is_visible()
        search.fill("")
        page.keyboard.press("ArrowDown")
        assert page.locator(".cmd-palette-item.selected").count() == 1

        page.set_viewport_size({"width": 390, "height": 844})
        _assert_inside_viewport(page, ".cmd-palette")
        page.screenshot(path=str(ARTIFACTS / "palette-mobile.png"), full_page=True)

        page.set_viewport_size({"width": 1280, "height": 720})
        page.evaluate("""
          document.body.style.transform='scale(0.90)';
          document.body.style.transformOrigin='top left';
          document.body.style.width='111.111111%';
          document.body.style.height='111.111111%';
        """)
        _assert_inside_viewport(page, ".cmd-palette")
        page.screenshot(path=str(ARTIFACTS / "palette-webview2-zoom-090.png"), full_page=True)

        assert errors == []
        browser.close()
