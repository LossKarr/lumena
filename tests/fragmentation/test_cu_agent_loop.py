"""
Tests unitaires pour src/computer_use/cu_agent_loop.py.

Teste la boucle Agent CU autonome : parsing d'actions, hash perceptuel,
exécution d'actions, détection de blocage, et boucle complète.

Pattern: mock complet du ComputerUse et VisionModule.
"""

import asyncio
import json
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.computer_use.cu_agent_loop import (
    CUAction,
    CUAgentLoop,
    CUStepResult,
    CUTaskResult,
    UNSTUCK_ACTIONS,
    _dict_to_action,
    _hamming_distance,
    _image_hash,
    _parse_cu_action,
)


# ─── CUAction ──────────────────────────────────────────────────────────────

class TestCUAction:
    def test_str(self):
        a = CUAction(action="click", params={"x": 100, "y": 200})
        assert "click" in str(a)
        assert "100" in str(a)

    def test_empty_params(self):
        a = CUAction(action="done", params={"summary": "OK"})
        assert a.action == "done"


# ─── CUStepResult ─────────────────────────────────────────────────────────

class TestCUStepResult:
    def test_creation(self):
        s = CUStepResult(
            iteration=1,
            action=CUAction(action="click", params={"x": 10, "y": 20}),
            success=True,
            output="Clic OK",
        )
        assert s.iteration == 1
        assert s.success
        assert s.timestamp  # auto-filled


# ─── CUTaskResult ─────────────────────────────────────────────────────────

class TestCUTaskResult:
    def test_success(self):
        r = CUTaskResult(goal="test", success=True, summary="Done", exit_reason="done")
        assert r.success
        assert r.exit_reason == "done"

    def test_failure(self):
        r = CUTaskResult(goal="test", success=False, summary="Stuck", exit_reason="stuck")
        assert not r.success


# ─── Action Parsing ───────────────────────────────────────────────────────

class TestParseAction:
    def test_valid_json(self):
        raw = '{"thought": "I see Chrome", "action": "click", "params": {"x": 500, "y": 300}}'
        a = _parse_cu_action(raw)
        assert a.action == "click"
        assert a.params["x"] == 500
        assert a.params["y"] == 300
        assert "Chrome" in a.thought

    def test_done_action(self):
        raw = '{"thought": "Task complete", "action": "done", "params": {"summary": "Opened Chrome"}}'
        a = _parse_cu_action(raw)
        assert a.action == "done"
        assert "Opened" in a.params["summary"]

    def test_type_text_action(self):
        raw = '{"action": "type_text", "params": {"text": "hello world"}}'
        a = _parse_cu_action(raw)
        assert a.action == "type_text"
        assert a.params["text"] == "hello world"

    def test_json_in_markdown(self):
        raw = '```json\n{"action": "scroll", "params": {"direction": "down", "amount": 3}}\n```'
        a = _parse_cu_action(raw)
        assert a.action == "scroll"
        assert a.params["direction"] == "down"

    def test_json_with_surrounding_text(self):
        raw = 'Here is my action:\n{"action": "click", "params": {"x": 100, "y": 200}}\nDone!'
        a = _parse_cu_action(raw)
        assert a.action == "click"

    def test_empty_response(self):
        a = _parse_cu_action("")
        assert a.action == "error"

    def test_invalid_json(self):
        a = _parse_cu_action("this is not json at all")
        assert a.action == "error"

    def test_params_at_top_level(self):
        """Certains LLMs mettent les params au même niveau que action."""
        raw = '{"thought": "clicking", "action": "click", "x": 200, "y": 400}'
        a = _parse_cu_action(raw)
        assert a.action == "click"
        assert a.params["x"] == 200
        assert a.params["y"] == 400

    def test_alias_type_to_type_text(self):
        raw = '{"action": "type", "params": {"text": "hello"}}'
        a = _parse_cu_action(raw)
        assert a.action == "type_text"

    def test_alias_finish_to_done(self):
        raw = '{"action": "finish", "params": {"summary": "OK"}}'
        a = _parse_cu_action(raw)
        assert a.action == "done"

    def test_alias_scroll_down(self):
        raw = '{"action": "scroll_down", "params": {}}'
        a = _parse_cu_action(raw)
        assert a.action == "scroll"
        assert a.params["direction"] == "down"

    def test_alias_scroll_up(self):
        raw = '{"action": "scroll_up"}'
        a = _parse_cu_action(raw)
        assert a.action == "scroll"
        assert a.params["direction"] == "up"

    def test_hotkey(self):
        raw = '{"action": "hotkey", "params": {"keys": "ctrl+c"}}'
        a = _parse_cu_action(raw)
        assert a.action == "hotkey"


