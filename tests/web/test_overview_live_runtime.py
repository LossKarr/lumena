"""Opt-in Playwright canary against a running Lumena instance."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import pytest
from playwright.sync_api import expect, sync_playwright


ROOT = Path(__file__).resolve().parents[2]
PROOF = ROOT / "plans" / "overview-concepts" / "proof"
BASE_URL = os.getenv("LUMENA_LIVE_BASE_URL", "").rstrip("/")


@pytest.mark.skipif(not BASE_URL, reason="set LUMENA_LIVE_BASE_URL to run the live canary")
def test_overview_against_live_lumena_runtime() -> None:
    """Exercise the production shell and real read-only Overview endpoints."""
    PROOF.mkdir(parents=True, exist_ok=True)
    expected_paths = {
        "/api/status", "/api/models", "/api/missions", "/api/tasks",
        "/api/alerts", "/api/document-studio/library", "/api/trace/recent",
        "/api/providers", "/api/voice/status", "/api/mcp/health",
        "/api/mcp/observability/overview", "/api/peers", "/api/workspaces/serving",
        "/api/system/reliability", "/api/runtime/audit", "/api/daemon/activity",
        "/api/journal", "/api/sessions", "/api/hooks",
        "/api/training", "/api/finetuning/status",
    }
    observed: dict[str, int] = {}
    page_errors: list[str] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on("pageerror", lambda error: page_errors.append(str(error)))

        def record_response(response) -> None:
            parsed = urlparse(response.url)
            if parsed.path in expected_paths:
                observed[parsed.path] = response.status

        page.on("response", record_response)
        page.route(
            "https://unpkg.com/**",
            lambda route: route.fulfill(
                status=200,
                content_type="application/javascript",
                body="window.lucide={createIcons:function(){}};",
            ),
        )
        page.route("https://fonts.googleapis.com/**", lambda route: route.abort())
        page.route("https://fonts.gstatic.com/**", lambda route: route.abort())
        page.goto(f"{BASE_URL}/", wait_until="domcontentloaded", timeout=60_000)
        expect(page.locator("#startup-btn")).to_be_enabled(timeout=60_000)
        page.locator("#startup-btn").click()
        page.locator("#app-shell").wait_for(state="visible", timeout=60_000)

        page.locator(".nav-item[data-panel='overview']").click()
        page.locator("#panel-overview.active").wait_for(timeout=30_000)
        expect(page.locator("#ov-updated")).to_contain_text("Actualis", timeout=30_000)
        page.wait_for_timeout(900)

        assert expected_paths <= observed.keys(), expected_paths - observed.keys()
        assert all(observed[path] == 200 for path in expected_paths)
        assert "source(s) indisponible(s)" not in page.locator("#ov-updated").inner_text()
        assert page.locator("#ov-summary .overview-summary-item").count() == 4
        assert page.locator("#ov-systems .overview-system").count() == 12
        assert page.locator("#ov-capabilities .overview-capability").count() >= 40
        assert page.locator("#ov-sources .overview-source-row").count() == 21
        assert page.locator("#ov-core-nodes .overview-node").count() == 7
        assert not page.locator(".overview-core").evaluate("el => el.classList.contains('is-fallback')")
        assert page.evaluate("document.getElementById('ov-core-canvas').toDataURL().length") > 1500

        with page.expect_response(
            lambda response: urlparse(response.url).path == "/api/status" and response.status == 200,
            timeout=30_000,
        ):
            page.locator("#ov-refresh").click()
        page.locator("#ov-core-pause").click()
        expect(page.locator("#ov-core-pause")).to_have_attribute("aria-pressed", "true")
        page.locator("#ov-core-pause").click()
        expect(page.locator("#ov-core-pause")).to_have_attribute("aria-pressed", "false")

        page.locator(".overview-action[data-ov-panel='chat']").click()
        assert page.locator("#panel-chat").evaluate("el => el.classList.contains('active')")
        page.locator(".nav-item[data-panel='overview']").click()
        page.locator("#ov-customize").click()
        page.locator("#ov-density").click()
        assert page.locator(".overview-shell").evaluate("el => el.classList.contains('is-compact')")
        health_toggle = page.locator("[data-ov-toggle='health']")
        health_toggle.uncheck()
        assert page.locator("[data-ov-widget='health']").is_hidden()
        page.locator("#ov-layout-reset").click()
        assert page.locator("[data-ov-widget='health']").is_visible()
        page.locator("#ov-customize").click()

        health = page.locator("[data-ov-widget='health']")
        health.locator(".overview-drag").focus()
        for _ in range(3):
            page.keyboard.press("Alt+ArrowUp")
        order = page.locator("#ov-widget-grid > [data-ov-widget]").evaluate_all(
            "elements => elements.map(element => element.dataset.ovWidget)"
        )
        assert order.index("health") < 5
        page.locator("#ov-customize").click()
        page.locator("#ov-layout-reset").click()
        page.locator("#ov-customize").click()

        page.screenshot(path=str(PROOF / "overview-live-desktop.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        page.wait_for_timeout(350)
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(PROOF / "overview-live-mobile.png"), full_page=True)
        assert not page_errors, page_errors
        browser.close()
