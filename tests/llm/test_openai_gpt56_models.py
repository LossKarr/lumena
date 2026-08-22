"""Garde-fous d'intégration des modèles OpenAI GPT-5.6."""

from pathlib import Path
from unittest.mock import patch

import pytest


GPT56_MODELS = ("gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna")


@pytest.mark.parametrize(
    ("name", "cost", "badge"),
    (
        ("gpt-5.6-sol", 5.0, "Frontier"),
        ("gpt-5.6-terra", 2.5, "Balanced"),
        ("gpt-5.6-luna", 1.0, "Efficient"),
    ),
)
def test_gpt56_catalog_and_official_contract(name, cost, badge):
    from src.llm.providers import ProviderType, get_model_config

    cfg = get_model_config(name)

    assert cfg is not None
    assert cfg.name == name
    assert cfg.model_id == name
    assert cfg.provider == ProviderType.OPENAI
    assert cfg.context_window == 1_050_000
    assert cfg.max_output_tokens == 128_000
    assert cfg.cost_per_million_tokens == cost
    assert cfg.badge == badge
    assert cfg.supports_vision is True
    assert cfg.supports_tools is True
    assert cfg.supports_image_generation is False
    assert cfg.supports_video_generation is False
    assert {
        "vision_describe",
        "vision_grounding",
        "tool_calling",
        "reasoning",
        "computer_use",
        "long_context",
        "code_generation",
    }.issubset(cfg.capabilities)
    assert "dom_assist" not in cfg.capabilities


def test_gpt56_alias_is_not_a_duplicate_catalog_entry():
    from src.llm.providers import AVAILABLE_MODELS

    assert "gpt-5.6" not in AVAILABLE_MODELS


def test_gpt56_fallbacks_are_ordered_and_available():
    from src.llm.providers import get_model_fallbacks

    assert get_model_fallbacks("gpt-5.6-sol") == [
        "gpt-5.5",
        "gpt-5.6-terra",
        "nvidia-nemotron-3-ultra-550b-a55b",
        "nvidia-gpt-oss-120b",
    ]
    assert get_model_fallbacks("gpt-5.6-terra") == [
        "gpt-5.4",
        "gpt-5.6-luna",
        "nvidia-gpt-oss-120b",
        "nvidia-nemotron-3-ultra-550b-a55b",
    ]
    assert get_model_fallbacks("gpt-5.6-luna") == [
        "gpt-5.4-mini",
        "nvidia-gpt-oss-120b",
        "nvidia-deepseek-v4-flash",
    ]


def test_gpt56_uses_existing_openai_behavior_profile():
    from src.llm.model_profile import get_model_profile

    for name in GPT56_MODELS:
        profile = get_model_profile(name)
        assert profile.parser_severity == "strict"
        assert profile.thought_leak_risk == "low"
        assert profile.tool_call_quality == "excellent"
        assert profile.react_stability == "stable"


def test_gpt56_is_explicitly_selectable_without_changing_openai_auto_winner():
    from src.llm.providers import MODEL_SKILLS, best_model_for

    for name in GPT56_MODELS:
        assert name in MODEL_SKILLS

    candidates = ["gpt-5.5", *GPT56_MODELS]
    with patch("src.llm.providers.check_api_key", return_value=True):
        for domain in ("code", "speed", "reasoning", "creative", "research", "vision", "web"):
            assert best_model_for(domain, candidates) == "gpt-5.5"


def test_gpt56_vision_capability_keeps_gpt55_first_for_direct_openai_vision():
    from src.llm.providers import AVAILABLE_MODELS, ProviderType, models_with_capability

    with patch("src.llm.providers.check_api_key", return_value=True):
        models = [
            name
            for name in models_with_capability("vision_describe")
            if AVAILABLE_MODELS[name].provider == ProviderType.OPENAI
        ]

    assert all(name in models for name in GPT56_MODELS)
    assert models.index("gpt-5.5") < models.index("gpt-5.6-sol")


def test_gpt56_config_lists_and_image_generation_exclusion():
    from web.routes.config import _CONFIG_SCHEMA

    schema = {entry["key"]: entry for entry in _CONFIG_SCHEMA}
    text_model_keys = (
        "LUMENA_DEFAULT_MODEL",
        "LUMENA_AGENT_CODE_MODEL",
        "LUMENA_AGENT_RESEARCH_MODEL",
        "LUMENA_AGENT_GENERAL_MODEL",
        "LUMENA_BRAIN_VISION",
        "LUMENA_BRAIN_CODE",
        "LUMENA_BRAIN_WEB",
    )

    for name in GPT56_MODELS:
        for key in text_model_keys:
            assert name in schema[key]["options"], f"{name} absent de {key}"
        assert name not in schema["LUMENA_BRAIN_IMAGE_GEN"]["options"]


def test_gpt56_setup_wizard_recommendations():
    setup_text = Path("web/routes/setup.py").read_text(encoding="utf-8")

    for name in GPT56_MODELS:
        assert setup_text.count(f'"{name}"') == 3


@pytest.mark.parametrize("name", GPT56_MODELS)
def test_gpt56_uses_the_existing_gpt5_chat_completions_payload(name, monkeypatch):
    from src.llm.multi_provider import MultiProviderLLM

    monkeypatch.delenv("LUMENA_OPENAI_REASONING_EFFORT", raising=False)
    tools = [{"type": "function", "function": {"name": "probe"}}]
    payload = MultiProviderLLM._build_openai_payload(
        name,
        [
            {"role": "system", "content": "Tu es Lumena"},
            {"role": "user", "content": "Teste le modèle"},
        ],
        temperature=0.7,
        max_tokens=321,
        stop=["OBSERVATION:"],
        tools=tools,
        stream=True,
    )

    assert MultiProviderLLM._is_gpt5_model(name) is True
    assert payload["model"] == name
    assert payload["messages"][0]["role"] == "developer"
    assert payload["max_completion_tokens"] == 321
    assert payload["tools"] == tools
    assert payload["stream"] is True
    assert "max_tokens" not in payload
    assert "temperature" not in payload
    assert "stop" not in payload
    assert "reasoning_effort" not in payload
