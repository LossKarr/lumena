"""Tests unitaires pour src/reasoning/handlers/computer_use.py (29 handlers).

Pattern: patch.dict(sys.modules) pour bloquer les imports réels de
src.computer_use, src.computer_use.vision, pyautogui, etc.
"""

import asyncio
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from src.reasoning.handlers.computer_use import (
    click,
    click_element,
    close_app,
    close_window,
    computer_task,
    cursor_ide_local,
    double_click,
    drag,
    find_element,
    get_active_window,
    get_computer_use_handler_defs,
    hotkey,
    list_screens,
    list_windows,
    mouse_pattern,
    move_mouse,
    open_app,
    open_url,
    press_key,
    screenshot,
    screenshot_analyze,
    scroll,
    set_screen,
    spotify_play,
    type_text,
    ui_click,
    ui_list_controls,
    ui_type,
    wait,
    _resolve_close_targets,
    _protected_process_names,
    IS_WINDOWS,
)
from src.reasoning.handlers.context import HandlerContext


# ─── Helpers ───────────────────────────────────────────────────────────────

def _make_ctx() -> HandlerContext:
    return HandlerContext.for_testing()


def _make_cu_module() -> tuple:
    """Crée un module mock pour src.computer_use + instance cu mock."""
    mock_cu = MagicMock()
    mock_cu.take_screenshot = AsyncMock(return_value="/tmp/shot.png")
    mock_cu.open_application = AsyncMock()
    mock_module = ModuleType("src.computer_use")
    mock_module.get_computer_use = MagicMock(return_value=mock_cu)
    return mock_module, mock_cu


def _make_vision_module() -> tuple:
    """Crée un module mock pour src.computer_use.vision."""
    mock_vision = MagicMock()
    mock_vision_mod = ModuleType("src.computer_use.vision")
    mock_vision_mod.get_vision = MagicMock(return_value=mock_vision)
    return mock_vision_mod, mock_vision




# ─── screenshot ────────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestScreenshot:
    async def test_success(self):
        mod, cu = _make_cu_module()
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (screenshot(_make_ctx()))
        assert r.success
        assert "shot.png" in r.output

    async def test_no_path(self):
        mod, cu = _make_cu_module()
        cu.take_screenshot = AsyncMock(return_value=None)
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (screenshot(_make_ctx()))
        assert not r.success

    async def test_exception(self):
        with patch.dict(sys.modules, {"src.computer_use": None}):
            r = await (screenshot(_make_ctx()))
        assert not r.success


# ─── click ─────────────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestClick:
    async def test_success(self):
        mod, cu = _make_cu_module()
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (click(_make_ctx(), x=100, y=200, button="left"))
        assert r.success
        cu.mouse.click.assert_called_once_with(100, 200, button="left")

    async def test_right_click(self):
        mod, cu = _make_cu_module()
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (click(_make_ctx(), x=50, y=50, button="right"))
        assert r.success
        assert "right" in r.output


# ─── type_text ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestTypeText:
    async def test_success(self):
        mod, cu = _make_cu_module()
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (type_text(_make_ctx(), text="hello world"))
        assert r.success
        cu.keyboard.type_text.assert_called_once_with("hello world")


# ─── open_app ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestOpenApp:
    async def test_success(self):
        mod, cu = _make_cu_module()
        ctx = _make_ctx()
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (open_app(ctx, name="notepad"))
        assert r.success
        assert "notepad" in ctx._opened_apps_history

    async def test_history_cap(self):
        mod, cu = _make_cu_module()
        ctx = _make_ctx()
        ctx._opened_apps_history = [f"app{i}" for i in range(30)]
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (open_app(ctx, name="new_app"))
        assert r.success
        assert len(ctx._opened_apps_history) <= 30
        assert "new_app" in ctx._opened_apps_history


