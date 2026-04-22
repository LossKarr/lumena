"""Tests P2 — MiniMax provider natif + o3/o4-mini."""

import os
import pytest
from unittest.mock import patch, AsyncMock, MagicMock

from src.llm.providers import (
    ProviderType,
    AVAILABLE_MODELS,
    MODEL_SKILLS,
    get_model_config,
    check_api_key,
    get_api_key,
    models_with_capability,
)


# ── P2.1: ProviderType.MINIMAX ──────────────────────────────────────────────

class TestMiniMaxProvider:
    def test_minimax_in_enum(self):
        assert hasattr(ProviderType, "MINIMAX")
        assert ProviderType.MINIMAX.value == "minimax"

    def test_check_api_key_minimax(self):
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "test-key"}):
            assert check_api_key(ProviderType.MINIMAX) is True

    def test_check_api_key_minimax_missing(self):
        with patch.dict(os.environ, {}, clear=True):
            # MINIMAX_API_KEY not set
            assert check_api_key(ProviderType.MINIMAX) is False

    def test_get_api_key_minimax(self):
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "mm-secret"}):
            assert get_api_key(ProviderType.MINIMAX) == "mm-secret"


# ── P2.2: MiniMax models ────────────────────────────────────────────────────

class TestMiniMaxModels:
    MINIMAX_MODELS = [
        "minimax-m2.5",
        "minimax-m2.5-highspeed",
        "minimax-m2.1",
        "minimax-m2.1-highspeed",
        "minimax-m2.7",
    ]

    @pytest.mark.parametrize("name", MINIMAX_MODELS)
    def test_minimax_model_exists(self, name):
        cfg = get_model_config(name)
        assert cfg is not None, f"{name} not in AVAILABLE_MODELS"
        assert cfg.provider == ProviderType.MINIMAX

    @pytest.mark.parametrize("name", MINIMAX_MODELS)
    def test_minimax_model_skills(self, name):
        assert name in MODEL_SKILLS, f"{name} missing from MODEL_SKILLS"

    def test_minimax_context_window(self):
        for name in self.MINIMAX_MODELS:
            cfg = get_model_config(name)
            assert cfg.context_window == 204800

    def test_minimax_max_output(self):
        for name in self.MINIMAX_MODELS:
            cfg = get_model_config(name)
            assert cfg.max_output_tokens == 32768

    def test_minimax_capabilities(self):
        for name in self.MINIMAX_MODELS:
            cfg = get_model_config(name)
            assert "tool_calling" in cfg.capabilities
            assert "cheap_text" in cfg.capabilities

    def test_nvidia_minimax_description_updated(self):
        cfg = get_model_config("nvidia-minimax-m2.5")
        assert "préférer MiniMax natif" in cfg.description


# ── P2.3: _chat_minimax_result routing ───────────────────────────────────────

class TestMiniMaxRouting:
    def test_provider_health_has_minimax(self):
        from src.llm.multi_provider import MultiProviderLLM
        llm = MultiProviderLLM.__new__(MultiProviderLLM)
        llm.provider_health = {}
        # Simulate __init__ health dict
        from src.llm.multi_provider import MultiProviderLLM as _M
        with patch.object(_M, "_resolve_initial_model_name", return_value="deepseek-v3"), \
             patch.object(_M, "_resolve_ollama_host", return_value="http://localhost:11434"), \
             patch.object(_M, "_load_model_config"):
            obj = _M(model_name="deepseek-v3")
            assert "minimax" in obj.provider_health

    def test_fallback_order_has_minimax(self):
        from src.llm.multi_provider import MultiProviderLLM as _M
        with patch.object(_M, "_resolve_initial_model_name", return_value="deepseek-v3"), \
             patch.object(_M, "_resolve_ollama_host", return_value="http://localhost:11434"), \
             patch.object(_M, "_load_model_config"):
            obj = _M(model_name="deepseek-v3")
            assert "minimax" in obj.fallback_order

    @pytest.mark.asyncio
    async def test_chat_provider_result_inner_routes_minimax(self):
        from src.llm.multi_provider import MultiProviderLLM as _M
        with patch.object(_M, "_resolve_initial_model_name", return_value="minimax-m2.5"), \
             patch.object(_M, "_resolve_ollama_host", return_value="http://localhost:11434"), \
             patch.object(_M, "_load_model_config"):
            obj = _M(model_name="minimax-m2.5")
            mock_result = {"text": "hello", "finish_reason": "stop", "provider_used": "minimax", "model_used": "MiniMax-M2.5"}
            obj._chat_minimax_result = AsyncMock(return_value=mock_result)
            result = await obj._chat_provider_result_inner(
                ProviderType.MINIMAX,
                [{"role": "user", "content": "test"}],
                temperature=0.7,
                max_tokens=4096,
                model="minimax-m2.5",
            )
            assert result["provider_used"] == "minimax"
            obj._chat_minimax_result.assert_called_once()

    @pytest.mark.asyncio
    async def test_chat_minimax_result_temp_clamping(self):
        """MiniMax API refuses temperature=0.0 — verify we clamp to 0.01."""
        from src.llm.multi_provider import MultiProviderLLM as _M
        import httpx

        with patch.object(_M, "_resolve_initial_model_name", return_value="minimax-m2.5"), \
             patch.object(_M, "_resolve_ollama_host", return_value="http://localhost:11434"), \
             patch.object(_M, "_load_model_config"), \
             patch.dict(os.environ, {"MINIMAX_API_KEY": "test"}):
            obj = _M(model_name="minimax-m2.5")

            mock_response = MagicMock()
            mock_response.raise_for_status = MagicMock()
            mock_response.json.return_value = {
                "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]
            }

            mock_http = AsyncMock()
            mock_http.post = AsyncMock(return_value=mock_response)
            obj._http = mock_http

            result = await obj._chat_minimax_result(
                [{"role": "user", "content": "hi"}],
                temperature=0.0,
                max_tokens=100,
            )
            # Check payload — temperature should be 0.01, not 0.0
            call_args = mock_http.post.call_args
            payload = call_args[1]["json"]
            assert payload["temperature"] == 0.01
            assert result["text"] == "ok"


