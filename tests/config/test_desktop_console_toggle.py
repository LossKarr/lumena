from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_desktop_console_toggle_schema():
    from web.routes.config import _CONFIG_SCHEMA

    schema = {entry["key"]: entry for entry in _CONFIG_SCHEMA}
    entry = schema["LUMENA_DESKTOP_SHOW_CONSOLE"]

    assert entry["group"] == "Interface"
    assert entry["type"] == "bool"
    assert entry["default"] == "0"
    assert entry["restart"] is True


def test_desktop_splash_schema():
    from web.routes.config import _CONFIG_SCHEMA

    schema = {entry["key"]: entry for entry in _CONFIG_SCHEMA}
    entry = schema["LUMENA_DESKTOP_SPLASH"]

    assert entry["group"] == "Interface"
    assert entry["type"] == "bool"
    assert entry["default"] == "1"
    assert entry["restart"] is True


def test_start_desktop_hides_console_by_default_but_keeps_visible_mode():
    text = (ROOT / "START_DESKTOP.bat").read_text(encoding="utf-8")

    assert "LUMENA_DESKTOP_SHOW_CONSOLE" in text
    assert 'set "LUMENA_DESKTOP_SHOW_CONSOLE=0"' in text
    assert "Start-Process" in text
    assert "-WindowStyle Hidden" in text
    assert "_RUNNING_HIDDEN" in text
    assert "_RUNNING_VISIBLE" in text
    assert 'python run_desktop.py' in text


def test_debug_launcher_forces_visible_console():
    text = (ROOT / "START_DESKTOP_DEBUG.bat").read_text(encoding="utf-8")

    assert 'set "LUMENA_DESKTOP_SHOW_CONSOLE=1"' in text
    assert "START_DESKTOP.bat" in text
    assert "_RUNNING_VISIBLE" in text