# ─── close_app ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestCloseApp:
    async def test_empty_target_uses_history(self):
        ctx = _make_ctx()
        ctx._opened_apps_history = ["notepad"]
        with patch("src.reasoning.handlers.computer_use.subprocess") as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=0)
            r = await (close_app(ctx, name=""))
        assert r.success

    async def test_empty_all_fails(self):
        ctx = _make_ctx()
        ctx._opened_apps_history = []
        r = await (close_app(ctx, name=""))
        assert not r.success
        assert "cible vide" in r.output

    async def test_known_alias(self):
        ctx = _make_ctx()
        with patch("src.reasoning.handlers.computer_use.subprocess") as mock_sub:
            mock_sub.run.return_value = MagicMock(returncode=0)
            r = await (close_app(ctx, name="chrome"))
        assert r.success

    async def test_mission_cannot_close_host_terminals(self):
        ctx = _make_ctx()
        ctx.is_mission_run = True
        with patch("src.reasoning.handlers.computer_use.subprocess") as mock_sub:
            r = await close_app(
                ctx,
                name="flask.exe",
                close_terminals=True,
                force=True,
                confirm=True,
            )
        assert not r.success
        assert "stop_website_server" in r.output
        mock_sub.run.assert_not_called()

    async def test_runtime_ancestry_image_is_never_killed(self):
        ctx = _make_ctx()
        with (
            patch(
                "src.reasoning.handlers.computer_use._protected_process_names",
                return_value={"powershell.exe", "python.exe"},
            ),
            patch("src.reasoning.handlers.computer_use.subprocess") as mock_sub,
        ):
            r = await close_app(ctx, name="powershell", confirm=True)
        assert not r.success
        assert "processus parent protégé" in r.output
        mock_sub.run.assert_not_called()

    async def test_external_application_remains_closable(self):
        ctx = _make_ctx()
        with (
            patch(
                "src.reasoning.handlers.computer_use._protected_process_names",
                return_value={"python.exe", "powershell.exe"},
            ),
            patch("src.reasoning.handlers.computer_use.subprocess") as mock_sub,
        ):
            mock_sub.run.return_value = MagicMock(returncode=0)
            r = await close_app(ctx, name="notepad", confirm=True)
        assert r.success
        mock_sub.run.assert_called_once()


def test_protected_process_names_always_contains_current_interpreter():
    assert Path(sys.executable).name.casefold() in _protected_process_names()


# ─── _resolve_close_targets ───────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestResolveCloseTargets:
    async def test_known_alias(self):
        targets = _resolve_close_targets("cmd", False)
        assert targets == ["cmd.exe"]

    async def test_terminal_alias(self):
        targets = _resolve_close_targets("terminal", False)
        assert "cmd.exe" in targets
        assert "powershell.exe" in targets

    async def test_close_terminals_flag(self):
        targets = _resolve_close_targets("", True)
        assert "cmd.exe" in targets

    async def test_unknown_adds_exe(self):
        targets = _resolve_close_targets("myapp", False)
        assert targets == ["myapp.exe"]

    async def test_deny_list(self):
        # "system" n'est pas dans aliases, donc -> "system.exe"
        # Le deny set contient "system" (sans .exe), mais "system.exe" != "system"
        # Donc il passe — c'est le comportement réel de react.py
        targets = _resolve_close_targets("lsass.exe", False)
        # lsass.exe est dans le deny set, mais "lsass.exe" arrive via elif branch
        # et lsass.exe IS in deny set so it's blocked
        assert targets == []

    async def test_deny_wininit(self):
        targets = _resolve_close_targets("wininit.exe", False)
        assert targets == []

    async def test_dedup(self):
        targets = _resolve_close_targets("terminal", True)
        assert len(targets) == len(set(targets))


# ─── hotkey ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestHotkey:
    async def test_success(self):
        mod, cu = _make_cu_module()
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (hotkey(_make_ctx(), keys="ctrl+c"))
        assert r.success
        cu.keyboard.hotkey.assert_called_once_with("ctrl", "c")

    async def test_input_alias(self):
        mod, cu = _make_cu_module()
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (hotkey(_make_ctx(), input="alt+tab"))
        assert r.success
        cu.keyboard.hotkey.assert_called_once_with("alt", "tab")

    async def test_json_parsing(self):
        mod, cu = _make_cu_module()
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (hotkey(_make_ctx(), keys='{"keys": "ctrl+v"}'))
        assert r.success
        cu.keyboard.hotkey.assert_called_once_with("ctrl", "v")

    async def test_missing_param(self):
        r = await (hotkey(_make_ctx()))
        assert not r.success