# ─── Dict to Action ──────────────────────────────────────────────────────

class TestDictToAction:
    def test_normal(self):
        a = _dict_to_action({"action": "click", "params": {"x": 1, "y": 2}}, "raw")
        assert a.action == "click"
        assert a.params == {"x": 1, "y": 2}

    def test_alias_complete(self):
        a = _dict_to_action({"action": "complete", "params": {"summary": "OK"}}, "raw")
        assert a.action == "done"


# ─── Hamming Distance ────────────────────────────────────────────────────

class TestHammingDistance:
    def test_identical(self):
        assert _hamming_distance("0101", "0101") == 0

    def test_one_bit(self):
        assert _hamming_distance("0000", "0001") == 1

    def test_all_different(self):
        assert _hamming_distance("0000", "1111") == 4

    def test_different_lengths(self):
        assert _hamming_distance("00", "000") == 999

    def test_empty(self):
        assert _hamming_distance("", "") == 999


# ─── Image Hash ──────────────────────────────────────────────────────────

class TestImageHash:
    def test_nonexistent_file(self):
        """Hash d'un fichier inexistant retourne une chaîne vide."""
        h = _image_hash("/nonexistent/file.png")
        assert h == ""

    def test_returns_string(self):
        """Quand PIL est disponible, retourne une string de 64 chars (binaire)."""
        import tempfile
        import os
        import uuid
        try:
            from PIL import Image
            img = Image.new("RGB", (100, 100), color="red")
            tmp = os.path.join(tempfile.gettempdir(), f"test_hash_{uuid.uuid4().hex}.png")
            img.save(tmp)
            h = _image_hash(tmp)
            assert isinstance(h, str)
            assert len(h) == 64  # 8x8 = 64 bits
            assert all(c in "01" for c in h)
            os.unlink(tmp)
        except ImportError:
            pytest.skip("PIL non disponible")

    def test_similar_images_low_distance(self):
        """Deux images quasi-identiques ont une distance de Hamming faible."""
        import tempfile
        import os
        try:
            from PIL import Image
            img1 = Image.new("RGB", (100, 100), color=(128, 128, 128))
            img2 = Image.new("RGB", (100, 100), color=(130, 130, 130))
            tmp1 = os.path.join(tempfile.gettempdir(), "test_h1.png")
            tmp2 = os.path.join(tempfile.gettempdir(), "test_h2.png")
            img1.save(tmp1)
            img2.save(tmp2)
            h1 = _image_hash(tmp1)
            h2 = _image_hash(tmp2)
            assert _hamming_distance(h1, h2) <= 5
            os.unlink(tmp1)
            os.unlink(tmp2)
        except ImportError:
            pytest.skip("PIL non disponible")


