"""Browser proof for the production Overview without starting Lumena services."""
from __future__ import annotations

import json
import io
import threading
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from PIL import Image, ImageChops
from playwright.sync_api import Route, sync_playwright


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"
PROOF = ROOT / "plans" / "overview-concepts" / "proof"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        return


@contextmanager
def _web_server():
    handler = partial(_QuietHandler, directory=str(WEB))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _payload(path: str) -> dict:
    if path == "/api/auth/config":
        return {"auth_required": False, "admin_token": ""}
    if path.startswith("/api/setup/status"):
        return {"needs_setup": False, "setup_complete": True}
    if path == "/api/health":
        return {"status": "ok"}
    if path == "/api/status":
        return {
            "memory_count": 1831, "skills_loaded": 34, "tool_count": 732,
            "active_modules": 7, "total_modules": 7, "autonomy_running": True,
            "telegram_running": True, "whatsapp_running": False,
            "twitter_enabled": True, "twitter_running": True,
            "sessions_active": 1, "sessions_total": 26,
            "pipeline_errors_total": 2, "pipeline_timeouts_total": 0,
            "slo_enabled": True, "slo_success_rate": 0.992,
            "slo_latency_median_ms": 412,
            "modules": {"memory": True, "rules": True, "hooks": True},
        }
    if path == "/api/models":
        return {"current_model": "gpt-5.6-luna", "models": [{
            "name": "gpt-5.6-luna", "display_name": "GPT-5.6 Luna",
            "provider": "openai", "available": True, "current": True,
        }]}
    if path.startswith("/api/missions"):
        return {"success": True, "missions": [
            {"task_id":"task_lead_1","state":"running","created_at":"2026-08-16T09:58:00Z","updated_at":"2026-08-16T10:04:00Z","message_preview":"Plan marketing Q2","metadata":{"objective":"Plan marketing Q2","mission_workspace":"missions/task_lead_1"}},
            {"task_id":"task_worker_1","state":"done","metadata":{"objective":"Collecte des sources","parent_task_id":"task_lead_1"}},
            {"task_id":"task_worker_2","state":"running","metadata":{"objective":"Synthèse","parent_task_id":"task_lead_1"}},
        ]}
    if path.startswith("/api/tasks"):
        return {"success": True, "tasks": [], "total": 0}
    if path.startswith("/api/alerts"):
        return {"success": True, "alerts": [{"ts":"2026-08-16T10:03:00Z","severity":"warning","channel":"mcp","message":"Valider une approbation MCP","ok":False}], "total":1}
    if path.startswith("/api/document-studio/library"):
        return {"documents": [
            {"id":"doc_1","filename":"Rapport_Salon_Nantes_v2.pdf","title":"Rapport Salon Nantes v2","format":"pdf","source_kind":"generated","imported_at":"2026-08-16T10:02:18Z","metadata":{"render_verified":True}},
            {"id":"doc_2","filename":"Synthese_Fournisseurs.xlsx","format":"xlsx","source_kind":"mission","imported_at":"2026-08-16T10:01:02Z","metadata":{"render_verified":True}},
        ]}
    if path.startswith("/api/trace/recent"):
        return {"events": [
            {"type":"tool","tool_name":"pytest","message":"18/18 tests verts","status":"success","ts":"2026-08-16T10:01:33Z","duration_ms":820},
            {"type":"checkpoint","message":"42 souvenirs consolidés","status":"success","ts":"2026-08-16T10:02:07Z"},
            {"type":"tool","tool_name":"create_pdf","message":"Rapport vérifié","status":"success","ts":"2026-08-16T10:02:18Z"},
        ], "count":3}
    if path == "/api/providers":
        return {"success": True, "providers": [{"name":"openai","status":"Sain","healthy":True,"api_configured":True}]}
    if path == "/api/voice/status":
        return {"available": True, "running": False}
    if path == "/api/mcp/health":
        return {"available": True, "components": {"catalog":{"available":True},"approval_queue":{"available":True}}}
    if path == "/api/mcp/observability/overview":
        return {"catalog_counts":{"declared":12,"installed":8,"active":5},"approvals_pending_count":0}
    if path == "/api/peers":
        return {"peers": [{"instance_id":"alice","status":"trusted"}], "count":1}
    if path == "/api/workspaces/serving":
        return {"serving": [{"slug":"demo","url":"http://127.0.0.1:8081","port":8081,"path":"workspace/demo"}]}
    if path == "/api/system/reliability":
        return {"tools":{"success_count":48,"error_total":1},"policy":{"refuse_count":2},"routing":{"total":15}}
    if path.startswith("/api/runtime/audit"):
        return {
            "total_tools":732, "advertised_count":732,
            "contract_callable_any_context":732, "drift_count":0,
            "broken_count":0, "categories":22,
        }
    if path == "/api/daemon/activity":
        return {"success":True,"handlers":[{"handler":"presence","timestamp":"2026-08-16T10:02:45Z","success":True,"summary":"Présence évaluée"}],"ops":{"incidents_today":[]},"total":1}
    if path.startswith("/api/journal"):
        return {"success":True,"entries":[{"type":"learning","summary":"Connaissance consolidée","timestamp":"2026-08-16T10:01:00Z"}],"total":1}
    if path == "/api/workspaces":
        return {"workspaces":[{"slug":"demo","files_count":6}]}
    if path.startswith("/api/sessions"):
        return {"sessions":[{"conversation_id":"conv_1","status":"active"}],"stats":{"active":1}}
    if path == "/api/hooks":
        return {"count":1,"hooks":[{"name":"audit","enabled":True}]}
    if path == "/api/training":
        return {"success":True,"datasets":[],"total_conversations":42}
    if path == "/api/finetuning/status":
        return {"active_job":None,"gpu":{"available":True}}
    # 2026-08-29 — l'Overview appelle desormais cette route (chantier « mise a
    # jour auto »). Le mock ne la connaissait pas, donc ces deux tests etaient
    # ROUGES — sans que personne le voie, `tests/web/` n'etant plus collecte
    # (voir `norecursedirs` dans pytest.ini). Forme prise sur la vraie route,
    # `web/routes/updates.py:67`, pas devinee.
    if path.startswith("/api/updates/releases"):
        return {"releases":[]}
    if path.startswith("/api/updates/status"):
        # Forme prise sur `UpdateService.status()`, src/runtime/update_service.py:124.
        return {"state":"idle","current_version":"1.0.54","installation_type":"git",
                "rollback_available":False,"settings":{}}
    raise AssertionError(f"Overview requested an unmocked API route: {path}")


