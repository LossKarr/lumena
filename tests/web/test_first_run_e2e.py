from __future__ import annotations

import contextlib
import functools
import json
import socket
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from src.utils.paths import DATA_DIR
from src.runtime import user_profile
from web.routes import deps, onboarding

playwright = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
ARTIFACTS = DATA_DIR / "test_artifacts" / "onboarding"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        return


@contextlib.contextmanager
def _web_server():
    handler = functools.partial(_QuietHandler, directory=str(WEB))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


@contextlib.contextmanager
def _integrated_onboarding_server(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "test-token")
    monkeypatch.setattr(deps, "setup_only_mode", False)
    monkeypatch.setattr(onboarding, "DATA_DIR", tmp_path)
    monkeypatch.setattr(onboarding, "_setup_completed", lambda: True)
    monkeypatch.setattr(user_profile, "MULTI_USER_ENABLED", False)
    app = FastAPI()
    app.include_router(onboarding.router)
    app.mount("/", StaticFiles(directory=str(WEB), html=True), name="web")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
    thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(.02)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()


def _json(route, payload, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(payload))


def _mock_api(
    page,
    *,
    setup_preview: bool,
    setup_required: bool | None = None,
    onboarding_status: str = "not_started",
    onboarding_step: str = "orientation",
    passthrough_onboarding: bool = False,
    codex_connected: bool = False,
    calls: list[str] | None = None,
) -> None:
    selected_goal = None
    steps = [
        {"id":"model","title":"Choisir un cerveau","subtitle":"Un modèle actif","icon":"brain","help":"Sélectionne le modèle utilisé par Lumena.","fields":[{"key":"LUMENA_DEFAULT_MODEL","options":["deepseek-v3"]}],"models_info":{"deepseek-v3":{"provider":"DeepSeek","desc":"Modèle général","cost":"API","badge":"Recommandé"}}},
        {"id":"keys","title":"Connecter un accès IA","subtitle":"Teste une clé ou utilise un accès déjà configuré","icon":"key-round","providers":[],"fields":[]},
        {"id":"security","title":"Sécurité locale","subtitle":"Lumena protège ses actions sensibles","icon":"shield","fields":[{"key":"LUMENA_ADMIN_TOKEN","label":"Token admin","type":"secret","default":"test-token"}]},
        {"id":"locale","title":"Toi et ton espace","subtitle":"Langue et dossier de travail","icon":"globe","fields":[{"key":"LUMENA_LANGUAGE","label":"Langue","type":"select","options":["fr","en"],"default":"fr"},{"key":"LUMENA_WORKSPACE_PATH","label":"Dossier de travail","type":"text","default":"workspace"}]},
        {"id":"voice","title":"Voix","subtitle":"Options avancées","icon":"mic","fields":[]},
    ]

    def handler(route):
        nonlocal selected_goal
        url = route.request.url
        path = url.split("/api/", 1)[-1].split("?", 1)[0]
        if calls is not None:
            calls.append(path)
        if passthrough_onboarding and path.startswith("onboarding/"):
            return route.fallback()
        if path == "setup/status":
            needs_setup = setup_preview if setup_required is None else setup_required
            return _json(route, {"needsSetup": needs_setup, "preview": setup_preview})
        if path == "setup/schema":
            return _json(route, {"steps": steps})
        if path == "preflight":
            return _json(route, {"components":[{"healthy":True,"message":"Python et dépendances disponibles","details":{"required":True}},{"healthy":False,"message":"Ollama non installé","details":{"required":False,"hint":"Optionnel"}}]})
        if path == "codex-subscription/account/status":
            account = {"email_masked":"c***@e***.com"} if codex_connected else None
            return _json(route, {"ok":True,"running":codex_connected,"account":account,"quota":None})
        if path == "codex-subscription/models":
            return _json(route, {"ok":True,"provider":"openai-codex","selected_model":"gpt-5.6-sol","models":[{"model_id":"gpt-5.6-sol","display_name":"GPT-5.6 Sol"}]})
        if path == "codex-subscription/model/select":
            return _json(route, {"success":True,"engine":"codex","model":"codex:gpt-5.6-sol"})
        if path == "setup/ollama-models":
            return _json(route, {"ollama_available":False,"installed_count":0,"catalog":[]})
        if path == "auth/config":
            return _json(route, {"admin_token":"test-token"})
        if path == "models":
            return _json(route, {"models":[{"name":"deepseek-v3","provider":"deepseek","available":True,"current":True,"supports_image_generation":False}]})
        if path == "onboarding/status":
            return _json(route, {"schema_version":1,"setup_completed":True,"tour_status":onboarding_status,"current_step":onboarding_step,"completed_steps":[],"skipped_steps":[],"tour_version":1})
        if path == "onboarding/goal":
            goal = (route.request.post_data_json or {}).get("goal", "chat")
            selected_goal = goal
            return _json(route, {"schema_version":1,"setup_completed":True,"tour_status":"in_progress","current_step":"files" if goal=="file" else "mode_choice","selected_goal":goal,"completed_steps":["first_goal"],"skipped_steps":[],"tour_version":1})
        if path.startswith("onboarding/"):
            return _json(route, {"schema_version":1,"setup_completed":True,"tour_status":"in_progress","current_step":"orientation","selected_goal":selected_goal,"completed_steps":[],"skipped_steps":[],"tour_version":1})
        if path in {"status", "health"}:
            return _json(route, {"status":"ok","tools_count":732,"skills_count":34})
        if path in {"tools", "trace/recent", "chat/history"}:
            return _json(route, {"tools":[],"events":[],"messages":[]})
        return _json(route, {})

    page.route("**/api/**", handler)


