"""MOD-9 — modèles publiés les 28-29 septembre 2026."""

from __future__ import annotations


def test_gpt61_sol_catalog_contract():
    from src.llm.providers import ModelLifecycle, ProviderType, get_model_config

    cfg = get_model_config("gpt-6.1-sol")
    assert cfg is not None
    assert cfg.provider == ProviderType.OPENAI
    assert cfg.model_id == "gpt-6.1-sol"
    assert cfg.context_window == 1_050_000
    assert cfg.max_output_tokens == 128_000
    assert cfg.lifecycle == ModelLifecycle.STABLE
    assert cfg.supports_vision is True
    assert cfg.supports_tools is True
    assert cfg.pricing is not None
    assert (
        cfg.pricing.input_per_million,
        cfg.pricing.cached_input_per_million,
        cfg.pricing.cache_write_per_million,
        cfg.pricing.output_per_million,
    ) == (2.0, 0.10, 2.50, 10.0)
    assert cfg.pricing.long_context_threshold == 272_000
    assert cfg.pricing.verified_on == "2026-09-30"
    assert {"responses_api", "reasoning", "computer_use", "tool_calling"} <= cfg.capabilities


def test_claude_sonnet55_catalog_contract():
    from src.llm.providers import ModelLifecycle, ProviderType, get_model_config

    cfg = get_model_config("claude-sonnet-5.5")
    assert cfg is not None
    assert cfg.provider == ProviderType.ANTHROPIC
    assert cfg.model_id == "claude-sonnet-5-5"
    assert cfg.context_window == 1_000_000
    assert cfg.max_output_tokens == 128_000
    assert cfg.lifecycle == ModelLifecycle.STABLE
    assert cfg.supports_vision is True
    assert cfg.supports_tools is True
    assert cfg.pricing is not None
    assert (
        cfg.pricing.input_per_million,
        cfg.pricing.cached_input_per_million,
        cfg.pricing.cache_write_per_million,
        cfg.pricing.output_per_million,
    ) == (2.0, 0.20, 2.50, 10.0)
    assert cfg.pricing.verified_on == "2026-09-30"
    assert {"reasoning", "computer_use", "tool_calling", "code_generation"} <= cfg.capabilities


def test_new_models_have_bounded_fallbacks_with_nvidia_last():
    from src.llm.providers import get_model_fallbacks

    assert get_model_fallbacks("gpt-6.1-sol") == [
        "gpt-6-sol",
        "gpt-6-luna",
        "claude-sonnet-5.5",
        "deepseek-flash",
        "nvidia-gpt-oss-20b",
    ]
    assert get_model_fallbacks("claude-sonnet-5.5") == [
        "gpt-6.1-sol",
        "deepseek-flash",
        "nvidia-gpt-oss-20b",
    ]


def test_gpt61_sol_uses_responses_and_rejects_unsupported_none_effort():
    from src.llm.multi_provider import MultiProviderLLM

    assert MultiProviderLLM._openai_uses_responses("gpt-6.1-sol") is True
    payload = MultiProviderLLM._build_openai_responses_payload(
        "gpt-6.1-sol",
        [{"role": "system", "content": "safe"}, {"role": "user", "content": "run"}],
        max_tokens=321,
        reasoning_effort="none",
    )
    assert payload["reasoning"] == {"effort": "medium"}
    assert payload["max_output_tokens"] == 321
    assert payload["input"][0]["role"] == "developer"


def test_sonnet55_payload_is_sampling_safe_and_uses_adaptive_effort(monkeypatch):
    from src.llm.multi_provider import _build_anthropic_payload

    monkeypatch.setenv("LUMENA_ANTHROPIC_EFFORT", "xhigh")
    payload = _build_anthropic_payload(
        "claude-sonnet-5-5",
        [{"role": "user", "content": "run"}],
        max_tokens=456,
        temperature=0.7,
        tools=[{"name": "probe", "description": "probe", "input_schema": {"type": "object"}}],
        tool_choice="auto",
    )
    assert payload["output_config"] == {"effort": "xhigh"}
    assert payload["tool_choice"] == {"type": "auto"}
    assert "temperature" not in payload


def test_new_models_are_profiled_ranked_and_catalog_is_valid():
    from src.llm.model_profile import get_model_profile
    from src.llm.providers import MODEL_SKILLS, validate_model_catalog

    for name in ("gpt-6.1-sol", "claude-sonnet-5.5"):
        profile = get_model_profile(name)
        assert profile.parser_severity == "strict"
        assert profile.tool_call_quality == "excellent"
        assert profile.sub_agent_stability == "stable"
        assert name in MODEL_SKILLS
    assert validate_model_catalog() == []


def test_xai_quality_slug_is_kept_for_compatibility_but_not_selectable_or_automatic():
    from src.services.image_gen import _MODEL_CATALOG, _MODEL_PROVIDER, _PROVIDER_FALLBACK_ORDER

    name = "grok-imagine-image-quality"
    assert _MODEL_PROVIDER[name] == "xai"
    assert _MODEL_CATALOG[name].lifecycle == "deprecated"
    assert _MODEL_CATALOG[name].selectable is False
    assert _MODEL_CATALOG[name].auto_eligible is False
    assert name not in _PROVIDER_FALLBACK_ORDER


def test_current_google_image_models_were_already_integrated():
    from src.services.image_gen import _MODEL_CATALOG, _MODEL_PROVIDER

    for name in (
        "gemini-3.1-flash-image",
        "gemini-3.1-flash-lite-image",
        "gemini-3-pro-image",
    ):
        assert _MODEL_PROVIDER[name] == "gemini"
        assert _MODEL_CATALOG[name].selectable is True


def test_new_model_names_are_understood_in_natural_language():
    from src.core_services.agent_service import AgentService

    service = AgentService.__new__(AgentService)
    assert service._match_model_alias("passe sur Claude Sonnet 5.5") == "claude-sonnet-5.5"
    assert service._match_model_alias("utilise GPT 6.1 Sol") == "gpt-6.1-sol"
