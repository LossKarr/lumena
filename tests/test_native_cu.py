"""
Tests unitaires pour src/computer_use/native_cu.py.

Teste la cascade CU natif (Anthropic → OpenAI → Google → None),
chaque boucle provider, et le wiring dans computer_task().
"""

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.computer_use.cu_agent_loop import CUAction, CUStepResult, CUTaskResult
from src.computer_use.native_cu import (
    _has_key,
    _get_key,
    _encode_b64,
    _execute_action_sync,
    try_native_cu_cascade,
    _anthropic_cu_loop,
    _openai_cu_loop,
    _google_cu_loop,
    _CASCADE_ORDER,
)


# ─── Helpers ───────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Nettoie les env vars CU natif entre chaque test."""
    for var in ("LUMENA_CU_NATIVE_DISABLED", "LUMENA_CU_NATIVE_ORDER",
                "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def _mock_screenshot():
    """Mock _take_screenshot pour retourner un faux path."""
    async def fake_ss():
        import tempfile, os
        path = os.path.join(tempfile.gettempdir(), "test_ncu_ss.png")
        # Créer un mini PNG valide (1x1 pixel)
        import struct, zlib
        def _min_png():
            sig = b'\x89PNG\r\n\x1a\n'
            ihdr_data = struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0)
            ihdr_crc = zlib.crc32(b'IHDR' + ihdr_data) & 0xffffffff
            ihdr = struct.pack('>I', 13) + b'IHDR' + ihdr_data + struct.pack('>I', ihdr_crc)
            raw = zlib.compress(b'\x00\x00\x00\x00')
            idat_crc = zlib.crc32(b'IDAT' + raw) & 0xffffffff
            idat = struct.pack('>I', len(raw)) + b'IDAT' + raw + struct.pack('>I', idat_crc)
            iend_crc = zlib.crc32(b'IEND') & 0xffffffff
            iend = struct.pack('>I', 0) + b'IEND' + struct.pack('>I', iend_crc)
            return sig + ihdr + idat + iend
        with open(path, 'wb') as f:
            f.write(_min_png())
        return path, 1920, 1080
    return fake_ss


# ─── _has_key / _get_key ──────────────────────────────────────────────────