def _assert_inside_viewport(page, selector: str) -> None:
    box = page.locator(selector).bounding_box()
    assert box is not None
    viewport = page.viewport_size
    assert viewport is not None
    assert box["x"] >= -1 and box["y"] >= -1
    assert box["x"] + box["width"] <= viewport["width"] + 1
    assert box["y"] + box["height"] <= viewport["height"] + 1


def _assert_focus_matches_target(page, target_selector: str) -> None:
    page.locator(".onboarding-focus:not([hidden])").wait_for()
    page.wait_for_timeout(40)
    geometry = page.evaluate("""selector => {
      const focus=document.querySelector('.onboarding-focus');
      const target=document.querySelector(selector);
      const f=focus.getBoundingClientRect(),t=target.getBoundingClientRect();
      let visible={
        left:Math.max(0,t.left),top:Math.max(0,t.top),
        right:Math.min(innerWidth,t.right),bottom:Math.min(innerHeight,t.bottom),
      };
      for(let parent=target.parentElement;parent&&parent!==document.body;parent=parent.parentElement){
        const style=getComputedStyle(parent),bounds=parent.getBoundingClientRect();
        if(/auto|scroll|hidden|clip/.test(`${style.overflowX} ${style.overflow}`)){
          visible.left=Math.max(visible.left,bounds.left);visible.right=Math.min(visible.right,bounds.right);
        }
        if(/auto|scroll|hidden|clip/.test(`${style.overflowY} ${style.overflow}`)){
          visible.top=Math.max(visible.top,bounds.top);visible.bottom=Math.min(visible.bottom,bounds.bottom);
        }
      }
      const overlap={
        width:Math.max(0,Math.min(f.right,visible.right)-Math.max(f.left,visible.left)),
        height:Math.max(0,Math.min(f.bottom,visible.bottom)-Math.max(f.top,visible.top)),
      };
      return {focus:{left:f.left,top:f.top,right:f.right,bottom:f.bottom},visible,overlap,
              viewport:{width:innerWidth,height:innerHeight}};
    }""", target_selector)
    focus, visible, overlap, viewport = (
        geometry["focus"], geometry["visible"], geometry["overlap"], geometry["viewport"]
    )
    assert focus["left"] >= 7 and focus["top"] >= 7
    assert focus["right"] <= viewport["width"] - 7
    assert focus["bottom"] <= viewport["height"] - 7
    assert overlap["width"] >= max(1, (visible["right"] - visible["left"]) * .8)
    assert overlap["height"] >= max(1, (visible["bottom"] - visible["top"]) * .8)