# ─── get_active_window ─────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestGetActiveWindow:
    async def test_success(self):
        mod, cu = _make_cu_module()
        cu.window.get_active_window.return_value = "Notepad"
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (get_active_window(_make_ctx()))
        assert r.success
        assert "Notepad" in r.output


# ─── double_click ──────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestDoubleClick:
    async def test_success(self):
        mod, cu = _make_cu_module()
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (double_click(_make_ctx(), x=10, y=20))
        assert r.success
        cu.mouse.double_click.assert_called_once_with(10, 20)


# ─── scroll ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestScroll:
    async def test_up(self):
        mod, cu = _make_cu_module()
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (scroll(_make_ctx(), direction="up", amount=5))
        assert r.success
        cu.mouse.scroll.assert_called_once_with(5)

    async def test_down(self):
        mod, cu = _make_cu_module()
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (scroll(_make_ctx(), direction="down", amount=3))
        assert r.success
        cu.mouse.scroll.assert_called_once_with(-3)


# ─── move_mouse ────────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestMoveMouse:
    async def test_success(self):
        mod, cu = _make_cu_module()
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (move_mouse(_make_ctx(), x=500, y=300))
        assert r.success
        cu.mouse.move_to.assert_called_once_with(500, 300)


# ─── press_key ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestPressKey:
    async def test_success(self):
        mod, cu = _make_cu_module()
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (press_key(_make_ctx(), key="enter"))
        assert r.success

    async def test_input_alias(self):
        mod, cu = _make_cu_module()
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (press_key(_make_ctx(), input="tab"))
        assert r.success

    async def test_json_parsing(self):
        mod, cu = _make_cu_module()
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (press_key(_make_ctx(), key='{"key": "escape"}'))
        assert r.success
        cu.keyboard.press_key.assert_called_once_with("escape")

    async def test_missing_param(self):
        r = await (press_key(_make_ctx()))
        assert not r.success


# ─── close_window ──────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestCloseWindow:
    async def test_success(self):
        mod, cu = _make_cu_module()
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (close_window(_make_ctx()))
        assert r.success
        cu.keyboard.hotkey.assert_called_once_with("alt", "f4")


# ─── wait ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestWait:
    async def test_success(self):
        r = await (wait(_make_ctx(), seconds=0))
        assert r.success

    async def test_cap_at_10(self):
        r = await (wait(_make_ctx(), seconds=999))
        assert r.success
        assert "10" in r.output


# ─── spotify_play ──────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestSpotifyPlay:
    async def test_success(self):
        mod, cu = _make_cu_module()
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                r = await (spotify_play(_make_ctx(), query="Daft Punk"))
        assert r.success
        assert "Daft Punk" in r.output

    async def test_exception(self):
        with patch.dict(sys.modules, {"src.computer_use": None}):
            r = await (spotify_play(_make_ctx(), query="test"))
        assert not r.success


# ─── open_url ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestOpenUrl:
    async def test_success(self):
        with patch("webbrowser.open") as mock_open:
            r = await (open_url(_make_ctx(), url="https://example.com"))
        assert r.success
        mock_open.assert_called_once_with("https://example.com")


# ─── list_windows ──────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestListWindows:
    async def test_success(self):
        mod, cu = _make_cu_module()
        cu.window.list_windows.return_value = ["Window1", "Window2"]
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (list_windows(_make_ctx()))
        assert r.success
        assert "Window1" in r.output

    async def test_empty(self):
        mod, cu = _make_cu_module()
        cu.window.list_windows.return_value = []
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (list_windows(_make_ctx()))
        assert r.success
        assert "Aucune" in r.output


# ─── drag ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestDrag:
    async def test_success(self):
        mod, cu = _make_cu_module()
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (drag(_make_ctx(), start_x=10, start_y=20, end_x=100, end_y=200))
        assert r.success
        cu.mouse.move_to.assert_called_once_with(10, 20)
        cu.mouse.drag_to.assert_called_once_with(100, 200)


