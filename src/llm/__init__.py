"""Module LLM multi-provider.

Exports charges a la demande pour eviter que `src.llm.model_profile` importe
les providers, charge le .env et initialise des dependances pendant les tests.
"""

__all__ = [
    "MultiProviderLLM",
    "ProviderType",
    "ModelConfig",
    "AVAILABLE_MODELS",
    "LOCAL_VALIDATED_MODELS",
    "get_model_config",
    "get_available_models",
    "get_local_models",
    "get_cloud_models",
    "get_free_models",
    "check_api_key",
    "get_api_key",
]


def __getattr__(name: str):
    if name == "MultiProviderLLM":
        from .multi_provider import MultiProviderLLM
        return MultiProviderLLM
    if name in {
        "ProviderType", "ModelConfig", "AVAILABLE_MODELS", "LOCAL_VALIDATED_MODELS",
        "get_model_config", "get_available_models", "get_local_models",
        "get_cloud_models", "get_free_models", "check_api_key", "get_api_key",
    }:
        from importlib import import_module
        providers = import_module(f"{__name__}.providers")
        return getattr(providers, name)
    raise AttributeError(name)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