@pytest.mark.parametrize("viewport", [
    {"width":1366,"height":768}, {"width":1920,"height":1080},
    {"width":820,"height":1180}, {"width":390,"height":844},
    {"width":683,"height":384},
])
def test_first_run_quick_setup_is_visible_responsive_and_keeps_complete_mode(viewport) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with _web_server() as base, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport=viewport, reduced_motion="reduce")
        errors=[]
        page.on("pageerror", lambda error: errors.append(str(error)))
        _mock_api(page, setup_preview=True)
        page.goto(f"{base}/?preview=1", wait_until="domcontentloaded")
        page.locator("#setup-wizard-overlay:not([hidden])").wait_for()
        assert page.get_by_text("Configuration rapide").is_visible()
        assert page.get_by_text("Configuration complète").is_visible()
        assert page.get_by_text("Python et dépendances disponibles").is_visible()
        _assert_inside_viewport(page, ".setup-wizard")
        page.locator(".setup-step.active").wait_for(state="visible")
        page.locator("#setup-next").focus()
        page.keyboard.press("Tab")
        assert page.evaluate("document.activeElement?.dataset?.setupMode") == "quick"
        page.keyboard.press("Shift+Tab")
        assert page.evaluate("document.activeElement?.id") == "setup-next"
        page.screenshot(path=str(ARTIFACTS / f"setup-{viewport['width']}x{viewport['height']}.png"), full_page=True)
        page.locator('[data-setup-mode="complete"]').click()
        assert page.locator(".setup-dot").count() == 7
        page.locator('[data-setup-mode="quick"]').click()
        assert page.locator(".setup-dot").count() == 5
        page.locator('#setup-next').click()
        page.get_by_role("heading", name="Choisir comment Lumena réfléchit").wait_for()
        _assert_inside_viewport(page, ".setup-wizard")
        page.screenshot(path=str(ARTIFACTS / f"access-{viewport['width']}x{viewport['height']}.png"), full_page=True)
        assert page.get_by_role("button", name="Clé API").is_visible()
        assert page.get_by_role("button", name="Abonnement ChatGPT").is_visible()
        assert page.get_by_role("button", name="Modèle local").is_visible()
        page.get_by_role("button", name="Clé API").click()
        assert page.locator(".setup-dot").count() == 7
        assert not errors
        browser.close()


@pytest.mark.parametrize("viewport", [
    {"width":1366,"height":768}, {"width":390,"height":844}, {"width":683,"height":384},
])
def test_product_tour_anchors_to_real_shell_and_falls_back_on_mobile(viewport) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with _web_server() as base, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport=viewport, reduced_motion="reduce")
        errors=[]
        page.on("pageerror", lambda error: errors.append(str(error)))
        _mock_api(page, setup_preview=False, onboarding_status="in_progress")
        page.goto(base, wait_until="domcontentloaded")
        page.evaluate("""() => {
          document.getElementById('startup-screen').classList.add('hidden');
          document.getElementById('app-shell').style.display='grid';
          document.dispatchEvent(new CustomEvent('lumena:app-ready'));
        }""")
        page.locator("#onboarding-layer:not([hidden])").wait_for()
        assert page.get_by_role("heading", name="Tout reste à portée de main").is_visible()
        _assert_inside_viewport(page, ".onboarding-popover")
        page.screenshot(path=str(ARTIFACTS / f"tour-{viewport['width']}x{viewport['height']}.png"), full_page=True)
        page.locator('.onboarding-next').click()
        page.get_by_role("heading", name="Que veux-tu faire maintenant ?").wait_for()
        assert "is-centered" in (page.locator("#onboarding-layer").get_attribute("class") or "")
        if viewport["width"] > 760:
            center = page.locator(".onboarding-popover").evaluate("el => { const r=el.getBoundingClientRect(); return r.left + r.width / 2; }")
            assert abs(center - viewport["width"] / 2) <= 2
        _assert_inside_viewport(page, ".onboarding-popover")
        page.screenshot(path=str(ARTIFACTS / f"goal-{viewport['width']}x{viewport['height']}.png"), full_page=True)
        page.locator('[data-goal="chat"]').click()
        page.get_by_role("heading", name="Choisis le niveau d’action").wait_for()
        assert not errors
        browser.close()