# ── P2.4: o3 + o4-mini ──────────────────────────────────────────────────────

class TestO3O4MiniModels:
    def test_o3_exists(self):
        cfg = get_model_config("o3")
        assert cfg is not None
        assert cfg.provider == ProviderType.OPENAI
        assert cfg.context_window == 200000
        assert cfg.max_output_tokens == 100000
        assert cfg.supports_vision is True

    def test_o4_mini_exists(self):
        cfg = get_model_config("o4-mini")
        assert cfg is not None
        assert cfg.provider == ProviderType.OPENAI
        assert cfg.context_window == 200000
        assert cfg.max_output_tokens == 65536
        assert cfg.supports_vision is True

    def test_o3_skills(self):
        assert "o3" in MODEL_SKILLS
        assert MODEL_SKILLS["o3"]["reasoning"] >= 95

    def test_o4_mini_skills(self):
        assert "o4-mini" in MODEL_SKILLS
        assert MODEL_SKILLS["o4-mini"]["speed"] > MODEL_SKILLS["o3"]["speed"]

    def test_o3_o4mini_capabilities(self):
        for name in ("o3", "o4-mini"):
            cfg = get_model_config(name)
            assert "vision_describe" in cfg.capabilities
            assert "tool_calling" in cfg.capabilities


# ── P2.5: Config panel ───────────────────────────────────────────────────────

class TestConfigPanelP2:
    @pytest.fixture
    def config_schema(self):
        from web.routes.config import _CONFIG_SCHEMA
        return _CONFIG_SCHEMA

    def test_minimax_api_key_in_schema(self, config_schema):
        keys = [e["key"] for e in config_schema]
        assert "MINIMAX_API_KEY" in keys

    def test_brain_code_has_minimax(self, config_schema):
        entry = next(e for e in config_schema if e["key"] == "LUMENA_BRAIN_CODE")
        assert "minimax-m2.5" in entry["options"]
        assert "minimax-m2.7" in entry["options"]

    def test_brain_code_has_o3(self, config_schema):
        entry = next(e for e in config_schema if e["key"] == "LUMENA_BRAIN_CODE")
        assert "o3" in entry["options"]
        assert "o4-mini" in entry["options"]

    def test_brain_vision_has_o3(self, config_schema):
        entry = next(e for e in config_schema if e["key"] == "LUMENA_BRAIN_VISION")
        assert "o3" in entry["options"]
        assert "o4-mini" in entry["options"]

    def test_brain_web_has_minimax(self, config_schema):
        entry = next(e for e in config_schema if e["key"] == "LUMENA_BRAIN_WEB")
        assert "minimax-m2.5" in entry["options"]


# ── Total model count ────────────────────────────────────────────────────────

class TestModelCountP2:
    def test_total_models(self):
        # Au moins 47 modèles statiques — d'autres tests peuvent enregistrer des modèles
        # dynamiques (Ollama auto-config) qui s'ajoutent au dict global en suite complète.
        assert len(MODEL_SKILLS) >= 47
        assert len(AVAILABLE_MODELS) >= len(MODEL_SKILLS)

    def test_all_models_have_skills(self):
        # Only check statically declared models (dynamic Ollama models are excluded)
        for name in MODEL_SKILLS:
            assert name in AVAILABLE_MODELS, f"{name} in MODEL_SKILLS but missing from AVAILABLE_MODELS"