# ─── CUAgentLoop — action execution ──────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestExecuteAction:
    """Teste _execute_action avec des mocks."""

    def _make_loop(self):
        loop = CUAgentLoop()
        mock_cu = MagicMock()
        mock_cu.mouse = MagicMock()
        mock_cu.keyboard = MagicMock()
        mock_cu.screen = MagicMock()
        mock_cu.screen.get_monitor_offset.return_value = (0, 0)
        mock_cu.open_application = AsyncMock()
        mock_vision = MagicMock()
        mock_vision.scale_coordinates_to_screen.return_value = (100, 200)
        loop._cu = mock_cu
        loop._vision = mock_vision
        return loop, mock_cu

    async def test_click(self):
        loop, cu = self._make_loop()
        action = CUAction(action="click", params={"x": 100, "y": 200})
        result = await loop._execute_action(action, scale_factor=1.0)
        assert "Clic" in result
        cu.mouse.click.assert_called_once()

    async def test_type_text(self):
        loop, cu = self._make_loop()
        action = CUAction(action="type_text", params={"text": "hello"})
        result = await loop._execute_action(action, scale_factor=1.0)
        assert "hello" in result
        cu.keyboard.type_text.assert_called_with("hello")

    async def test_press_key(self):
        loop, cu = self._make_loop()
        action = CUAction(action="press_key", params={"key": "enter"})
        result = await loop._execute_action(action, scale_factor=1.0)
        assert "enter" in result
        cu.keyboard.press_key.assert_called_with("enter")

    async def test_hotkey(self):
        loop, cu = self._make_loop()
        action = CUAction(action="hotkey", params={"keys": "ctrl+c"})
        result = await loop._execute_action(action, scale_factor=1.0)
        assert "ctrl" in result
        cu.keyboard.hotkey.assert_called()

    async def test_scroll(self):
        loop, cu = self._make_loop()
        action = CUAction(action="scroll", params={"direction": "down", "amount": 3})
        result = await loop._execute_action(action, scale_factor=1.0)
        assert "Scroll" in result
        cu.mouse.scroll.assert_called_with(-3)

    async def test_scroll_up(self):
        loop, cu = self._make_loop()
        action = CUAction(action="scroll", params={"direction": "up", "amount": 5})
        result = await loop._execute_action(action, scale_factor=1.0)
        cu.mouse.scroll.assert_called_with(5)

    async def test_open_app(self):
        loop, cu = self._make_loop()
        action = CUAction(action="open_app", params={"name": "chrome"})
        result = await loop._execute_action(action, scale_factor=1.0)
        assert "chrome" in result
        cu.open_application.assert_called_with("chrome")

    async def test_wait(self):
        loop, cu = self._make_loop()
        action = CUAction(action="wait", params={"seconds": 1})
        result = await loop._execute_action(action, scale_factor=1.0)
        assert "1s" in result

    async def test_done(self):
        loop, cu = self._make_loop()
        action = CUAction(action="done", params={"summary": "Task complete"})
        result = await loop._execute_action(action, scale_factor=1.0)
        assert "DONE" in result
        assert "Task complete" in result

    async def test_unknown_action(self):
        loop, cu = self._make_loop()
        action = CUAction(action="fly_to_mars", params={})
        result = await loop._execute_action(action, scale_factor=1.0)
        assert "inconnue" in result

    async def test_coordinate_scaling(self):
        """Les coordonnées LLM sont converties vers l'espace écran."""
        loop, cu = self._make_loop()
        loop._vision.scale_coordinates_to_screen.return_value = (200, 400)
        cu.screen.get_monitor_offset.return_value = (1920, 0)
        action = CUAction(action="click", params={"x": 100, "y": 200})
        await loop._execute_action(action, scale_factor=0.5)
        # scale(100,200) → (200,400) + offset(1920,0) = (2120,400)
        cu.mouse.click.assert_called_with(2120, 400, "left")

    async def test_double_click(self):
        loop, cu = self._make_loop()
        action = CUAction(action="double_click", params={"x": 50, "y": 60})
        result = await loop._execute_action(action, scale_factor=1.0)
        assert "Double-clic" in result

    async def test_right_click(self):
        loop, cu = self._make_loop()
        action = CUAction(action="right_click", params={"x": 50, "y": 60})
        result = await loop._execute_action(action, scale_factor=1.0)
        assert "Clic droit" in result


