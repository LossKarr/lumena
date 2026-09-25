"""Contrats Mistral, NVIDIA NIM et Ollama dynamique (MOD-5)."""

from __future__ import annotations


def test_current_mistral_snapshots_are_present():
    from src.llm.providers import ProviderType, get_model_config

    names = {
        "codestral-2508", "ministral-14b-2512", "ministral-3b-2512",
        "ministral-8b-2512", "mistral-large-2512", "mistral-medium-3-5",
        "mistral-small-2603",
    }
    for name in names:
        cfg = get_model_config(name)
        assert cfg is not None, name
        assert cfg.provider == ProviderType.MISTRAL
        assert cfg.model_id == name


def test_nvidia_allowlist_contains_only_inventory_proven_new_routes():
    from src.llm.providers import ProviderType, get_model_config

    expected = {
        "nvidia-kimi-k3": "moonshotai/kimi-k3",
        "nvidia-nemotron-3.5-lightning": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia-gpt-oss-20b": "openai/gpt-oss-20b",
        "nvidia-glm-5.3": "z-ai/glm-5.3",
        "nvidia-glm-5.3-flash": "z-ai/glm-5.3-flash",
        "nvidia-muse-glimmer-30b": "meta/muse-glimmer-30b",
    }
    for name, model_id in expected.items():
        cfg = get_model_config(name)
        assert cfg is not None
        assert cfg.provider == ProviderType.NVIDIA
        assert cfg.model_id == model_id
        assert cfg.is_selectable() is True


def test_nvidia_routes_absent_from_live_inventory_are_not_routable():
    from src.llm.providers import get_model_config

    absent = {
        "nvidia-deepseek-v4-pro", "nvidia-gpt-oss-120b",
        "nvidia-step-3.7-flash", "nvidia-glm-5.1",
        "nvidia-minimax-m2.7", "nvidia-minimax-m3",
    }
    for name in absent:
        cfg = get_model_config(name)
        assert cfg is not None
        assert cfg.is_selectable() is False
        assert cfg.is_fallback_eligible() is False


def test_retired_nvidia_deepseek_snapshot_is_not_a_fallback():
    from src.llm.providers import ModelLifecycle, get_model_config

    cfg = get_model_config("nvidia-deepseek-v4-flash")
    assert cfg.model_id == "deepseek-ai/deepseek-v4-flash-0731"
    assert cfg.lifecycle == ModelLifecycle.RETIRED
    assert cfg.is_fallback_eligible() is False


def test_ollama_registration_does_not_register_remote_inventory():
    from src.llm.providers import AVAILABLE_MODELS, MODEL_SKILLS, ProviderType, register_ollama_models

    remote_before = {k for k, v in AVAILABLE_MODELS.items() if v.provider != ProviderType.OLLAMA}
    register_ollama_models(["catalog-mod5-local:latest"])
    remote_after = {k for k, v in AVAILABLE_MODELS.items() if v.provider != ProviderType.OLLAMA}

    assert remote_after == remote_before
    assert AVAILABLE_MODELS["catalog-mod5-local-latest"].provider == ProviderType.OLLAMA
    AVAILABLE_MODELS.pop("catalog-mod5-local-latest", None)
    MODEL_SKILLS.pop("catalog-mod5-local-latest", None)
