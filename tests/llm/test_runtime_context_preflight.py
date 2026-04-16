"""Tests — Phase 2 : RuntimeContext preflight + build_runtime_snapshot + fallback chain."""
import pytest
from unittest.mock import patch, MagicMock

from src.core_services.runtime_context import RuntimeContext
from src.llm.providers import (
    ModelConfig, ProviderType, get_default_model_for_provider,
    AVAILABLE_MODELS,
)


# ── RuntimeContext dataclass ────────────────────────────────────────────

class TestRuntimeContextPreflight:
    def test_create_with_defaults(self):
        ctx = RuntimeContext(active_model="deepseek-v3", active_provider="deepseek")
        assert ctx.active_model == "deepseek-v3"
        assert ctx.active_provider == "deepseek"
        assert ctx.max_context_window == 32000
        assert ctx.max_output_tokens == 4096
        assert ctx.budget_seconds == 900.0
        assert ctx.source_channel == "web"
        assert ctx.mode == "agent"
        assert ctx.fallback_order == []

    def test_create_with_values(self):
        ctx = RuntimeContext(
            active_model="claude-opus-4",
            active_provider="anthropic",
            max_context_window=1000000,
            max_output_tokens=128000,
            providers_health={"anthropic": True, "ollama": True, "deepseek": False},
            healthy_providers=["anthropic", "ollama"],
            budget_seconds=300.0,
            source_channel="telegram",
            mode="chat",
            fallback_order=["anthropic", "ollama"],
        )
        assert ctx.max_context_window == 1000000
        assert ctx.max_output_tokens == 128000
        assert ctx.source_channel == "telegram"
        assert ctx.mode == "chat"
        assert ctx.budget_seconds == 300.0
        assert len(ctx.healthy_providers) == 2
        assert "deepseek" not in ctx.healthy_providers

    def test_frozen(self):
        ctx = RuntimeContext(active_model="x", active_provider="ollama")
        with pytest.raises(Exception):
            ctx.active_model = "y"


# ── get_default_model_for_provider ──────────────────────────────────────

class TestGetDefaultModelForProvider:
    def test_ollama(self):
        m = get_default_model_for_provider("ollama")
        assert m is not None
        assert m.provider == ProviderType.OLLAMA

    def test_openai(self):
        m = get_default_model_for_provider("openai")
        assert m is not None
        assert m.provider == ProviderType.OPENAI

    def test_anthropic(self):
        m = get_default_model_for_provider("anthropic")
        assert m is not None
        assert m.provider == ProviderType.ANTHROPIC

    def test_deepseek(self):
        m = get_default_model_for_provider("deepseek")
        assert m is not None
        assert m.provider == ProviderType.DEEPSEEK

    def test_unknown_provider(self):
        m = get_default_model_for_provider("notexist")
        assert m is None

    def test_all_providers_have_models(self):
        for pt in ProviderType:
            m = get_default_model_for_provider(pt.value)
            assert m is not None, f"No model for provider {pt.value}"


# ── max_output_tokens dans ModelConfig ──────────────────────────────────

class TestMaxOutputTokensConfig:
    def test_all_models_have_max_output(self):
        for name, cfg in AVAILABLE_MODELS.items():
            if getattr(cfg, 'supports_image_generation', False):
                continue  # image-gen models (dall-e-3) n'ont pas de max_output_tokens
            assert cfg.max_output_tokens > 0, f"{name} missing max_output_tokens"

    def test_gpt54_has_128k_output(self):
        cfg = AVAILABLE_MODELS["gpt-5.4"]
        assert cfg.max_output_tokens == 128000

    def test_claude_opus_has_128k_output(self):
        cfg = AVAILABLE_MODELS["claude-opus-4.6"]
        assert cfg.max_output_tokens == 128000

    def test_deepseek_v3_has_8k_output(self):
        cfg = AVAILABLE_MODELS["deepseek-v3"]
        assert cfg.max_output_tokens == 8192

    def test_ollama_models_have_reasonable_output(self):
        for name, cfg in AVAILABLE_MODELS.items():
            if cfg.provider == ProviderType.OLLAMA:
                assert cfg.max_output_tokens in (4096, 8192), f"{name} = {cfg.max_output_tokens}"


# ── build_runtime_snapshot ──────────────────────────────────────────────

class TestBuildRuntimeSnapshot:
    def test_basic(self):
        from src.llm.multi_provider import MultiProviderLLM
        llm = MultiProviderLLM()
        ctx = llm.build_runtime_snapshot()
        assert ctx.active_model == llm.model_name
        assert ctx.active_provider == llm.provider.value
        assert ctx.max_context_window == llm.context_window
        assert ctx.max_output_tokens == llm.max_output_tokens
        assert ctx.source_channel == "web"
        assert ctx.mode == "agent"

    def test_telegram_channel(self):
        from src.llm.multi_provider import MultiProviderLLM
        llm = MultiProviderLLM()
        ctx = llm.build_runtime_snapshot(source_channel="telegram", mode="chat")
        assert ctx.source_channel == "telegram"
        assert ctx.mode == "chat"

    def test_healthy_providers_all_healthy(self):
        from src.llm.multi_provider import MultiProviderLLM
        llm = MultiProviderLLM()
        ctx = llm.build_runtime_snapshot()
        assert len(ctx.healthy_providers) == len(llm.provider_health)
        assert all(ctx.providers_health.values())

    def test_fallback_order_filters_unhealthy(self):
        from src.llm.multi_provider import MultiProviderLLM
        llm = MultiProviderLLM()
        llm.provider_health["openai"]["healthy"] = False
        ctx = llm.build_runtime_snapshot()
        assert "openai" not in ctx.fallback_order
        assert "openai" not in ctx.healthy_providers
        llm.provider_health["openai"]["healthy"] = True

    def test_budget_seconds(self):
        from src.llm.multi_provider import MultiProviderLLM
        llm = MultiProviderLLM()
        ctx = llm.build_runtime_snapshot(budget_seconds=60.0)
        assert ctx.budget_seconds == 60.0