# ─── CUAgentLoop — full run ──────────────────────────────────────────────

@pytest.mark.asyncio(mode="auto")
class TestCUAgentLoopRun:
    """Teste la boucle complète avec un scénario mocké."""

    def _make_loop_with_llm(self, llm_responses: list):
        """Crée un CUAgentLoop avec des réponses LLM pré-programmées."""
        response_iter = iter(llm_responses)

        async def mock_vision_llm(image_path, prompt):
            try:
                resp = next(response_iter)
                return {"success": True, "answer": resp}
            except StopIteration:
                # Forcer done si on manque de réponses
                return {"success": True, "answer": '{"action": "done", "params": {"summary": "Auto-done"}}'}

        loop = CUAgentLoop(
            vision_llm_func=mock_vision_llm,
            max_iterations=10,
            timeout_seconds=30,
        )

        # Mock CU et Vision
        mock_cu = MagicMock()
        mock_cu.take_screenshot = AsyncMock(return_value="/tmp/shot.png")
        mock_cu.mouse = MagicMock()
        mock_cu.keyboard = MagicMock()
        mock_cu.screen = MagicMock()
        mock_cu.screen.get_monitor_offset.return_value = (0, 0)
        mock_cu.open_application = AsyncMock()
        mock_cu.window = MagicMock()

        mock_vision = MagicMock()
        mock_vision.prepare_screenshot_for_llm = AsyncMock(
            return_value=("/tmp/prep.png", 0.8, 1920, 1080, 0, 0)
        )
        mock_vision.get_screen_metadata.return_value = "[Screen: 1920x1080]"
        mock_vision.scale_coordinates_to_screen.side_effect = (
            lambda x, y, s, pad_offset_x=0, pad_offset_y=0: (round((x - pad_offset_x) / s), round((y - pad_offset_y) / s))
        )

        loop._cu = mock_cu
        loop._vision = mock_vision

        return loop, mock_cu

    async def test_single_step_done(self):
        """Le LLM dit 'done' immédiatement → 1 itération."""
        loop, cu = self._make_loop_with_llm([
            '{"thought": "Already done", "action": "done", "params": {"summary": "Nothing to do"}}',
        ])
        result = await loop.run("Do nothing")
        assert result.success
        assert result.exit_reason == "done"
        assert result.total_iterations == 1
        assert "Nothing to do" in result.summary

    async def test_two_step_task(self):
        """Click puis done → 2 itérations."""
        loop, cu = self._make_loop_with_llm([
            '{"thought": "I see a button", "action": "click", "params": {"x": 500, "y": 300}}',
            '{"thought": "Clicked", "action": "done", "params": {"summary": "Button clicked"}}',
        ])
        result = await loop.run("Click the button")
        assert result.success
        assert result.total_iterations == 2
        cu.mouse.click.assert_called_once()

    async def test_multi_step_scenario(self):
        """Scénario 5 étapes : open_app → click → type → press_key → done."""
        loop, cu = self._make_loop_with_llm([
            '{"thought": "Opening Chrome", "action": "open_app", "params": {"name": "chrome"}}',
            '{"thought": "Clicking URL bar", "action": "click", "params": {"x": 400, "y": 50}}',
            '{"thought": "Typing URL", "action": "type_text", "params": {"text": "google.com"}}',
            '{"thought": "Pressing Enter", "action": "press_key", "params": {"key": "enter"}}',
            '{"thought": "Page loaded", "action": "done", "params": {"summary": "Navigated to google.com"}}',
        ])
        result = await loop.run("Open Chrome and go to google.com")
        assert result.success
        assert result.total_iterations == 5
        assert len(result.steps) == 5
        cu.open_application.assert_called_with("chrome")
        cu.keyboard.type_text.assert_called_with("google.com")

    async def test_max_iterations_reached(self):
        """Le LLM ne dit jamais 'done' → arrêt au max_iterations."""
        loop, cu = self._make_loop_with_llm([
            '{"thought": "Clicking", "action": "click", "params": {"x": 100, "y": 100}}'
        ] * 10)
        loop.max_iterations = 5
        result = await loop.run("Never-ending task")
        assert not result.success
        assert result.exit_reason == "max_iterations"
        assert result.total_iterations == 5

    async def test_llm_error_continues(self):
        """Si le LLM retourne une erreur, la boucle continue."""
        response_idx = [0]
        responses = [
            None,  # erreur
            '{"thought": "OK", "action": "done", "params": {"summary": "Recovered"}}',
        ]

        async def mock_llm(image_path, prompt):
            idx = response_idx[0]
            response_idx[0] += 1
            if idx == 0:
                return {"success": False, "error": "API timeout"}
            return {"success": True, "answer": responses[1]}

        loop = CUAgentLoop(vision_llm_func=mock_llm, max_iterations=5)
        mock_cu = MagicMock()
        mock_cu.take_screenshot = AsyncMock(return_value="/tmp/s.png")
        mock_cu.screen = MagicMock()
        mock_cu.screen.get_monitor_offset.return_value = (0, 0)
        mock_vision = MagicMock()
        mock_vision.prepare_screenshot_for_llm = AsyncMock(return_value=("/tmp/p.png", 1.0, 1920, 1080, 0, 0))
        mock_vision.get_screen_metadata.return_value = "[Screen]"
        loop._cu = mock_cu
        loop._vision = mock_vision

        result = await loop.run("Recover from error")
        assert result.success
        assert result.total_iterations == 2  # 1 error + 1 done

    async def test_invalid_json_continues(self):
        """Si le LLM retourne du JSON invalide, la boucle continue."""
        loop, cu = self._make_loop_with_llm([
            "This is not JSON at all!",
            '{"action": "done", "params": {"summary": "Fixed"}}',
        ])
        result = await loop.run("Handle bad JSON")
        assert result.success
        assert result.total_iterations == 2

    async def test_task_result_has_steps(self):
        """Le résultat contient les détails de chaque étape."""
        loop, cu = self._make_loop_with_llm([
            '{"action": "click", "params": {"x": 10, "y": 20}}',
            '{"action": "done", "params": {"summary": "OK"}}',
        ])
        result = await loop.run("Step tracking")
        assert len(result.steps) == 2
        assert result.steps[0].action.action == "click"
        assert result.steps[1].action.action == "done"
        assert all(s.duration_ms >= 0 for s in result.steps)


