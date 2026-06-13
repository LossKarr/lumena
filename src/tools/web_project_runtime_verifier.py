"""Runtime verification for local web projects.

This module is intentionally small and independent from CodeAgent.  It lets
ReAct/Lumena reopen a project after CodeAgent has written it, exercise it with
Playwright, and return a compact proof report.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Optional

from loguru import logger


@dataclass
class WebRuntimeVerification:
    project_path: str
    url: str = ""
    passed: bool = False
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    dom: dict[str, Any] = field(default_factory=dict)
    interactions: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    duration_ms: float = 0.0

    def to_report(self, max_chars: int = 5000) -> str:
        status = "OK" if self.passed else "FAIL"
        lines = [
            f"## Runtime web verify: {status}",
            f"Project: {self.project_path}",
        ]
        if self.url:
            lines.append(f"URL: {self.url}")
        if self.screenshots:
            lines.append("Screenshots:")
            lines.extend(f"- {path}" for path in self.screenshots[:4])
        if self.dom:
            lines.append("DOM:")
            for key in (
                "ready_state", "title", "body_text_length", "dom_nodes",
                "buttons", "links", "inputs", "canvas_count",
                "scroll_height", "loading_like",
            ):
                if key in self.dom:
                    lines.append(f"- {key}: {self.dom.get(key)}")
            body_preview = str(self.dom.get("body_text_preview") or "").strip()
            if body_preview:
                lines.append(f"- visible_text: {body_preview[:500]}")
        if self.interactions:
            lines.append("Interactions:")
            lines.extend(f"- {item}" for item in self.interactions[:10])
        if self.metrics:
            lines.append("Metrics:")
            for key in ("dom_nodes", "resources_count", "first_contentful_paint_ms"):
                if key in self.metrics:
                    lines.append(f"- {key}: {self.metrics.get(key)}")
        if self.errors:
            lines.append("Errors:")
            lines.extend(f"- {err}" for err in self.errors[:20])
        if self.warnings:
            lines.append("Warnings:")
            lines.extend(f"- {warn}" for warn in self.warnings[:20])
        lines.append(f"Duration: {int(self.duration_ms)}ms")
        report = "\n".join(lines)
        if len(report) > max_chars:
            return report[: max_chars - 80] + "\n[... runtime report truncated ...]"
        return report


def looks_like_web_project(path: Path) -> bool:
    if not path or not path.exists() or not path.is_dir():
        return False
    if (path / "index.html").is_file():
        return True
    if (path / "package.json").is_file():
        return True
    try:
        return any(path.glob("*.html"))
    except Exception:
        return False


def _critical_request_failure(url: str) -> bool:
    lowered = (url or "").lower()
    if not lowered or lowered.endswith("/favicon.ico"):
        return False
    return any(lowered.split("?", 1)[0].endswith(ext) for ext in (".js", ".css", ".html", ".json", ".wasm"))


def _looks_like_js_mime_error(text: str) -> bool:
    lowered = (text or "").lower()
    return (
        "mime type" in lowered
        and "application/json" in lowered
        and ("javascript" in lowered or "text/javascript" in lowered)
        and (".js" in lowered or "script" in lowered)
    )


def _state_script() -> str:
    return """