def _route_api(route: Route) -> None:
    path = urlparse(route.request.url).path
    query = urlparse(route.request.url).query
    full = f"{path}?{query}" if query else path
    if path == "/api/trace/stream":
        route.fulfill(status=200, content_type="text/event-stream", body="data: {}\n\n")
        return
    route.fulfill(status=200, content_type="application/json", body=json.dumps(_payload(full)))


def _route_api_with_models_down(route: Route) -> None:
    if urlparse(route.request.url).path == "/api/models":
        route.fulfill(status=503, content_type="application/json", body='{"detail":"provider catalog unavailable"}')
        return
    _route_api(route)


def _open_overview(page, base: str) -> None:
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
    page.wait_for_function("typeof window.loadOverview === 'function'")
    page.evaluate("""
      document.getElementById('startup-screen')?.setAttribute('style','display:none!important');
      document.getElementById('app-shell').style.display='grid';
      window.switchPanel('overview');
    """)
    page.wait_for_selector("#panel-overview.active")
    page.wait_for_function("document.getElementById('ov-updated').textContent.toLowerCase().includes('actualis')")
    page.wait_for_timeout(800)


def test_overview_runtime_desktop_mobile_and_webgl() -> None:
    PROOF.mkdir(parents=True, exist_ok=True)
    with _web_server() as base, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        console_errors: list[str] = []
        page.on("pageerror", lambda error: console_errors.append(str(error)))
        _open_overview(page, base)

        assert page.locator("#ov-global-state").evaluate("el => el.classList.contains('is-warn')")
        assert not page.locator("#ov-global-state").evaluate("el => el.classList.contains('is-danger')")
        assert page.locator("#ov-work .overview-row").count() == 1
        assert page.locator("#ov-systems .overview-system").count() == 12
        assert page.locator("#ov-deliverables .overview-row").count() == 2
        assert page.locator("#ov-capabilities .overview-capability").count() >= 40
        assert page.locator("#ov-sources .overview-source-row").count() == 21
        assert "21/21" in page.locator("#ov-source-summary").inner_text()
        assert "732 OUTILS · 22 DOMAINES" in page.locator("#ov-capabilities").inner_text()
        health_text = page.locator("#ov-health").inner_text().upper()
        assert "OUTILS APPELABLES" in health_text
        assert "DRIFT CONTRACTUEL" in health_text
        assert "Twitter / X" in page.locator("#ov-capabilities").inner_text()
        assert "Discord" in page.locator("#ov-capabilities").inner_text()
        assert "Fichiers" in page.locator("#ov-capabilities").inner_text()
        assert page.locator("#ov-core-nodes .overview-node").count() == 7
        assert not page.locator(".overview-core").evaluate("el => el.classList.contains('is-fallback')")
        assert page.locator("#ov-core-stage").screenshot(path=str(PROOF / "overview-core.png"))
        canvas_png = page.locator("#ov-core-canvas").screenshot(path=str(PROOF / "overview-core-canvas.png"))
        pixels = Image.open(io.BytesIO(canvas_png)).convert("RGBA")
        flat = pixels.get_flattened_data() if hasattr(pixels, "get_flattened_data") else pixels.getdata()
        orange_pixels = sum(
            1 for red, green, blue, alpha in flat
            if alpha > 20 and red > 60 and red > green * 1.1 and red > blue * 1.3
        )
        assert orange_pixels > 1_000, "The WebGL canvas rendered but the Lumena orange mark is blank"
        page.screenshot(path=str(PROOF / "overview-desktop.png"), full_page=True)

        page.locator(".overview-system[data-ov-panel='missions']").click()
        assert page.locator("#panel-missions").evaluate("el => el.classList.contains('active')")
        page.evaluate("window.switchPanel('overview')")
        page.wait_for_selector("#panel-overview.active")

        page.locator("#ov-customize").click()
        health_toggle = page.locator("[data-ov-toggle='health']")
        health_toggle.uncheck()
        assert page.locator("[data-ov-widget='health']").is_hidden()
        page.locator("#ov-layout-reset").click()
        assert page.locator("[data-ov-widget='health']").is_visible()
        page.locator("#ov-customize").click()

        page.set_viewport_size({"width": 820, "height": 900})
        page.wait_for_timeout(250)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(PROOF / "overview-tablet.png"), full_page=True)

        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(350)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(PROOF / "overview-mobile.png"), full_page=True)
        assert not console_errors, console_errors
        browser.close()


