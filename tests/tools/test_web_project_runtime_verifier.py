from pathlib import Path

import pytest

from src.tools.web_project_runtime_verifier import _looks_like_js_mime_error, verify_web_project_runtime


class _FakeConsoleMessage:
    type = "error"

    def __init__(self, text: str):
        self._text = text

    def text(self) -> str:
        return self._text


class _FakePage:
    def __init__(self):
        self.events = {}

    def on(self, name, callback):
        self.events[name] = callback

    async def wait_for_load_state(self, *_args, **_kwargs):
        return None

    async def wait_for_timeout(self, *_args, **_kwargs):
        return None

    async def evaluate(self, *_args, **_kwargs):
        return None


class _FakeBrowser:
    def __init__(self, dom, *, console_error: str = ""):
        self._page = _FakePage()
        self.dom = dom
        self.console_error = console_error
        self.stopped = False

    async def start(self):
        return True

    async def navigate(self, url, wait_until="domcontentloaded"):
        if self.console_error and "console" in self._page.events:
            self._page.events["console"](_FakeConsoleMessage(self.console_error))
        return {"success": True, "url": url, "title": "Test", "status": 200}

    async def evaluate(self, _script):
        return {"success": True, "result": dict(self.dom)}

    async def screenshot(self, filename=None, full_page=False):
        return {"success": True, "path": filename or "shot.png", "full_page": full_page}

    async def scroll(self, direction="down", amount=500):
        return {"success": True, "scrolled": direction}

    async def click_at(self, x, y):
        return {"success": True, "clicked_at": {"x": x, "y": y}}

    async def keyboard_press(self, key):
        return {"success": True, "key": key}

    async def get_metrics(self):
        return {"success": True, "metrics": {"dom_nodes": self.dom.get("dom_nodes", 0)}}

    async def stop(self):
        self.stopped = True


def _server_start(_path: Path, _port: int):
    return {"success": True, "url": "http://localhost:8765", "port": 8765}


def _server_stop():
    return {"success": True}


def _dom(**overrides):
    data = {
        "ready_state": "complete",
        "title": "Demo",
        "body_text_length": 42,
        "body_text_preview": "Demo ready",
        "dom_nodes": 18,
        "buttons": 1,
        "links": 0,
        "inputs": 0,
        "canvas_count": 1,
        "canvases": [{"width": 640, "height": 360}],
        "scroll_height": 900,
        "viewport": {"width": 1280, "height": 720},
        "loading_like": False,
    }
    data.update(overrides)
    return data


@pytest.mark.asyncio
async def test_runtime_verifier_passes_valid_project(tmp_path: Path):
    (tmp_path / "index.html").write_text("<!doctype html><canvas></canvas>", encoding="utf-8")

    result = await verify_web_project_runtime(
        tmp_path,
        expect_canvas=True,
        browser_factory=lambda: _FakeBrowser(_dom()),
        start_server_fn=_server_start,
        stop_server_fn=_server_stop,
    )

    assert result.passed is True
    assert result.errors == []
    assert "click:viewport_center:ok" in result.interactions
    assert result.screenshots


@pytest.mark.asyncio
async def test_runtime_verifier_fails_on_console_error(tmp_path: Path):
    (tmp_path / "index.html").write_text("<!doctype html><main>Demo</main>", encoding="utf-8")

    result = await verify_web_project_runtime(
        tmp_path,
        browser_factory=lambda: _FakeBrowser(_dom(canvas_count=0, canvases=[]), console_error="boom"),
        start_server_fn=_server_start,
        stop_server_fn=_server_stop,
    )

    assert result.passed is False
    assert any("console_error" in err and "boom" in err for err in result.errors)


@pytest.mark.asyncio
async def test_runtime_verifier_labels_preview_server_mime_error(tmp_path: Path):
    (tmp_path / "index.html").write_text("<!doctype html><script src='main.js'></script>", encoding="utf-8")

    mime_error = (
        "Refused to execute script from 'http://localhost:8765/main.js' because its MIME type "
        "('application/json') is not executable, and strict MIME type checking is enabled. "
        "Expected a JavaScript module script."
    )

    result = await verify_web_project_runtime(
        tmp_path,
        browser_factory=lambda: _FakeBrowser(_dom(canvas_count=0, canvases=[]), console_error=mime_error),
        start_server_fn=_server_start,
        stop_server_fn=_server_stop,
    )

    assert _looks_like_js_mime_error(mime_error)
    assert result.passed is False
    assert any(err.startswith("preview_server_mime_error:") for err in result.errors)
    assert not any(err.startswith("console_error:") for err in result.errors)


@pytest.mark.asyncio
async def test_runtime_verifier_fails_on_stuck_loading(tmp_path: Path):
    (tmp_path / "index.html").write_text("<!doctype html><main>Loading...</main>", encoding="utf-8")

    result = await verify_web_project_runtime(
        tmp_path,
        browser_factory=lambda: _FakeBrowser(_dom(
            body_text_length=10,
            body_text_preview="Loading...",
            canvas_count=0,
            canvases=[],
            loading_like=True,
        )),
        start_server_fn=_server_start,
        stop_server_fn=_server_stop,
    )

    assert result.passed is False
    assert "loading_screen_still_visible" in result.errors
