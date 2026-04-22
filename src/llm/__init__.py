"""
🌟 LUMENA - Module LLM Multi-Provider

Ce module fournit des clients LLM pour différents providers.
"""

from .providers import (
    ProviderType, ModelConfig, AVAILABLE_MODELS, LOCAL_VALIDATED_MODELS,
    get_model_config, get_available_models,
    get_local_models, get_cloud_models, get_free_models,
    check_api_key, get_api_key
)
from .multi_provider import MultiProviderLLM

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
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