def test_tour_changes_real_mode_then_waits_for_real_response() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with _web_server() as base, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1866, "height": 1053}, reduced_motion="reduce")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        _mock_api(page, setup_preview=False, onboarding_status="in_progress")
        page.goto(base, wait_until="domcontentloaded")
        page.evaluate("""() => {
          document.getElementById('startup-screen').classList.add('hidden');
          document.getElementById('app-shell').style.display='grid';
          document.dispatchEvent(new CustomEvent('lumena:app-ready'));
        }""")
        _assert_focus_matches_target(page, '[data-onboarding-target="navigation"]')
        page.locator(".onboarding-next").click()
        page.get_by_role("heading", name="Que veux-tu faire maintenant ?").wait_for()
        page.locator('[data-goal="agent"]').click()
        page.get_by_role("heading", name="Choisis le niveau d’action").wait_for()
        _assert_focus_matches_target(page, '[data-onboarding-target="agent-mode"]')
        assert page.locator(".onboarding-next").is_hidden()
        page.locator('[data-mode="agent"]').click()
        page.get_by_role("heading", name="Parle à Lumena").wait_for()
        _assert_focus_matches_target(page, '[data-onboarding-target="composer"]')
        page.screenshot(path=str(ARTIFACTS / "frame-composer-1866x1053.png"), full_page=True)
        assert page.locator("#agent-toggle").get_attribute("class").find("active") >= 0
        assert page.evaluate("document.activeElement?.id") == "message-input"
        page.evaluate("document.dispatchEvent(new CustomEvent('lumena:agent-progress'))")
        page.evaluate("document.dispatchEvent(new CustomEvent('lumena:chat-response',{detail:{agent:true}}))")
        page.get_by_role("heading", name="Ajoute du contexte quand tu en as besoin").wait_for()
        _assert_focus_matches_target(page, '[data-onboarding-target="file-button"]')
        page.locator(".onboarding-next").click()
        page.get_by_role("heading", name="Que veux-tu découvrir ?").wait_for()
        _assert_focus_matches_target(page, '[data-onboarding-target="agent-navigation"]')
        page.screenshot(path=str(ARTIFACTS / "frame-agent-navigation-1866x1053.png"), full_page=True)
        assert not errors
        browser.close()


def test_tour_frames_align_inside_desktop_compensated_zoom() -> None:
    """pywebview scales body; viewport rectangles must be converted back once."""
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with _web_server() as base, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1866, "height": 1053}, reduced_motion="reduce")
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        _mock_api(page, setup_preview=False, onboarding_status="in_progress")
        page.goto(base, wait_until="domcontentloaded")
        page.evaluate("""() => {
          const zoom=.9,root=document.documentElement,body=document.body;
          root.dataset.lumenaDesktopZoom=String(zoom);
          root.classList.add('lumena-desktop-zoom');
          root.style.overflow='hidden';
          body.style.transform=`scale(${zoom})`;
          body.style.transformOrigin='top left';
          body.style.width=`${100/zoom}%`;
          body.style.height=`${100/zoom}%`;
          body.style.overflow='hidden';
          document.getElementById('startup-screen').classList.add('hidden');
          document.getElementById('app-shell').style.display='grid';
          document.dispatchEvent(new CustomEvent('lumena:app-ready'));
        }""")
        _assert_focus_matches_target(page, '[data-onboarding-target="navigation"]')
        page.locator('.onboarding-next').click()
        page.locator('[data-goal="agent"]').click()
        _assert_focus_matches_target(page, '[data-onboarding-target="agent-mode"]')
        page.locator('[data-mode="agent"]').click()
        page.get_by_role("heading", name="Parle à Lumena").wait_for()
        _assert_focus_matches_target(page, '[data-onboarding-target="composer"]')
        _assert_inside_viewport(page, ".onboarding-popover")
        page.screenshot(path=str(ARTIFACTS / "frame-desktop-zoom-090.png"), full_page=True)
        assert not errors
        browser.close()