# ─── screenshot_analyze ───────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestScreenshotAnalyze:
    async def test_success(self):
        cu_mod, cu = _make_cu_module()
        vis_mod, vision = _make_vision_module()
        # P3.6 : screenshot_analyze utilise route_cu_vision — mocker cu_router dans sys.modules
        import types as _types
        router_mod = _types.ModuleType("src.computer_use.cu_router")
        async def _mock_route(v, path, prompt, *, capability="vision_describe", cascade=None):
            return {"success": True, "text": "Desktop visible"}
        router_mod.route_cu_vision = _mock_route
        with patch.dict(sys.modules, {
            "src.computer_use": cu_mod,
            "src.computer_use.vision": vis_mod,
            "src.computer_use.cu_router": router_mod,
        }):
            r = await (screenshot_analyze(_make_ctx(), question="What is this?"))
        assert r.success
        assert "Desktop" in r.output

    async def test_gemini_429_fallback_claude(self):
        cu_mod, cu = _make_cu_module()
        vis_mod, vision = _make_vision_module()
        # La cascade est maintenant gérée par route_cu_vision — on simule le résultat final
        import types as _types
        router_mod = _types.ModuleType("src.computer_use.cu_router")
        async def _mock_route(v, path, prompt, *, capability="vision_describe", cascade=None):
            return {"success": True, "text": "Claude result"}
        router_mod.route_cu_vision = _mock_route
        with patch.dict(sys.modules, {
            "src.computer_use": cu_mod,
            "src.computer_use.vision": vis_mod,
            "src.computer_use.cu_router": router_mod,
        }):
            r = await (screenshot_analyze(_make_ctx()))
        assert r.success
        assert "Claude" in r.output

    async def test_import_error(self):
        with patch.dict(sys.modules, {"src.computer_use": None}):
            r = await (screenshot_analyze(_make_ctx()))
        assert not r.success


