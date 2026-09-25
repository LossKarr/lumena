"""Contrats Moonshot, DeepSeek, MiniMax et Z.AI (MOD-4)."""

from __future__ import annotations


def test_live_inventory_additions_are_canonical_and_selectable():
    from src.llm.providers import ProviderType, get_model_config

    expected = {
        "kimi-k2.7-code-highspeed": ProviderType.MOONSHOT,
        "deepseek-flash": ProviderType.DEEPSEEK,
        "minimax-m2.7-highspeed": ProviderType.MINIMAX,
        "glm-5.3": ProviderType.ZAI,
        "glm-5.3-flash": ProviderType.ZAI,
        "glm-5.3-flashx": ProviderType.ZAI,
    }
    for name, provider in expected.items():
        cfg = get_model_config(name)
        assert cfg is not None, name
        assert cfg.provider == provider
        assert cfg.model_id.lower() == name.lower()
        assert cfg.is_selectable() is True


def test_deepseek_old_name_is_alias_only():
    from src.llm.providers import AVAILABLE_MODELS, get_model_config, resolve_model_name

    assert "deepseek-v4-flash" not in AVAILABLE_MODELS
    assert resolve_model_name("deepseek-v4-flash") == "deepseek-flash"
    assert get_model_config("deepseek-v4-flash") is get_model_config("deepseek-flash")


def test_glm53_payload_enables_thinking_without_sampling():
    from src.llm.multi_provider import _build_zai_payload

    payload = _build_zai_payload(
        "glm-5.3",
        [{"role": "user", "content": "test"}],
        max_tokens=100,
        temperature=0.7,
        stop=["STOP"],
    )

    assert payload["thinking"] == {"type": "enabled"}
    assert payload["max_tokens"] == 100
    assert "temperature" not in payload
    assert "stop" not in payload


def test_glm53_flash_variants_advertise_native_vision():
    from src.llm.providers import get_model_config

    for name in ("glm-5.3-flash", "glm-5.3-flashx"):
        cfg = get_model_config(name)
        assert cfg.supports_vision is True
        assert "vision_describe" in cfg.capabilities


def test_new_models_are_never_added_silently_to_unrelated_fallbacks():
    from src.llm.providers import MODEL_FALLBACKS

    highspeed = "kimi-k2.7-code-highspeed"
    roots = {root for root, targets in MODEL_FALLBACKS.items() if highspeed in targets}
    assert roots <= {"kimi-k3", "kimi-k2.7-code"}
