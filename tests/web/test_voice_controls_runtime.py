"""Browser proof for the Voice conversation controls."""
from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import Route, sync_playwright


ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "web"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *_args) -> None:
        return


@contextmanager
def _web_server():
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), partial(_QuietHandler, directory=str(WEB))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_voice_save_and_push_to_talk_buttons_execute_in_browser() -> None:
    calls: list[tuple[str, str, object]] = []
    config_values = {
        "LUMENA_VOICE_V2_MODE": "chat",
        "LUMENA_VOICE_ACTIVATION_MODE": "push_to_talk",
        "LUMENA_STT_LANGUAGE": "fr",
        "LUMENA_VOICE_WAKE_PHRASE": "Lumena",
        "LUMENA_STT_MODEL": "tiny",
    }
    profile_applied = {"value": False}

    def config_payload() -> dict:
        specs = {
            "LUMENA_VOICE_V2_MODE": ("Mode Voice V2", "select", ["chat", "agent"]),
            "LUMENA_VOICE_ACTIVATION_MODE": ("Activation du micro", "select", ["wake_phrase", "push_to_talk", "open_mic"]),
            "LUMENA_STT_LANGUAGE": ("Langue d'écoute", "select", ["auto", "fr", "en", "es"]),
            "LUMENA_VOICE_WAKE_PHRASE": ("Phrase d'activation", "text", None),
            "LUMENA_STT_MODEL": ("Modèle Whisper", "select", ["tiny", "small", "medium"]),
        }
        items = []
        for key, value in config_values.items():
            label, kind, options = specs[key]
            item = {"key": key, "value": value, "label": label, "group": "Voix", "type": kind}
            if options is not None:
                item["options"] = options
            items.append(item)
        return {"success": True, "items": items, "groups": {"Voix": items}}

    def route_api(route: Route) -> None:
        request = route.request
        path = urlparse(request.url).path
        method = request.method
        body = request.post_data_json if request.post_data else None
        calls.append((method, path, body))
        if path == "/api/auth/config":
            payload = {"auth_required": False, "admin_token": ""}
        elif path.startswith("/api/setup/status"):
            payload = {"needs_setup": False, "setup_complete": True}
        elif path == "/api/voice/status":
            payload = {
                "available": True,
                "running": True,
                "backend": "v2",
                "state": "running",
                "activation_mode": "push_to_talk",
            }
        elif path == "/api/config" and method == "GET":
            payload = config_payload()
        elif path == "/api/config" and method == "PUT":
            config_values.update((body or {}).get("updates", {}))
            payload = {
                "success": True,
                "needs_restart": True,
                "note": "Réglages enregistrés.",
            }
        elif path == "/api/voice/restart" and method == "POST":
            payload = {
                "running": True,
                "restarted": True,
                "backend": "v2",
                "state": "running",
                "activation_mode": "push_to_talk",
            }
        elif path == "/api/voice/push-to-talk" and method == "POST":
            payload = {"armed": True, "scope": "next_utterance"}
        elif path == "/api/voice/test-micro" and method == "POST":
            payload = {
                "ok": True,
                "active_runtime": True,
                "calibration": {"energy_threshold": 180},
            }
        elif path == "/api/voice/stop-audio" and method == "POST":
            payload = {"stopped": True, "task_continues": True}
        elif path == "/api/voice/performance-profiles" and method == "GET":
            payload = {
                "active_profile": "balanced" if profile_applied["value"] else "custom",
                "profiles": [{
                    "id": "balanced", "order": 3, "label": "Équilibré",
                    "tagline": "Recommandé", "description": "Profil de test",
                    "quality": 3, "responsiveness": 4, "requirement": "CPU",
                    "privacy": "Local", "compatible": True,
                    "incompatibility_reasons": [], "active": profile_applied["value"],
                    "recommended": True,
                }],
            }
        elif path == "/api/voice/performance-profiles/balanced" and method == "POST":
            profile_applied["value"] = True
            config_values["LUMENA_STT_MODEL"] = "small"
            payload = {
                "success": True,
                "profile": "balanced",
                "updated": ["LUMENA_STT_MODEL"],
                "needs_restart": True,
                "note": "Profil enregistré.",
            }
        elif path == "/api/trace/stream":
            route.fulfill(
                status=200, content_type="text/event-stream", body="data: {}\n\n"
            )
            return
        else:
            payload = {}
        route.fulfill(
            status=200, content_type="application/json", body=json.dumps(payload)
        )

    with _web_server() as base, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1280, "height": 900})
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
        page.wait_for_function("typeof window.saveVoiceSettings === 'function'")
        page.evaluate("""
          document.getElementById('startup-screen')?.setAttribute('style','display:none!important');
          document.getElementById('app-shell').style.display='grid';
          window.switchPanel('voice');
        """)
        page.wait_for_function(
            "document.getElementById('voice-setting-activation').value === 'push_to_talk'"
        )
        page.wait_for_function("""
          document.querySelector('#voice-setting-activation')
            ?.closest('.dark-select')
            ?.querySelector('.dark-select-text')
            ?.textContent.includes('Appuyer pour parler')
        """)

        page.locator("#voice-settings-save").click()
        page.wait_for_function(
            "document.getElementById('voice-settings-message').textContent.includes('redémarrée')"
        )
        assert any(method == "PUT" and path == "/api/config" for method, path, _ in calls)
        assert any(
            method == "POST" and path == "/api/voice/restart"
            for method, path, _ in calls
        )

        page.locator("#voice-ptt-button").click()
        page.wait_for_function(
            "document.getElementById('voice-settings-message').textContent.includes('parle maintenant')"
        )
        assert any(
            method == "POST" and path == "/api/voice/push-to-talk"
            for method, path, _ in calls
        )

        page.locator('[data-voice-tab="listen"]').click()
        page.locator('[data-action="testVoiceMicro"]').click()
        page.wait_for_function(
            "document.getElementById('voice-micro-result').textContent.includes('écoute active')"
        )
        assert any(
            method == "POST" and path == "/api/voice/test-micro"
            for method, path, _ in calls
        )

        page.locator('[data-action="stopVoiceAudio"]').click()
        page.wait_for_function(
            "document.getElementById('voice-control-message').textContent.includes('travail continue')"
        )
        assert any(
            method == "POST" and path == "/api/voice/stop-audio"
            for method, path, _ in calls
        )

        # A profile refreshes the Configuration panel from the same server state,
        # while an unrelated unsaved draft remains intact.
        page.evaluate("window.switchPanel('config')")
        page.wait_for_selector('[data-cfg="LUMENA_STT_MODEL"]')
        page.locator('[data-cfg="LUMENA_VOICE_WAKE_PHRASE"]').fill("Brouillon conservé")
        page.evaluate("window.switchPanel('voice')")
        page.locator('[data-voice-tab="conversation"]').click()
        page.wait_for_selector('[data-apply-voice-profile="balanced"]')
        page.locator('[data-apply-voice-profile="balanced"]').click()
        page.wait_for_function(
            "document.getElementById('voice-profile-message').textContent.includes('redémarrée')"
        )
        page.wait_for_function("""
          document.querySelector('[data-cfg="LUMENA_STT_MODEL"]')?.value === 'small'
          && document.querySelector('[data-cfg="LUMENA_VOICE_WAKE_PHRASE"]')?.value === 'Brouillon conservé'
        """)
        assert any(
            method == "POST" and path == "/api/voice/performance-profiles/balanced"
            for method, path, _ in calls
        )
        browser.close()


def test_push_to_talk_button_explains_wrong_activation_mode_without_request() -> None:
    source = (WEB / "static" / "js" / "api.js").read_text(encoding="utf-8")
    guard = source.index("if(selected!=='push_to_talk')")
    request = source.index("/api/voice/push-to-talk", guard)
    assert guard < request
    assert "Choisis « Appuyer pour parler »" in source[guard:request]