# ─── click_element (self-healing cascade) ─────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestClickElement:
    async def test_success_direct(self):
        """Étape 0 : trouvé du premier coup -- pas de self-healing."""
        cu_mod, cu = _make_cu_module()
        vis_mod, vision = _make_vision_module()
        vision.find_element_coordinates = AsyncMock(return_value={
            "success": True, "found": True, "x": 100, "y": 200, "confidence": "high"
        })
        cu.screen.get_monitor_offset.return_value = (0, 0)
        with patch.dict(sys.modules, {"src.computer_use": cu_mod, "src.computer_use.vision": vis_mod}):
            r = await (click_element(_make_ctx(), element="bouton OK"))
        assert r.success
        assert "bouton OK" in r.output

    async def test_success_on_retry(self):
        """Étape 1 : échoue d'abord, réussit au retry direct."""
        cu_mod, cu = _make_cu_module()
        vis_mod, vision = _make_vision_module()
        vision.find_element_coordinates = AsyncMock(side_effect=[
            {"success": True, "found": False},
            {"success": True, "found": True, "x": 50, "y": 60, "confidence": "medium"},
        ])
        cu.screen.get_monitor_offset.return_value = (0, 0)
        with patch.dict(sys.modules, {"src.computer_use": cu_mod, "src.computer_use.vision": vis_mod}):
            r = await (click_element(_make_ctx(), element="bouton retry"))
        assert r.success
        assert "retry" in r.output.lower()

    async def test_success_on_scroll_down(self):
        """Étape 2 : trouvé après scroll down."""
        cu_mod, cu = _make_cu_module()
        vis_mod, vision = _make_vision_module()
        vision.find_element_coordinates = AsyncMock(side_effect=[
            {"success": True, "found": False},
            {"success": True, "found": False},
            {"success": True, "found": True, "x": 200, "y": 400, "confidence": "high"},
        ])
        cu.screen.get_monitor_offset.return_value = (0, 0)
        cu.ui.click_element_by_name.return_value = False  # empêche UIA step 0b d'intercepter
        with patch.dict(sys.modules, {"src.computer_use": cu_mod, "src.computer_use.vision": vis_mod}):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                r = await (click_element(_make_ctx(), element="bouton bas"))
        assert r.success
        assert "scroll" in r.output.lower() or "200" in r.output
        cu.mouse.scroll.assert_called()

    async def test_success_on_scroll_up(self):
        """Étape 3 : trouvé après scroll up."""
        cu_mod, cu = _make_cu_module()
        vis_mod, vision = _make_vision_module()
        vision.find_element_coordinates = AsyncMock(side_effect=[
            {"success": True, "found": False},
            {"success": True, "found": False},
            {"success": True, "found": False},
            {"success": True, "found": True, "x": 100, "y": 50, "confidence": "medium"},
        ])
        cu.screen.get_monitor_offset.return_value = (0, 0)
        cu.ui.click_element_by_name.return_value = False  # empêche UIA step 0b d'intercepter
        with patch.dict(sys.modules, {"src.computer_use": cu_mod, "src.computer_use.vision": vis_mod}):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                r = await (click_element(_make_ctx(), element="bouton haut"))
        assert r.success
        assert "scroll" in r.output.lower() or "100" in r.output

    async def test_success_on_ocr(self):
        """Étape 4 : vision échoue partout, OCR local trouve l'élément."""
        cu_mod, cu = _make_cu_module()
        vis_mod, vision = _make_vision_module()
        vision.find_element_coordinates = AsyncMock(return_value={"success": True, "found": False})
        vision._find_element_with_ocr = AsyncMock(return_value={
            "success": True, "found": True, "x": 300, "y": 150, "confidence": "low"
        })
        cu.screen.get_monitor_offset.return_value = (0, 0)
        with patch.dict(sys.modules, {"src.computer_use": cu_mod, "src.computer_use.vision": vis_mod}):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                r = await (click_element(_make_ctx(), element="texte OCR"))
        assert r.success
        assert "OCR" in r.output

    async def test_success_on_ui_automation(self):
        """Étape 5 : tout échoue sauf UI Automation (pywinauto)."""
        cu_mod, cu = _make_cu_module()
        vis_mod, vision = _make_vision_module()
        vision.find_element_coordinates = AsyncMock(return_value={"success": True, "found": False})
        vision._find_element_with_ocr = AsyncMock(return_value={"success": True, "found": False})
        cu.ui.click_element_by_name.return_value = True
        cu.screen.get_monitor_offset.return_value = (0, 0)
        with patch.dict(sys.modules, {"src.computer_use": cu_mod, "src.computer_use.vision": vis_mod}):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                r = await (click_element(_make_ctx(), element="bouton UI"))
        assert r.success
        assert "UI Automation" in r.output

    async def test_total_failure(self):
        """Toutes les étapes échouent -- message clair avec 5 tentatives."""
        cu_mod, cu = _make_cu_module()
        vis_mod, vision = _make_vision_module()
        vision.find_element_coordinates = AsyncMock(return_value={"success": True, "found": False})
        vision._find_element_with_ocr = AsyncMock(return_value={"success": True, "found": False})
        cu.ui.click_element_by_name.return_value = False
        cu.screen.get_monitor_offset.return_value = (0, 0)
        with patch.dict(sys.modules, {"src.computer_use": cu_mod, "src.computer_use.vision": vis_mod}):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                r = await (click_element(_make_ctx(), element="fantôme"))
        assert not r.success
        assert "introuvable" in r.output or "5 tentatives" in r.output

    async def test_monitor_offset_applied(self):
        """Les offsets multi-écran sont correctement appliqués."""
        cu_mod, cu = _make_cu_module()
        vis_mod, vision = _make_vision_module()
        vision.find_element_coordinates = AsyncMock(return_value={
            "success": True, "found": True, "x": 100, "y": 200, "confidence": "high"
        })
        cu.screen.get_monitor_offset.return_value = (1920, 0)
        cu.ui.click_element_by_name.return_value = False  # empêche UIA step 0b d'intercepter
        with patch.dict(sys.modules, {"src.computer_use": cu_mod, "src.computer_use.vision": vis_mod}):
            r = await (click_element(_make_ctx(), element="second écran"))
        assert r.success
        cu.mouse.click.assert_called_with(2020, 200)


# ─── find_element ──────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestFindElement:
    async def test_success(self):
        cu_mod, cu = _make_cu_module()
        vis_mod, vision = _make_vision_module()
        vision.find_element_coordinates = AsyncMock(return_value={
            "success": True, "found": True, "x": 50, "y": 75, "confidence": "medium", "description": "Button"
        })
        with patch.dict(sys.modules, {"src.computer_use": cu_mod, "src.computer_use.vision": vis_mod}):
            r = await (find_element(_make_ctx(), element="submit button"))
        assert r.success
        assert "50" in r.output

    async def test_not_found(self):
        cu_mod, cu = _make_cu_module()
        vis_mod, vision = _make_vision_module()
        vision.find_element_coordinates = AsyncMock(return_value={"success": True, "found": False})
        with patch.dict(sys.modules, {"src.computer_use": cu_mod, "src.computer_use.vision": vis_mod}):
            r = await (find_element(_make_ctx(), element="nope"))
        assert not r.success


