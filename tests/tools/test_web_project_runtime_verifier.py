from pathlib import Path

import pytest

from src.tools.web_project_runtime_verifier import (
    _critical_http_response,
    _looks_like_js_mime_error,
    _observable_state_changed,
    looks_like_web_project,
    verify_web_project_runtime,
)


def test_static_index_is_recognized_as_web_project(tmp_path: Path):
    (tmp_path / "static").mkdir()
    (tmp_path / "static" / "index.html").write_text("<main>App</main>", encoding="utf-8")
    assert looks_like_web_project(tmp_path) is True


class _FakeConsoleMessage:
    type = "error"

    def __init__(self, text: str):
        self._text = text

    def text(self) -> str:
        return self._text


class _FakeRequest:
    def __init__(self, method="GET"):
        self.method = method


class _FakeResponse:
    def __init__(self, url, status, method="GET"):
        self.url = url
        self.status = status
        self.request = _FakeRequest(method)


class _FakeControl:
    def __init__(self, page, kind, attrs=None, text=""):
        self.page = page
        self.kind = kind
        self.attrs = dict(attrs or {})
        self.text = text
        self.value = ""

    async def is_visible(self):
        return True

    async def get_attribute(self, name):
        return self.attrs.get(name)

    async def fill(self, value):
        self.value = value

    async def inner_text(self):
        return self.text

    async def click(self):
        callback = self.page.events.get("response")
        if callback and self.page.response_status:
            callback(_FakeResponse(
                "http://localhost:8765/api/empreinte",
                self.page.response_status,
                "POST",
            ))


class _FakeLocator:
    def __init__(self, controls=None):
        self.controls = list(controls or [])

    async def count(self):
        return len(self.controls)

    def nth(self, index):
        return self.controls[index]


class _FakePage:
    def __init__(self, *, form=False, response_status=0, field_specs=None):
        self.events = {}
        self.response_status = response_status
        self.fields = []
        self.buttons = []
        if form:
            specs = field_specs or [{"type": "number", "id": "distance"}]
            for attrs in specs:
                self.fields.append(_FakeControl(self, "input", attrs))
            self.buttons.append(_FakeControl(
                self, "button", {"id": "comparer"}, "Comparer",
            ))

    def on(self, name, callback):
        self.events[name] = callback

    async def wait_for_load_state(self, *_args, **_kwargs):
        return None

    async def wait_for_timeout(self, *_args, **_kwargs):
        return None

    async def evaluate(self, *_args, **_kwargs):
        return None

    def locator(self, selector):
        if selector.startswith("input:not"):
            return _FakeLocator(self.fields)
        if selector.startswith("select"):
            return _FakeLocator()
        if selector.startswith("button"):
            return _FakeLocator(self.buttons)
        return _FakeLocator()


class _FakeBrowser:
    def __init__(self, dom, *, dom_after=None, console_error: str = "", form=False,
                 response_status=0, field_specs=None):
        self._page = _FakePage(
            form=form, response_status=response_status, field_specs=field_specs
        )
        self.dom = dom
        self.dom_after = dom_after
        self.evaluate_calls = 0
        self.console_error = console_error
        self.stopped = False

    async def start(self):
        return True

    async def navigate(self, url, wait_until="domcontentloaded"):
        if self.console_error and "console" in self._page.events:
            self._page.events["console"](_FakeConsoleMessage(self.console_error))
        return {"success": True, "url": url, "title": "Test", "status": 200}

    async def evaluate(self, _script):
        self.evaluate_calls += 1
        state = self.dom_after if self.evaluate_calls > 1 and self.dom_after else self.dom
        return {"success": True, "result": dict(state)}

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


def test_http_error_detection_is_local_and_ignores_favicon():
    base = "http://localhost:8765"
    assert _critical_http_response(f"{base}/api/empreinte", 405, base)
    assert _critical_http_response(f"{base}/missing.css", 404, base)
    assert not _critical_http_response(f"{base}/favicon.ico", 404, base)
    assert not _critical_http_response("https://cdn.example.test/a.js", 500, base)
    assert not _critical_http_response(f"{base}/api/ok", 200, base)


def test_observable_state_change_ignores_identical_form_submission():
    before = _dom(body_text_preview="Total 5029", body_text_length=10, inputs=2)
    assert _observable_state_changed(before, dict(before)) is False
    after = _dom(body_text_preview="Total 5204", body_text_length=10, inputs=2)
    assert _observable_state_changed(before, after) is True


@pytest.mark.asyncio
async def test_runtime_verifier_submits_form_and_fails_on_http_405(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><input id='distance' type='number'><button>Comparer</button>",
        encoding="utf-8",
    )
    browser = _FakeBrowser(
        _dom(inputs=1, canvas_count=0, canvases=[]),
        form=True,
        response_status=405,
    )

    result = await verify_web_project_runtime(
        tmp_path,
        browser_factory=lambda: browser,
        start_server_fn=_server_start,
        stop_server_fn=_server_stop,
    )

    assert result.passed is False
    assert "form:fill:distance" in result.interactions
    assert any(item.startswith("form:submit:comparer") for item in result.interactions)
    assert any("http_405: POST" in err and "/api/empreinte" in err for err in result.errors)


@pytest.mark.asyncio
async def test_runtime_verifier_accepts_exercised_form_with_http_200(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><input id='distance' type='number'><button>Comparer</button>",
        encoding="utf-8",
    )
    result = await verify_web_project_runtime(
        tmp_path,
        browser_factory=lambda: _FakeBrowser(
            _dom(inputs=1, canvas_count=0, canvases=[]),
            dom_after=_dom(
                inputs=1, canvas_count=0, canvases=[],
                body_text_preview="Demo ready - resultat 100",
                body_text_length=25,
            ),
            form=True,
            response_status=200,
        ),
        start_server_fn=_server_start,
        stop_server_fn=_server_stop,
    )

    assert result.passed is True
    assert "form:fill:distance" in result.interactions
    assert any(item.startswith("form:submit:comparer") for item in result.interactions)
    assert not any(item.startswith("http_") for item in result.errors)


@pytest.mark.asyncio
async def test_runtime_verifier_rejects_submit_without_observable_change(tmp_path: Path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><input id='litres' type='number'><input id='date' type='date'>"
        "<button>Ajouter</button>",
        encoding="utf-8",
    )
    browser = _FakeBrowser(
        _dom(inputs=2, canvas_count=0, canvases=[], body_text_preview="Total 5029"),
        form=True,
        response_status=200,
        field_specs=[
            {"type": "number", "id": "litres"},
            {"type": "date", "id": "date"},
        ],
    )

    result = await verify_web_project_runtime(
        tmp_path,
        browser_factory=lambda: browser,
        start_server_fn=_server_start,
        stop_server_fn=_server_stop,
    )

    assert result.passed is False
    assert "form:fill:litres" in result.interactions
    assert "form:fill:date" in result.interactions
    assert browser._page.fields[1].value == "2026-01-15"
    assert "interactive_form_no_observable_change" in result.errors