def test_tour_resumes_exact_step_falls_back_and_escape_skips() -> None:
    with _web_server() as base, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 920, "height": 700}, reduced_motion="reduce")
        _mock_api(
            page,
            setup_preview=False,
            onboarding_status="in_progress",
            onboarding_step="first_message",
        )
        page.goto(base, wait_until="domcontentloaded")
        page.evaluate("""() => {
          document.getElementById('compose-box').remove();
          document.getElementById('startup-screen').classList.add('hidden');
          document.getElementById('app-shell').style.display='grid';
          document.dispatchEvent(new CustomEvent('lumena:app-ready'));
        }""")
        page.get_by_role("heading", name="Parle à Lumena").wait_for()
        assert page.locator("#onboarding-layer").get_attribute("class").find("is-centered") >= 0
        page.keyboard.press("Escape")
        page.get_by_role("heading", name="Quitter le tutoriel ?").wait_for()
        page.get_by_role("button", name="Reprendre plus tard").click()
        page.locator("#onboarding-layer").wait_for(state="hidden")
        browser.close()


def test_file_goal_requires_a_real_file_selection_and_fills_no_message_automatically() -> None:
    with _web_server() as base, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1100, "height": 760}, reduced_motion="reduce")
        _mock_api(page, setup_preview=False, onboarding_status="in_progress")
        page.goto(base, wait_until="domcontentloaded")
        page.evaluate("""() => {
          document.getElementById('startup-screen').classList.add('hidden');
          document.getElementById('app-shell').style.display='grid';
          document.dispatchEvent(new CustomEvent('lumena:app-ready'));
        }""")
        page.locator('.onboarding-next').click()
        page.locator('[data-goal="file"]').click()
        page.get_by_role("heading", name="Ajoute du contexte quand tu en as besoin").wait_for()
        assert page.locator('.onboarding-next').is_disabled()
        assert page.locator('#message-input').input_value() == ""
        page.set_input_files('#file-upload-input', {
            "name": "brief.txt", "mimeType": "text/plain", "buffer": b"brief",
        })
        page.get_by_role("heading", name="Choisis le niveau d’action").wait_for()
        assert page.locator('#message-input').input_value() == ""
        browser.close()


def test_completed_tour_can_be_replayed_without_rerunning_setup() -> None:
    with _web_server() as base, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1100, "height": 760}, reduced_motion="reduce")
        reset_authorization = []
        page.on("request", lambda request: reset_authorization.append(
            request.headers.get("authorization", "")
        ) if "/api/onboarding/reset" in request.url else None)
        _mock_api(page, setup_preview=False, onboarding_status="completed")
        page.goto(base, wait_until="domcontentloaded")
        page.wait_for_function("typeof ADMIN_TOKEN !== 'undefined' && ADMIN_TOKEN === 'test-token'")
        page.evaluate("""() => {
          document.getElementById('startup-screen').classList.add('hidden');
          document.getElementById('app-shell').style.display='grid';
          document.dispatchEvent(new CustomEvent('lumena:app-ready'));
        }""")
        assert page.locator("#onboarding-layer").is_hidden()
        page.evaluate("window.replayOnboarding()")
        page.get_by_role("heading", name="Tout reste à portée de main").wait_for()
        assert page.locator("#setup-wizard-overlay").is_hidden()
        assert reset_authorization == ["Bearer test-token"]
        browser.close()


