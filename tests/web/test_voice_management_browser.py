"""Real-browser proof for Voice advice, apply and rollback controls."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import Route, sync_playwright


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        return


@contextmanager
def _web_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(_QuietHandler, directory=str(WEB)))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_voice_advisor_applies_and_rolls_back_in_browser() -> None:
    calls: list[tuple[str, str]] = []

    def management_payload(goal: str) -> dict:
        return {
            "snapshot": {
                "configuration": {"settings_pending": False},
                "engine": {"effective": "piper"},
            },
            "diagnostic": {
                "healthy": goal != "quality",
                "issues": [] if goal != "quality" else [{"code": "qwen_not_conversation_qualified"}],
            },
            "recommendation": {
                "recommendation_id": f"ticket-{goal}",
                "profile_id": "balanced",
                "goal": goal,
                "confidence": "high",
                "compatible": True,
                "updates": {"LUMENA_VOICE_TTS_ENGINE": "piper"},
                "reasons": [f"goal:{goal}", "piper_low_latency_local"],
                "limitations": [],
            },
        }

    def route_api(route: Route) -> None:
        request = route.request
        parsed = urlparse(request.url)
        path, method = parsed.path, request.method
        calls.append((method, path))
        if path == "/api/auth/config":
            payload = {"auth_required": False, "admin_token": ""}
        elif path.startswith("/api/setup/status"):
            payload = {"needs_setup": False, "setup_complete": True}
        elif path == "/api/voice/status":
            payload = {"available": True, "running": False, "backend": "v2", "state": "stopped"}
        elif path == "/api/voice/management" and method == "GET":
            payload = management_payload(parse_qs(parsed.query).get("goal", ["balanced"])[0])
        elif path.startswith("/api/voice/management/apply/") and method == "POST":
            payload = {"ok": True, "verified": True, "state": "verified", "proof": {}}
        elif path == "/api/voice/management/rollback" and method == "POST":
            payload = {"ok": True, "verified": True, "state": "restored", "proof": {}}
        elif path == "/api/config":
            payload = {"success": True, "items": [], "groups": {}}
        elif path == "/api/voice/performance-profiles":
            payload = {"profiles": []}
        elif path == "/api/trace/stream":
            route.fulfill(status=200, content_type="text/event-stream", body="data: {}\n\n")
            return
        else:
            payload = {}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    with _web_server() as base, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 1000})
        page.route("**/api/**", route_api)
        page.route(
            "https://unpkg.com/**",
            lambda route: route.fulfill(
                status=200,
                content_type="application/javascript",
                body="window.lucide={createIcons:function(){}};",
            ),
        )
        page.goto(base, wait_until="domcontentloaded")
        page.wait_for_function("typeof window.switchPanel === 'function'")
        page.evaluate("""
          document.getElementById('startup-screen')?.setAttribute('style','display:none!important');
          document.getElementById('app-shell').style.display='grid';
          window.switchPanel('voice');
        """)
        page.wait_for_function("document.getElementById('voice-advisor-profile').textContent.includes('Équilibré')")
        page.locator("#voice-advisor-goal").select_option("quality")
        page.wait_for_function("document.getElementById('voice-advisor-health').textContent.includes('Attention')")
        page.wait_for_function("document.getElementById('voice-advisor-evidence').textContent.includes('latence')")
        page.locator("#voice-advisor-apply").click()
        page.wait_for_function("document.getElementById('voice-advisor-message').textContent.includes('vérifié')")
        assert ("POST", "/api/voice/management/apply/ticket-quality") in calls

        page.locator("#voice-advisor-rollback").click()
        page.wait_for_function("document.getElementById('voice-advisor-message').textContent.includes('restaurée')")
        assert ("POST", "/api/voice/management/rollback") in calls
        browser.close()