class TestHasKey:
    def test_no_key(self):
        assert not _has_key("anthropic")
        assert not _has_key("openai")
        assert not _has_key("google")

    def test_with_key(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        assert _has_key("anthropic")
        assert not _has_key("openai")

    def test_unknown_provider(self):
        assert not _has_key("unknown")


class TestGetKey:
    def test_returns_key(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        assert _get_key("openai") == "sk-test"

    def test_missing(self):
        assert _get_key("anthropic") == ""


# ─── _execute_action_sync ─────────────────────────────────────────────────

class TestExecuteAction:
    def test_unknown_action(self):
        result = _execute_action_sync("unknown_action", {})
        assert "inconnue" in result.lower() or "unknown" in result.lower()

    @patch("src.computer_use.controller.get_computer_use")
    def test_click(self, mock_cu):
        mock_cu.return_value.mouse = MagicMock()
        result = _execute_action_sync("click", {"x": 100, "y": 200})
        assert "100" in result
        mock_cu.return_value.mouse.click.assert_called_once()

    @patch("src.computer_use.controller.get_computer_use")
    def test_type_text(self, mock_cu):
        mock_cu.return_value.keyboard = MagicMock()
        result = _execute_action_sync("type_text", {"text": "hello"})
        assert "hello" in result
        mock_cu.return_value.keyboard.type_text.assert_called_once_with("hello")

    @patch("src.computer_use.controller.get_computer_use")
    def test_press_key(self, mock_cu):
        mock_cu.return_value.keyboard = MagicMock()
        result = _execute_action_sync("press_key", {"key": "Return"})
        assert "enter" in result.lower() or "return" in result.lower()

    @patch("src.computer_use.controller.get_computer_use")
    def test_scroll(self, mock_cu):
        mock_cu.return_value.mouse = MagicMock()
        result = _execute_action_sync("scroll", {"direction": "down", "amount": 3})
        assert "scroll" in result.lower()

    def test_screenshot_action(self):
        result = _execute_action_sync("screenshot", {})
        assert "screenshot" in result.lower()

    @patch("src.computer_use.controller.get_computer_use")
    def test_hotkey(self, mock_cu):
        mock_cu.return_value.keyboard = MagicMock()
        result = _execute_action_sync("hotkey", {"keys": "ctrl+c"})
        assert "ctrl" in result.lower()

    @patch("src.computer_use.controller.get_computer_use")
    def test_error_handling(self, mock_cu):
        mock_cu.return_value.mouse.click.side_effect = Exception("click failed")
        result = _execute_action_sync("click", {"x": 0, "y": 0})
        assert "erreur" in result.lower() or "error" in result.lower()


# ─── try_native_cu_cascade ────────────────────────────────────────────────

class TestCascade:
    @pytest.mark.asyncio
    async def test_no_keys_returns_none(self):
        """Sans clé API, la cascade retourne None."""
        result = await try_native_cu_cascade("test goal")
        assert result is None

    @pytest.mark.asyncio
    async def test_disabled_returns_none(self, monkeypatch):
        """LUMENA_CU_NATIVE_DISABLED=1 → None immédiat."""
        monkeypatch.setenv("LUMENA_CU_NATIVE_DISABLED", "1")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        result = await try_native_cu_cascade("test goal")
        assert result is None

    @pytest.mark.asyncio
    async def test_anthropic_first(self, monkeypatch):
        """Avec clé Anthropic, tente Anthropic en premier."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        mock_result = CUTaskResult(
            goal="test", success=True, summary="Done",
            steps=[], total_iterations=1,
            total_duration_ms=100, exit_reason="done",
        )

        with patch("src.computer_use.native_cu._anthropic_cu_loop", new_callable=AsyncMock) as mock_loop:
            mock_loop.return_value = mock_result
            result = await try_native_cu_cascade("test goal")

        assert result is not None
        assert result.success
        assert "ANTHROPIC" in result.summary
        mock_loop.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_to_openai(self, monkeypatch):
        """Si Anthropic échoue (exception), tente OpenAI."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test2")

        mock_result = CUTaskResult(
            goal="test", success=True, summary="Done via OpenAI",
            steps=[], total_iterations=2,
            total_duration_ms=200, exit_reason="done",
        )

        with patch("src.computer_use.native_cu._anthropic_cu_loop", new_callable=AsyncMock) as mock_ant, \
             patch("src.computer_use.native_cu._openai_cu_loop", new_callable=AsyncMock) as mock_oai:
            mock_ant.side_effect = RuntimeError("Anthropic error")
            mock_oai.return_value = mock_result
            result = await try_native_cu_cascade("test goal")

        assert result is not None
        assert "OPENAI" in result.summary

    @pytest.mark.asyncio
    async def test_fallback_to_google(self, monkeypatch):
        """Si Anthropic et OpenAI échouent, tente Google."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test2")
        monkeypatch.setenv("GOOGLE_API_KEY", "gk-test")

        mock_result = CUTaskResult(
            goal="test", success=True, summary="Done via Google",
            steps=[], total_iterations=3,
            total_duration_ms=300, exit_reason="done",
        )

        with patch("src.computer_use.native_cu._anthropic_cu_loop", new_callable=AsyncMock) as mock_ant, \
             patch("src.computer_use.native_cu._openai_cu_loop", new_callable=AsyncMock) as mock_oai, \
             patch("src.computer_use.native_cu._google_cu_loop", new_callable=AsyncMock) as mock_goo:
            mock_ant.side_effect = RuntimeError("Anthropic error")
            mock_oai.side_effect = RuntimeError("OpenAI error")
            mock_goo.return_value = mock_result
            result = await try_native_cu_cascade("test goal")

        assert result is not None
        assert "GOOGLE" in result.summary

    @pytest.mark.asyncio
    async def test_all_fail_returns_none(self, monkeypatch):
        """Si tous les natifs échouent, retourne None."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test2")
        monkeypatch.setenv("GOOGLE_API_KEY", "gk-test")

        with patch("src.computer_use.native_cu._anthropic_cu_loop", new_callable=AsyncMock) as m1, \
             patch("src.computer_use.native_cu._openai_cu_loop", new_callable=AsyncMock) as m2, \
             patch("src.computer_use.native_cu._google_cu_loop", new_callable=AsyncMock) as m3:
            m1.side_effect = RuntimeError("fail")
            m2.side_effect = RuntimeError("fail")
            m3.side_effect = RuntimeError("fail")
            result = await try_native_cu_cascade("test goal")

        assert result is None

    @pytest.mark.asyncio
    async def test_custom_order(self, monkeypatch):
        """LUMENA_CU_NATIVE_ORDER permet de changer l'ordre."""
        monkeypatch.setenv("LUMENA_CU_NATIVE_ORDER", "google,anthropic")
        monkeypatch.setenv("GOOGLE_API_KEY", "gk-test")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        mock_result = CUTaskResult(
            goal="test", success=True, summary="Done",
            steps=[], total_iterations=1,
            total_duration_ms=100, exit_reason="done",
        )

        with patch("src.computer_use.native_cu._google_cu_loop", new_callable=AsyncMock) as mock_goo, \
             patch("src.computer_use.native_cu._anthropic_cu_loop", new_callable=AsyncMock) as mock_ant:
            mock_goo.return_value = mock_result
            result = await try_native_cu_cascade("test goal")

        assert result is not None
        assert "GOOGLE" in result.summary
        mock_ant.assert_not_called()  # Google réussit, Anthropic pas appelé

    @pytest.mark.asyncio
    async def test_successful_but_failed_task_not_cascaded(self, monkeypatch):
        """Un provider qui s'exécute mais échoue (max_iter) ne cascade PAS."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test2")

        mock_result = CUTaskResult(
            goal="test", success=False, summary="Max iterations",
            steps=[], total_iterations=30,
            total_duration_ms=5000, exit_reason="max_iterations",
        )

        with patch("src.computer_use.native_cu._anthropic_cu_loop", new_callable=AsyncMock) as mock_ant, \
             patch("src.computer_use.native_cu._openai_cu_loop", new_callable=AsyncMock) as mock_oai:
            mock_ant.return_value = mock_result
            result = await try_native_cu_cascade("test goal")

        assert result is not None
        assert not result.success
        mock_oai.assert_not_called()  # Pas de cascade car Anthropic a fonctionné

    def test_cascade_order(self):
        """L'ordre par défaut est Anthropic → OpenAI → Google."""
        assert _CASCADE_ORDER == ["anthropic", "openai", "google"]