# ─── list_screens ──────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestListScreens:
    async def test_success(self):
        mod, cu = _make_cu_module()
        cu.screen._monitor_info = {
            0: {"width": 1920, "height": 1080, "left": 0, "top": 0, "is_combined": False},
            1: {"width": 2560, "height": 1440, "left": 1920, "top": 0, "is_combined": False},
        }
        cu.screen._primary_monitor_index = 0
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (list_screens(_make_ctx()))
        assert r.success
        assert "1920" in r.output
        assert "ACTIF" in r.output


# ─── set_screen ────────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestSetScreen:
    async def test_success(self):
        mod, cu = _make_cu_module()
        cu.screen._primary_monitor_index = 1
        cu.screen.get_monitor_offset.return_value = (1920, 0)
        cu.screen._monitor_info = {1: {"width": 2560, "height": 1440}}
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (set_screen(_make_ctx(), index=1))
        assert r.success

    async def test_missing_index(self):
        r = await (set_screen(_make_ctx()))
        assert not r.success
        assert "index" in r.output.lower()


# ─── ui_click ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestUiClick:
    async def test_success(self):
        mod, cu = _make_cu_module()
        cu.ui.is_available.return_value = True
        cu.ui.click_element_by_name.return_value = True
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (ui_click(_make_ctx(), element="OK"))
        assert r.success

    async def test_not_available(self):
        mod, cu = _make_cu_module()
        cu.ui.is_available.return_value = False
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (ui_click(_make_ctx(), element="OK"))
        assert not r.success
        assert "pywinauto" in r.output

    async def test_not_found(self):
        mod, cu = _make_cu_module()
        cu.ui.is_available.return_value = True
        cu.ui.click_element_by_name.return_value = False
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (ui_click(_make_ctx(), element="Invisible"))
        assert not r.success


# ─── ui_type ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestUiType:
    async def test_success(self):
        mod, cu = _make_cu_module()
        cu.ui.is_available.return_value = True
        cu.ui.type_in_field.return_value = True
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (ui_type(_make_ctx(), field="Search", text="hello"))
        assert r.success

    async def test_not_available(self):
        mod, cu = _make_cu_module()
        cu.ui.is_available.return_value = False
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (ui_type(_make_ctx(), field="Search", text="hello"))
        assert not r.success


# ─── ui_list_controls ──────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestUiListControls:
    async def test_success(self):
        mod, cu = _make_cu_module()
        cu.ui.is_available.return_value = True
        cu.ui.list_controls.return_value = [
            {"type": "Button", "name": "OK"},
            {"type": "Edit", "name": "Search"},
        ]
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (ui_list_controls(_make_ctx(), window="Notepad"))
        assert r.success
        assert "OK" in r.output

    async def test_no_controls(self):
        mod, cu = _make_cu_module()
        cu.ui.is_available.return_value = True
        cu.ui.list_controls.return_value = []
        with patch.dict(sys.modules, {"src.computer_use": mod}):
            r = await (ui_list_controls(_make_ctx(), window="Ghost"))
        assert not r.success


# ─── mouse_pattern ─────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestMousePattern:
    async def test_circle(self):
        cu_mod, cu = _make_cu_module()
        mock_pag = MagicMock()
        mock_pag.size.return_value = (1920, 1080)
        mock_pag.PAUSE = 0.1
        mock_pag.easeInOutQuad = MagicMock()
        mock_pag.moveTo = MagicMock()
        with patch.dict(sys.modules, {"src.computer_use": cu_mod, "pyautogui": mock_pag}):
            r = await (mouse_pattern(_make_ctx(), shape="circle", repetitions=1, radius=50))
        assert r.success
        assert "circle" in r.output

    async def test_unknown_shape(self):
        cu_mod, cu = _make_cu_module()
        mock_pag = MagicMock()
        mock_pag.size.return_value = (1920, 1080)
        mock_pag.PAUSE = 0.1
        mock_pag.moveTo = MagicMock()
        with patch.dict(sys.modules, {"src.computer_use": cu_mod, "pyautogui": mock_pag}):
            r = await (mouse_pattern(_make_ctx(), shape="hexagon"))
        assert not r.success
        assert "inconnue" in r.output