# ─── Unstuck Actions ─────────────────────────────────────────────────────

class TestUnstuckActions:
    def test_unstuck_actions_exist(self):
        assert len(UNSTUCK_ACTIONS) >= 3

    def test_unstuck_actions_valid(self):
        for a in UNSTUCK_ACTIONS:
            assert a.action in ("press_key", "scroll", "hotkey", "click")
            assert a.thought  # Chaque stratégie a une description


# ─── Prompt Building ─────────────────────────────────────────────────────

class TestPromptBuilding:
    def test_build_step_prompt(self):
        loop = CUAgentLoop()
        prompt = loop._build_step_prompt(
            goal="Open Chrome",
            steps=[],
            screen_metadata="[Screen: 1920x1080]",
        )
        assert "1920x1080" in prompt
        assert "aucune action" in prompt

    def test_build_step_prompt_with_history(self):
        loop = CUAgentLoop()
        steps = [
            CUStepResult(
                iteration=1,
                action=CUAction(action="click", params={"x": 100, "y": 200}),
                success=True,
                output="Clic OK",
            ),
        ]
        prompt = loop._build_step_prompt(
            goal="Test",
            steps=steps,
            screen_metadata="[Screen]",
        )
        assert "click" in prompt
        assert "1" in prompt  # 1 step dans l'historique