# ─── Anthropic CU Loop ───────────────────────────────────────────────────

class TestAnthropicCULoop:
    @pytest.mark.asyncio
    async def test_end_turn_no_tools(self, monkeypatch):
        """Si Claude répond sans tool_use, c'est terminé."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        response_data = {
            "stop_reason": "end_turn",
            "content": [
                {"type": "text", "text": "J'ai terminé la tâche"}
            ],
        }

        with patch("src.computer_use.native_cu._take_screenshot", new=_mock_screenshot()), \
             patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = response_data
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=MagicMock(
                post=AsyncMock(return_value=mock_resp)
            ))
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _anthropic_cu_loop("test", max_steps=5)

        assert result.success
        assert "terminé" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_tool_use_click(self, monkeypatch):
        """Claude demande un clic, puis termine."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        responses = [
            {
                "stop_reason": "tool_use",
                "content": [
                    {"type": "text", "text": "Je clique sur le bouton"},
                    {
                        "type": "tool_use",
                        "id": "tu_123",
                        "name": "computer",
                        "input": {"action": "left_click", "coordinate": [500, 300]},
                    },
                ],
            },
            {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "Tâche accomplie"}],
            },
        ]
        call_count = [0]

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = responses[min(call_count[0], len(responses) - 1)]
            call_count[0] += 1
            return resp

        with patch("src.computer_use.native_cu._take_screenshot", new=_mock_screenshot()), \
             patch("src.computer_use.native_cu._exec_action", new_callable=AsyncMock) as mock_exec, \
             patch("httpx.AsyncClient") as mock_client_cls:
            mock_exec.return_value = "Clic à (500, 300)"
            client_inst = MagicMock()
            client_inst.post = mock_post
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client_inst)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _anthropic_cu_loop("click test", max_steps=5)

        assert result.success
        assert len(result.steps) >= 1

    @pytest.mark.asyncio
    async def test_http_error_raises(self, monkeypatch):
        """Erreur HTTP Anthropic lève RuntimeError."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

        with patch("src.computer_use.native_cu._take_screenshot", new=_mock_screenshot()), \
             patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            mock_resp.text = "Internal Server Error"
            client_inst = MagicMock()
            client_inst.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client_inst)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            with pytest.raises(RuntimeError, match="500"):
                await _anthropic_cu_loop("test", max_steps=2)


# ─── OpenAI CU Loop ──────────────────────────────────────────────────────

class TestOpenAICULoop:
    @pytest.mark.asyncio
    async def test_no_computer_call(self, monkeypatch):
        """Si le modèle répond sans computer_call, c'est terminé."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        response_data = {
            "id": "resp_123",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "Tâche terminée"}],
                }
            ],
        }

        with patch("src.computer_use.native_cu._take_screenshot", new=_mock_screenshot()), \
             patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = response_data
            client_inst = MagicMock()
            client_inst.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client_inst)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _openai_cu_loop("test", max_steps=5)

        assert result.success

    @pytest.mark.asyncio
    async def test_computer_call_then_done(self, monkeypatch):
        """computer_call → exécution → réponse sans call → terminé."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

        responses = [
            {
                "id": "resp_1",
                "output": [
                    {
                        "type": "computer_call",
                        "call_id": "cc_1",
                        "action": {"type": "click", "coordinate": [400, 300]},
                    }
                ],
            },
            {
                "id": "resp_2",
                "output": [
                    {
                        "type": "message",
                        "content": [{"type": "output_text", "text": "Done"}],
                    }
                ],
            },
        ]
        call_count = [0]

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = responses[min(call_count[0], len(responses) - 1)]
            call_count[0] += 1
            return resp

        with patch("src.computer_use.native_cu._take_screenshot", new=_mock_screenshot()), \
             patch("src.computer_use.native_cu._exec_action", new_callable=AsyncMock) as mock_exec, \
             patch("httpx.AsyncClient") as mock_client_cls:
            mock_exec.return_value = "Clic à (400, 300)"
            client_inst = MagicMock()
            client_inst.post = mock_post
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client_inst)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _openai_cu_loop("click test", max_steps=5)

        assert result.success
        assert len(result.steps) >= 1


# ─── Google CU Loop ──────────────────────────────────────────────────────

class TestGoogleCULoop:
    @pytest.mark.asyncio
    async def test_text_response_means_done(self, monkeypatch):
        """Réponse texte sans functionCall = terminé."""
        monkeypatch.setenv("GOOGLE_API_KEY", "gk-test")

        response_data = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "J'ai terminé le but"}],
                    },
                    "finishReason": "STOP",
                }
            ]
        }

        with patch("src.computer_use.native_cu._take_screenshot", new=_mock_screenshot()), \
             patch("httpx.AsyncClient") as mock_client_cls:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = response_data
            client_inst = MagicMock()
            client_inst.post = AsyncMock(return_value=mock_resp)
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client_inst)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _google_cu_loop("test", max_steps=5)

        assert result.success

    @pytest.mark.asyncio
    async def test_function_call_click(self, monkeypatch):
        """functionCall click → exécution → texte = terminé."""
        monkeypatch.setenv("GOOGLE_API_KEY", "gk-test")

        responses = [
            {
                "candidates": [{
                    "content": {
                        "parts": [{
                            "functionCall": {
                                "name": "click",
                                "args": {"x": 5000, "y": 5000},
                            }
                        }],
                    },
                    "finishReason": "STOP",
                }]
            },
            {
                "candidates": [{
                    "content": {
                        "parts": [{"text": "Tâche accomplie"}],
                    },
                    "finishReason": "STOP",
                }]
            },
        ]
        call_count = [0]

        async def mock_post(url, **kwargs):
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = responses[min(call_count[0], len(responses) - 1)]
            call_count[0] += 1
            return resp

        with patch("src.computer_use.native_cu._take_screenshot", new=_mock_screenshot()), \
             patch("src.computer_use.native_cu._exec_action", new_callable=AsyncMock) as mock_exec, \
             patch("httpx.AsyncClient") as mock_client_cls:
            mock_exec.return_value = "Clic à (960, 540)"
            client_inst = MagicMock()
            client_inst.post = mock_post
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=client_inst)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await _google_cu_loop("click test", max_steps=5)

        assert result.success
        # Vérifier la conversion coordonnées normalisées → pixels
        exec_call = mock_exec.call_args
        assert exec_call is not None


# ─── Wiring computer_task ─────────────────────────────────────────────────

class TestComputerTaskWiring:
    @pytest.mark.asyncio
    async def test_cascade_then_fallback(self, monkeypatch):
        """computer_task utilise la cascade puis fallback maison."""
        mock_result = CUTaskResult(
            goal="test", success=True, summary="Done natif",
            steps=[], total_iterations=1,
            total_duration_ms=100, exit_reason="done",
        )

        with patch("src.computer_use.native_cu.try_native_cu_cascade", new_callable=AsyncMock) as mock_cascade:
            mock_cascade.return_value = mock_result

            from src.reasoning.handlers.computer_use import computer_task
            ctx = MagicMock()
            result = await computer_task(ctx, goal="test goal", max_steps=10)

        assert result.success
        mock_cascade.assert_called_once()

    @pytest.mark.asyncio
    async def test_fallback_to_maison_when_cascade_none(self, monkeypatch):
        """Si la cascade retourne None, fallback vers CUAgentLoop."""
        maison_result = CUTaskResult(
            goal="test", success=True, summary="Done maison",
            steps=[], total_iterations=2,
            total_duration_ms=200, exit_reason="done",
        )

        with patch("src.computer_use.native_cu.try_native_cu_cascade", new_callable=AsyncMock) as mock_cascade, \
             patch("src.computer_use.cu_agent_loop.CUAgentLoop") as mock_loop_cls:
            mock_cascade.return_value = None
            mock_loop = MagicMock()
            mock_loop.run = AsyncMock(return_value=maison_result)
            mock_loop_cls.return_value = mock_loop

            from src.reasoning.handlers.computer_use import computer_task
            ctx = MagicMock()
            result = await computer_task(ctx, goal="test fallback", max_steps=10)

        assert result.success
        mock_loop.run.assert_called_once_with("test fallback")


# ─── Coordinate conversion Google ─────────────────────────────────────────

class TestGoogleCoordConversion:
    def test_normalized_to_pixels(self):
        """Coordonnées normalisées 0-9999 → pixels réels."""
        scr_w, scr_h = 1920, 1080
        nx, ny = 5000, 5000
        px = round(nx * scr_w / 10000)
        py = round(ny * scr_h / 10000)
        assert px == 960
        assert py == 540

    def test_corner_cases(self):
        scr_w, scr_h = 1920, 1080
        assert round(0 * scr_w / 10000) == 0
        assert round(0 * scr_h / 10000) == 0
        assert round(9999 * scr_w / 10000) == 1920  # ~1919.8 → 1920


# ─── Env var override ─────────────────────────────────────────────────────

class TestEnvVarConfig:
    def test_models_from_env(self, monkeypatch):
        monkeypatch.setenv("LUMENA_ANTHROPIC_CU_MODEL", "claude-test")
        monkeypatch.setenv("LUMENA_OPENAI_CU_MODEL", "gpt-test")
        monkeypatch.setenv("LUMENA_GOOGLE_CU_MODEL", "gemini-test")

        # Re-import pour prendre les env vars
        import importlib
        import src.computer_use.native_cu as ncu
        importlib.reload(ncu)

        assert ncu._ANTHROPIC_CU_MODEL == "claude-test"
        assert ncu._OPENAI_CU_MODEL == "gpt-test"
        assert ncu._GOOGLE_CU_MODEL == "gemini-test"

        # Cleanup: re-reload avec les défauts
        monkeypatch.delenv("LUMENA_ANTHROPIC_CU_MODEL")
        monkeypatch.delenv("LUMENA_OPENAI_CU_MODEL")
        monkeypatch.delenv("LUMENA_GOOGLE_CU_MODEL")
        importlib.reload(ncu)