() => {
  const body = document.body;
  const text = ((body && body.innerText) || '').trim();
  const canvases = Array.from(document.querySelectorAll('canvas')).map(c => {
    const r = c.getBoundingClientRect();
    return {
      width: c.width || Math.round(r.width),
      height: c.height || Math.round(r.height),
      visibleWidth: Math.round(r.width),
      visibleHeight: Math.round(r.height)
    };
  });
  const lower = text.toLowerCase();
  const loadingLike = (
    /^(loading|chargement|loading\\.\\.\\.|chargement\\.\\.\\.|100\\s*%)$/i.test(text) ||
    ((lower.includes('loading') || lower.includes('chargement')) && text.length < 280)
  );
  return {
    ready_state: document.readyState,
    title: document.title || '',
    body_text_length: text.length,
    body_text_preview: text.slice(0, 1200),
    dom_nodes: document.querySelectorAll('*').length,
    buttons: document.querySelectorAll('button,[role="button"]').length,
    links: document.querySelectorAll('a[href]').length,
    inputs: document.querySelectorAll('input,textarea,select').length,
    canvas_count: canvases.length,
    canvases,
    scroll_height: Math.max(document.body?.scrollHeight || 0, document.documentElement?.scrollHeight || 0),
    viewport: { width: window.innerWidth, height: window.innerHeight },
    loading_like: loadingLike
  };
}
"""


async def verify_web_project_runtime(
    project_path: str | Path,
    *,
    entry: str = "index.html",
    expect_canvas: bool = False,
    max_clicks: int = 3,
    timeout_ms: int = 30000,
    browser_factory: Optional[Callable[[], Any]] = None,
    start_server_fn: Optional[Callable[[Path, int], dict[str, Any]]] = None,
    stop_server_fn: Optional[Callable[[], dict[str, Any]]] = None,
) -> WebRuntimeVerification:
    """Open a local web project and verify runtime/browser health."""

    started = perf_counter()
    base = Path(project_path).resolve()
    result = WebRuntimeVerification(project_path=str(base))

    if not base.exists() or not base.is_dir():
        result.errors.append(f"project_dir_not_found: {base}")
        result.duration_ms = (perf_counter() - started) * 1000
        return result
    if not looks_like_web_project(base):
        result.errors.append(f"not_a_web_project: {base}")
        result.duration_ms = (perf_counter() - started) * 1000
        return result

    entry = (entry or "index.html").lstrip("/\\")
    if entry and not (base / entry).exists() and (base / "index.html").exists():
        entry = "index.html"

    browser = None
    try:
        if start_server_fn is None or stop_server_fn is None:
            from src.tools.website_builder import start_preview_server, stop_preview_server
            start_server_fn = start_server_fn or start_preview_server
            stop_server_fn = stop_server_fn or stop_preview_server

        server = start_server_fn(base, 8080)
        if not server.get("success"):
            result.errors.append(f"preview_server_failed: {server.get('error', 'unknown')}")
            return result

        await asyncio.sleep(0.6)
        base_url = str(server.get("url") or "").rstrip("/")
        result.url = f"{base_url}/{entry}" if entry else base_url

        if browser_factory is None:
            from src.tools.playwright_browser import PlaywrightBrowser
            browser_factory = lambda: PlaywrightBrowser(headless=True, profile_name=None)
        browser = browser_factory()
        if not await browser.start():
            result.errors.append("playwright_start_failed")
            return result

        page = getattr(browser, "_page", None)
        console_errors: list[str] = []
        page_errors: list[str] = []
        request_failures: list[str] = []

        if page is not None and hasattr(page, "on"):
            def _on_console(msg: Any) -> None:
                try:
                    msg_type = str(getattr(msg, "type", "") or "").lower()
                    msg_text = str(getattr(msg, "text", "") or "")
                    if callable(getattr(msg, "text", None)):
                        msg_text = str(msg.text())
                    if msg_type == "error":
                        console_errors.append(msg_text[:500])
                except Exception as exc:
                    console_errors.append(f"console_capture_error: {exc}")

            def _on_page_error(exc: Any) -> None:
                page_errors.append(str(exc)[:500])

            def _on_request_failed(req: Any) -> None:
                try:
                    req_url = str(getattr(req, "url", "") or "")
                    if _critical_request_failure(req_url):
                        request_failures.append(req_url[:500])
                except Exception:
                    pass

            page.on("console", _on_console)
            page.on("pageerror", _on_page_error)
            page.on("requestfailed", _on_request_failed)

        nav = await browser.navigate(result.url, wait_until="domcontentloaded")
        if not nav.get("success"):
            result.errors.append(f"navigate_failed: {nav.get('error', 'unknown')}")
            return result

        page = getattr(browser, "_page", None)
        if page is not None:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
                await page.wait_for_timeout(1000)
            except Exception as exc:
                result.warnings.append(f"load_wait_warning: {str(exc)[:180]}")

        state = await browser.evaluate(_state_script())
        if not state.get("success"):
            result.errors.append(f"dom_state_failed: {state.get('error', 'unknown')}")
            return result
        result.dom = dict(state.get("result") or {})

        shot = await browser.screenshot(full_page=False)
        if shot.get("success") and shot.get("path"):
            result.screenshots.append(str(shot["path"]))
        else:
            result.errors.append(f"screenshot_failed: {shot.get('error', 'unknown')}")

        dom_nodes = int(result.dom.get("dom_nodes") or 0)
        body_len = int(result.dom.get("body_text_length") or 0)
        canvas_count = int(result.dom.get("canvas_count") or 0)
        if dom_nodes < 5 and body_len == 0 and canvas_count == 0:
            result.errors.append("blank_or_empty_dom")
        if result.dom.get("loading_like"):
            result.errors.append("loading_screen_still_visible")
        if expect_canvas and canvas_count == 0:
            result.errors.append("expected_canvas_missing")

        for direction in ("bottom", "top"):
            scrolled = await browser.scroll(direction=direction, amount=700)
            if scrolled.get("success"):
                result.interactions.append(f"scroll:{direction}:ok")
            else:
                result.warnings.append(f"scroll:{direction}: {scrolled.get('error', 'unknown')}")

        clicks_done = 0
        canvases = result.dom.get("canvases") or []
        viewport = result.dom.get("viewport") or {}
        if canvases and max_clicks > 0:
            vw = int(viewport.get("width") or 1280)
            vh = int(viewport.get("height") or 720)
            clicked = await browser.click_at(max(1, vw // 2), max(1, vh // 2))
            if clicked.get("success"):
                result.interactions.append("click:viewport_center:ok")
                clicks_done += 1
            else:
                result.warnings.append(f"click:center: {clicked.get('error', 'unknown')}")

        if clicks_done < max_clicks and page is not None:
            try:
                await page.evaluate("document.body && document.body.click && document.body.click()")
                result.interactions.append("click:body:ok")
            except Exception as exc:
                result.warnings.append(f"click:body: {str(exc)[:160]}")

        for key in ("Space", "Escape", "KeyW"):
            try:
                pressed = await browser.keyboard_press(key)
                if pressed.get("success"):
                    result.interactions.append(f"keyboard:{key}:ok")
            except Exception as exc:
                result.warnings.append(f"keyboard:{key}: {str(exc)[:160]}")

        if page is not None:
            try:
                await page.wait_for_timeout(500)
            except Exception:
                pass
        shot2 = await browser.screenshot(filename="runtime_verify_after_interaction.png", full_page=False)
        if shot2.get("success") and shot2.get("path"):
            result.screenshots.append(str(shot2["path"]))

        try:
            metrics = await browser.get_metrics()
            if metrics.get("success"):
                result.metrics = dict(metrics.get("metrics") or metrics)
        except Exception as exc:
            result.warnings.append(f"metrics_unavailable: {str(exc)[:160]}")

        for item in console_errors:
            if item:
                if _looks_like_js_mime_error(item):
                    result.errors.append(f"preview_server_mime_error: {item}")
                else:
                    result.errors.append(f"console_error: {item}")
        for item in page_errors:
            if item:
                result.errors.append(f"page_error: {item}")
        for item in request_failures:
            if item:
                result.errors.append(f"request_failed: {item}")

        result.passed = not result.errors
        return result

    except Exception as exc:
        logger.debug("[web_runtime_verify] exception: {}", exc)
        result.errors.append(f"runtime_verify_exception: {type(exc).__name__}: {str(exc)[:240]}")
        return result
    finally:
        result.duration_ms = (perf_counter() - started) * 1000
        if browser is not None:
            try:
                await browser.stop()
            except Exception:
                pass
        if stop_server_fn is not None:
            try:
                stop_server_fn()
            except Exception:
                pass


__all__ = [
    "WebRuntimeVerification",
    "looks_like_web_project",
    "verify_web_project_runtime",
]