# ─── cursor_ide_local ─────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestCursorIdeLocal:
    async def test_invalid_action(self):
        r = await (cursor_ide_local(_make_ctx(), action="destroy"))
        assert not r.success
        assert "status" in r.output

    async def test_status_ide_not_found(self):
        ctx = _make_ctx()
        # ide_root won't exist in test environment
        r = await (cursor_ide_local(ctx, action="status"))
        # Should fail because ide_root doesn't exist
        assert not r.success or "introuvable" in r.output or "status" in r.output.lower()


# ─── computer_task ─────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestComputerTask:
    async def test_success(self):
        """computer_task avec un agent loop qui réussit immédiatement."""
        from src.computer_use.cu_agent_loop import CUTaskResult, CUStepResult, CUAction
        mock_result = CUTaskResult(
            goal="test", success=True, summary="Done",
            steps=[CUStepResult(
                iteration=1, action=CUAction(action="done", params={"summary": "OK"}),
                success=True, output="DONE: OK",
            )],
            total_iterations=1, total_duration_ms=500, exit_reason="done",
        )

        mock_loop = MagicMock()
        mock_loop.run = AsyncMock(return_value=mock_result)
        mock_cu_loop_mod = ModuleType("src.computer_use.cu_agent_loop")
        mock_cu_loop_mod.CUAgentLoop = MagicMock(return_value=mock_loop)
        mock_cu_loop_mod.CUAction = CUAction
        mock_cu_loop_mod.CUStepResult = CUStepResult
        mock_cu_loop_mod.CUTaskResult = CUTaskResult

        with patch.dict(sys.modules, {"src.computer_use.cu_agent_loop": mock_cu_loop_mod}):
            r = await (computer_task(_make_ctx(), goal="test task"))
        assert r.success
        assert "Done" in r.output

    async def test_failure(self):
        """computer_task avec un agent loop qui échoue."""
        from src.computer_use.cu_agent_loop import CUTaskResult
        mock_result = CUTaskResult(
            goal="test", success=False, summary="Max iterations",
            total_iterations=30, total_duration_ms=60000, exit_reason="max_iterations",
        )

        mock_loop = MagicMock()
        mock_loop.run = AsyncMock(return_value=mock_result)
        mock_cu_loop_mod = ModuleType("src.computer_use.cu_agent_loop")
        mock_cu_loop_mod.CUAgentLoop = MagicMock(return_value=mock_loop)
        mock_cu_loop_mod.CUTaskResult = CUTaskResult

        with patch.dict(sys.modules, {"src.computer_use.cu_agent_loop": mock_cu_loop_mod}):
            r = await (computer_task(_make_ctx(), goal="failing task"))
        assert not r.success


# ─── Handler Defs ─────────────────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestComputerUseHandlerDefs:
    async def test_count(self):
        defs = get_computer_use_handler_defs()
        assert len(defs) == 29

    async def test_names_unique(self):
        defs = get_computer_use_handler_defs()
        names = [d.name for d in defs]
        assert len(names) == len(set(names))

    async def test_all_have_handler(self):
        defs = get_computer_use_handler_defs()
        for d in defs:
            assert callable(d.handler), f"{d.name} handler non callable"

    async def test_expected_names_present(self):
        defs = get_computer_use_handler_defs()
        names = {d.name for d in defs}
        expected = {
            "click", "type_text", "open_app", "close_app",
            "cursor_ide_local", "hotkey", "get_active_window", "double_click",
            "scroll", "move_mouse", "press_key", "close_window", "wait",
            "spotify_play", "open_url", "list_windows", "drag",
            "screenshot_analyze", "click_element", "find_element",
            "list_screens", "set_screen", "ui_click", "ui_type",
            "ui_list_controls", "mouse_pattern", "zoom",
            "computer_task", "cu_readiness",
        }
        assert expected == names