def test_smart_setup_reuses_detected_configuration_and_can_activate_codex() -> None:
    with _web_server() as base, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1180, "height": 820}, reduced_motion="reduce")
        calls = []
        _mock_api(page, setup_preview=False, setup_required=True, codex_connected=True, calls=calls)
        page.goto(base, wait_until="domcontentloaded")
        page.locator('#setup-next').click()
        page.get_by_role("heading", name="Choisir comment Lumena réfléchit").wait_for()
        page.get_by_text("Configuration détectée").wait_for()
        page.get_by_role("button", name="Abonnement ChatGPT").click()
        page.get_by_role("button", name="Utiliser cet abonnement").wait_for()
        page.get_by_role("button", name="Utiliser cet abonnement").click()
        page.get_by_role("heading", name="Toi et ton espace").wait_for()
        assert "codex-subscription/model/select" in calls
        assert not page.get_by_role("heading", name="Connecter un accès IA").is_visible()
        browser.close()


def test_smart_setup_preview_never_changes_codex_selection() -> None:
    with _web_server() as base, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1180, "height": 820}, reduced_motion="reduce")
        calls = []
        _mock_api(page, setup_preview=True, codex_connected=True, calls=calls)
        page.goto(f"{base}/?preview=1", wait_until="domcontentloaded")
        page.locator('#setup-next').click()
        page.get_by_role("heading", name="Choisir comment Lumena réfléchit").wait_for()
        page.get_by_role("button", name="Abonnement ChatGPT").click()
        page.get_by_role("button", name="Utiliser cet abonnement").click()
        page.get_by_role("heading", name="Toi et ton espace").wait_for()
        assert "codex-subscription/model/select" not in calls
        browser.close()


def test_real_backend_persists_file_goal_and_resumes_exact_step(monkeypatch, tmp_path) -> None:
    with _integrated_onboarding_server(monkeypatch, tmp_path) as base, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1100, "height": 760}, reduced_motion="reduce")
        _mock_api(page, setup_preview=False, passthrough_onboarding=True)
        page.goto(base, wait_until="domcontentloaded")
        page.wait_for_function("typeof ADMIN_TOKEN !== 'undefined' && ADMIN_TOKEN === 'test-token'")
        page.evaluate("""async () => {
          document.getElementById('startup-screen').classList.add('hidden');
          document.getElementById('app-shell').style.display='grid';
          document.dispatchEvent(new CustomEvent('lumena:app-ready'));
          await window.replayOnboarding();
        }""")
        page.locator('.onboarding-next').click()
        page.locator('[data-goal="file"]').click()
        page.get_by_role("heading", name="Ajoute du contexte quand tu en as besoin").wait_for()
        page.set_input_files('#file-upload-input', {
            "name": "brief.txt", "mimeType": "text/plain", "buffer": b"brief",
        })
        page.get_by_role("heading", name="Choisis le niveau d’action").wait_for()
        stored = page.request.get(f"{base}/api/onboarding/status").json()
        assert stored["selected_goal"] == "file"
        assert "files" in stored["completed_steps"]
        page.reload(wait_until="domcontentloaded")
        page.evaluate("""() => {
          document.getElementById('startup-screen').classList.add('hidden');
          document.getElementById('app-shell').style.display='grid';
          document.dispatchEvent(new CustomEvent('lumena:app-ready'));
        }""")
        page.get_by_role("heading", name="Choisis le niveau d’action").wait_for()
        browser.close()


def test_replay_backend_failure_is_inline_and_never_uses_native_dialog() -> None:
    with _web_server() as base, playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1100, "height": 760}, reduced_motion="reduce")
        dialogs = []
        page.on("dialog", lambda dialog: dialogs.append(dialog.message))
        _mock_api(page, setup_preview=False, onboarding_status="completed")
        page.route("**/api/onboarding/reset", lambda route: _json(route, {"detail":"Session expirée"}, 401))
        page.goto(base, wait_until="domcontentloaded")
        page.evaluate("""() => {
          document.getElementById('startup-screen').classList.add('hidden');
          document.getElementById('app-shell').style.display='grid';
          document.dispatchEvent(new CustomEvent('lumena:app-ready'));
        }""")
        page.evaluate("window.replayOnboarding()")
        page.get_by_role("heading", name="Impossible de démarrer le parcours").wait_for()
        assert page.get_by_text("Session expirée", exact=False).is_visible()
        assert dialogs == []
        browser.close()
