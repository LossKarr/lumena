import run_desktop
import urllib.error


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


def test_desktop_splash_enabled_default(monkeypatch):
    monkeypatch.delenv("LUMENA_DESKTOP_SPLASH", raising=False)
    assert run_desktop._desktop_splash_enabled() is True


def test_desktop_splash_can_be_disabled(monkeypatch):
    monkeypatch.setenv("LUMENA_DESKTOP_SPLASH", "0")
    assert run_desktop._desktop_splash_enabled() is False
    assert run_desktop._create_desktop_splash() is None


def test_create_desktop_splash_noops_when_tkinter_unavailable(monkeypatch):
    import builtins

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "tkinter":
            raise ImportError("tk unavailable")
        return original_import(name, *args, **kwargs)

    monkeypatch.setenv("LUMENA_DESKTOP_SPLASH", "1")
    monkeypatch.setattr(builtins, "__import__", fake_import)

    assert run_desktop._create_desktop_splash() is None


def test_wait_for_server_pumps_splash_tick(monkeypatch):
    ticks = []
    monotonic_values = iter([0.0, 0.1, 0.7])

    monkeypatch.setattr(run_desktop.time, "monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(run_desktop.time, "sleep", lambda _seconds: None)

    import urllib.request

    def fail_urlopen(*_args, **_kwargs):
        raise urllib.error.URLError("not ready")

    monkeypatch.setattr(urllib.request, "urlopen", fail_urlopen)

    assert run_desktop._wait_for_server(8080, timeout=0.2, tick=lambda: ticks.append("tick")) is False
    assert ticks


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
