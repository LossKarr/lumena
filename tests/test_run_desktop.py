import run_desktop


class FakeWindow:
    def __init__(self):
        self.scripts = []

    def evaluate_js(self, script):
        self.scripts.append(script)


def test_desktop_zoom_default(monkeypatch):
    monkeypatch.delenv("LUMENA_DESKTOP_ZOOM", raising=False)
    assert run_desktop._desktop_zoom() == 0.90


def test_desktop_zoom_clamped(monkeypatch):
    monkeypatch.setenv("LUMENA_DESKTOP_ZOOM", "2")
    assert run_desktop._desktop_zoom() == 1.25

    monkeypatch.setenv("LUMENA_DESKTOP_ZOOM", "0.1")
    assert run_desktop._desktop_zoom() == 0.67


def test_desktop_zoom_invalid_falls_back(monkeypatch):
    monkeypatch.setenv("LUMENA_DESKTOP_ZOOM", "nope")
    assert run_desktop._desktop_zoom() == 0.90


def test_apply_desktop_zoom_uses_compensated_scale(monkeypatch):
    monkeypatch.setenv("LUMENA_DESKTOP_ZOOM", "0.9")
    window = FakeWindow()

    run_desktop._apply_desktop_zoom(window)

    assert len(window.scripts) == 1
    script = window.scripts[0]
    assert 'style.removeProperty("zoom")' in script
    assert ".style.zoom =" not in script
    assert "body.style.transform" in script
    assert "100 / zoom" in script
    assert ".shell" in script
    assert "calc(100vh / \" + zoom + \")" in script