def test_overview_motion_drag_and_webgl_fallback_contract() -> None:
    PROOF.mkdir(parents=True, exist_ok=True)
    with _web_server() as base, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)

        reduced = browser.new_context(viewport={"width": 1280, "height": 760}, reduced_motion="reduce")
        page = reduced.new_page()
        _open_overview(page, base)
        first_frame = Image.open(io.BytesIO(page.locator("#ov-core-canvas").screenshot())).convert("RGB")
        page.wait_for_timeout(500)
        second_frame = Image.open(io.BytesIO(page.locator("#ov-core-canvas").screenshot())).convert("RGB")
        assert ImageChops.difference(first_frame, second_frame).getbbox() is None
        reduced.close()

        interactive = browser.new_page(viewport={"width": 1280, "height": 760})
        _open_overview(interactive, base)
        interactive.locator("#ov-core-pause").click()
        before_drag = Image.open(io.BytesIO(interactive.locator("#ov-core-canvas").screenshot())).convert("RGB")
        box = interactive.locator("#ov-core-stage").bounding_box()
        assert box is not None
        interactive.mouse.move(box["x"] + box["width"] * .45, box["y"] + box["height"] * .5)
        interactive.mouse.down()
        interactive.mouse.move(box["x"] + box["width"] * .62, box["y"] + box["height"] * .5, steps=4)
        interactive.mouse.up()
        after_drag = Image.open(io.BytesIO(interactive.locator("#ov-core-canvas").screenshot())).convert("RGB")
        assert ImageChops.difference(before_drag, after_drag).getbbox() is not None
        interactive.close()

        fallback = browser.new_page(viewport={"width": 1280, "height": 760})
        fallback.add_init_script("Object.defineProperty(window,'WebGLRenderingContext',{value:undefined,configurable:true});")
        _open_overview(fallback, base)
        assert fallback.locator(".overview-core").evaluate("el => el.classList.contains('is-fallback')")
        assert fallback.locator("#ov-core-fallback").is_visible()
        fallback.screenshot(path=str(PROOF / "overview-fallback.png"), full_page=True)
        fallback.close()
        browser.close()


def test_overview_exposes_critical_source_failure_without_false_green() -> None:
    with _web_server() as base, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 800})
        page.route("**/api/**", _route_api_with_models_down)
        page.route(
            "https://unpkg.com/**",
            lambda route: route.fulfill(
                status=200,
                content_type="application/javascript",
                body="window.lucide={createIcons:function(){}};",
            ),
        )
        page.goto(base, wait_until="domcontentloaded")
        page.wait_for_function("typeof window.loadOverview === 'function'")
        page.evaluate("""
          document.getElementById('startup-screen')?.setAttribute('style','display:none!important');
          document.getElementById('app-shell').style.display='grid';
          window.switchPanel('overview');
        """)
        page.wait_for_function("document.querySelectorAll('#ov-sources .overview-source-row').length === 21")
        models_row = page.locator("#ov-sources .overview-source-row").filter(has_text="Modèles")
        assert "ERREUR 503" in models_row.inner_text()
        assert page.locator("#ov-global-state").evaluate("el => el.classList.contains('is-danger')")
        assert "Sources critiques" in page.locator("#ov-global-state").inner_text()
        browser.close()
