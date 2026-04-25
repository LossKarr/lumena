"""Tests P1 — capabilities field, new models, helpers, ModelConfig output caps."""
import pytest

from src.llm.providers import (
    AVAILABLE_MODELS,
    MODEL_SKILLS,
    ModelConfig,
    ProviderType,
    get_model_config,
    models_with_capability,
    best_model_for_capability,
)


class TestCapabilitiesField:
    """P1.1 — champ capabilities sur ModelConfig."""

    def test_all_models_have_capabilities(self):
        """Chaque modèle doit avoir au moins une capability."""
        for name, cfg in AVAILABLE_MODELS.items():
            assert cfg.capabilities, f"{name} has empty capabilities"

    def test_capabilities_are_frozenset(self):
        for name, cfg in AVAILABLE_MODELS.items():
            assert isinstance(cfg.capabilities, frozenset), f"{name} capabilities not frozenset"

    def test_gpt54_has_computer_use(self):
        cfg = AVAILABLE_MODELS["gpt-5.4"]
        assert "computer_use" in cfg.capabilities
        assert "vision_describe" in cfg.capabilities
        assert "vision_grounding" in cfg.capabilities

    def test_claude_opus_has_dom_assist(self):
        cfg = AVAILABLE_MODELS["claude-opus-4.6"]
        assert "dom_assist" in cfg.capabilities

    def test_dalle3_removed_from_registry(self):
        assert "dall-e-3" not in AVAILABLE_MODELS

    def test_deepseek_v3_cheap_text(self):
        cfg = AVAILABLE_MODELS["deepseek-v3"]
        assert "cheap_text" in cfg.capabilities
        assert "vision_describe" not in cfg.capabilities

    def test_vision_models_have_vision_describe(self):
        """Tous les modèles avec supports_vision=True doivent avoir vision_describe."""
        for name, cfg in AVAILABLE_MODELS.items():
            if cfg.supports_vision:
                assert "vision_describe" in cfg.capabilities, f"{name}: supports_vision but no vision_describe cap"


class TestNewModels:
    """P1.2 — gpt-5.4-nano, gpt-4o-mini, gemini-2.5-pro."""

    def test_gpt54_nano_exists(self):
        cfg = get_model_config("gpt-5.4-nano")
        assert cfg is not None
        assert cfg.provider == ProviderType.OPENAI
        assert cfg.supports_vision is True
        assert "cheap_text" in cfg.capabilities

    def test_gpt4o_mini_exists(self):
        cfg = get_model_config("gpt-4o-mini")
        assert cfg is not None
        assert cfg.provider == ProviderType.OPENAI
        assert cfg.max_output_tokens == 16384

    def test_gemini25_pro_exists(self):
        cfg = get_model_config("gemini-2.5-pro")
        assert cfg is not None
        assert cfg.provider == ProviderType.GOOGLE
        assert cfg.context_window == 1048576
        assert "computer_use" in cfg.capabilities

    def test_new_models_in_skills(self):
        for name in ["gpt-5.4-nano", "gpt-4o-mini", "gemini-2.5-pro"]:
            assert name in MODEL_SKILLS, f"{name} missing from MODEL_SKILLS"

    def test_total_model_count(self):
        assert len(AVAILABLE_MODELS) >= 39  # P1: 39, grows with P2+


class TestCapabilityHelpers:
    """P1.1 — models_with_capability() + best_model_for_capability()."""

    def test_vision_describe_returns_only_vision_models(self):
        result = models_with_capability("vision_describe", available_only=False)
        for name in result:
            cfg = AVAILABLE_MODELS[name]
            assert "vision_describe" in cfg.capabilities

    def test_computer_use_subset(self):
        result = models_with_capability("computer_use", available_only=False)
        assert len(result) >= 10  # gpt-5.4, claude-*, gemini-*, grok-*

    def test_dom_assist_only_claude(self):
        result = models_with_capability("dom_assist", available_only=False)
        for name in result:
            assert "claude" in name

    def test_nonexistent_capability(self):
        result = models_with_capability("teleportation", available_only=False)
        assert result == []

    def test_best_model_returns_string_or_none(self):
        result = best_model_for_capability("vision_describe")
        # Either a model name or None (if no API keys)
        assert result is None or result in AVAILABLE_MODELS


class TestModelConfigOutputCaps:
    """P1.3 — max_output_tokens correctement défini sur chaque modèle."""

    def test_gpt54_output_128k(self):
        assert AVAILABLE_MODELS["gpt-5.4"].max_output_tokens == 128000

    def test_gpt4o_output_16k(self):
        assert AVAILABLE_MODELS["gpt-4o"].max_output_tokens == 16384

    def test_deepseek_reasoner_output_65k(self):
        assert AVAILABLE_MODELS["deepseek-reasoner"].max_output_tokens == 65536

    def test_deepseek_v3_output_8k(self):
        assert AVAILABLE_MODELS["deepseek-v3"].max_output_tokens == 8192
