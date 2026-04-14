"""
🌟 LUMENA - CU Router

Orchestre le routage vision et l'acquisition d'état pour Computer Use.

Deux couches exactement séparées :
  1. Vision routing  — LLM providers uniquement (OpenAI / Anthropic / Google / xAI / Ollama)
  2. State policy    — DOM / UIA / OCR (sources structurées, jamais un LLM ici)

Invariant : route_cu_vision() ne reçoit JAMAIS dom / uia / ocr dans sa cascade.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Dict, List, Optional

from loguru import logger

if TYPE_CHECKING:
    from .vision import VisionModule

# ---------------------------------------------------------------------------
# Env var helpers
# ---------------------------------------------------------------------------

def get_execution_mode() -> str:
    """Retourne le mode : cloud | hybrid | local. Défaut: hybrid."""
    return os.getenv("LUMENA_EXECUTION_MODE", "hybrid").lower()


# ---------------------------------------------------------------------------
# Vision policy (LLM providers uniquement)
# ---------------------------------------------------------------------------

_DEFAULT_VISION_POLICY: Dict[str, List[str]] = {
    "vision_describe":  ["openai", "anthropic", "google", "xai"],
    "vision_grounding": ["anthropic", "google", "openai", "xai"],
}

_LOCAL_VISION_POLICY: Dict[str, List[str]] = {
    "vision_describe":  ["ollama"],
    "vision_grounding": ["ollama"],
}

# Mapping provider name → ProviderType (import lazy pour éviter cycles)
_PROVIDER_NAME_MAP = {
    "openai":    "OPENAI",
    "anthropic": "ANTHROPIC",
    "google":    "GOOGLE",
    "xai":       "XAI",
    "minimax":   "MINIMAX",
    "moonshot":  "MOONSHOT",
    "nvidia":    "NVIDIA",
    "deepseek":  "DEEPSEEK",
}


def _has_api_key(provider_name: str) -> bool:
    """Vérifie la clé API d'un provider par son nom (string)."""
    if provider_name == "ollama":
        return True  # pas de clé nécessaire
    try:
        from src.llm.providers import ProviderType, check_api_key
        enum_name = _PROVIDER_NAME_MAP.get(provider_name)
        if not enum_name:
            return False
        ptype = ProviderType(provider_name)
        return check_api_key(ptype)
    except Exception:
        # Fallback : regarder directement l'env var
        key_map = {
            "openai":    "OPENAI_API_KEY",
            "anthropic": "ANTHROPIC_API_KEY",
            "google":    "GOOGLE_API_KEY",
            "xai":       "XAI_API_KEY",
            "minimax":   "MINIMAX_API_KEY",
        }
        var = key_map.get(provider_name)
        return bool(os.getenv(var)) if var else False


def build_vision_policy(capability: str) -> List[str]:
    """
    Retourne la liste ordonnée de providers LLM pour la capacité demandée.

    Règles :
    - mode=local             → ["ollama"]
    - mode=cloud/hybrid      → liste cloud filtrée par clé API dispo
    - LUMENA_CU_VISION_ORDER → override complet (liste séparée par virgules)
    - LUMENA_CU_OLLAMA_VISION=1 + mode hybrid → ajoute "ollama" en queue cloud

    Invariant : jamais "dom", "uia", "ocr" dans le résultat.
    """
    override = os.getenv("LUMENA_CU_VISION_ORDER", "").strip()
    if override:
        providers = [p.strip() for p in override.split(",") if p.strip()]
        # Filtrer les non-LLM au cas où
        providers = [p for p in providers if p not in ("dom", "uia", "ocr")]
        return providers

    mode = get_execution_mode()

    if mode == "local":
        return list(_LOCAL_VISION_POLICY.get(capability, ["ollama"]))

    # cloud ou hybrid
    policy = list(_DEFAULT_VISION_POLICY.get(capability, ["openai", "anthropic", "google", "xai"]))

    # Filtrer les providers sans clé API
    policy = [p for p in policy if _has_api_key(p)]

    if mode == "hybrid" and os.getenv("LUMENA_CU_OLLAMA_VISION", "0") == "1":
        if "ollama" not in policy:
            policy.append("ollama")

    return policy


# ---------------------------------------------------------------------------
# State policy (DOM / UIA / OCR — sources structurées, pas de LLM)
# ---------------------------------------------------------------------------

_DEFAULT_STATE_POLICY: Dict[str, List[str]] = {
    "desktop": ["uia"],
    "web":     ["dom"],
}

_LOCAL_STATE_POLICY: Dict[str, List[str]] = {
    "desktop": ["uia", "ocr"],
    "web":     ["dom", "ocr"],
}


def build_state_policy(context: str) -> List[str]:
    """
    Retourne la liste ordonnée de sources d'état structuré.

    context : "web" | "desktop"
    Les sources retournées sont uniquement : dom, uia, ocr — jamais un LLM.
    """
    mode = get_execution_mode()
    if mode == "local":
        return list(_LOCAL_STATE_POLICY.get(context, ["uia", "ocr"]))
    return list(_DEFAULT_STATE_POLICY.get(context, ["uia"]))


# ---------------------------------------------------------------------------
# Vision routing
# ---------------------------------------------------------------------------

async def route_cu_vision(
    vision: "VisionModule",
    image_path: str,
    prompt: str,
    *,
    capability: str = "vision_describe",
    cascade: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Orchestre les appels LLM vision en cascadant les providers disponibles.

    Retourne :
      {"success": True,  "text": <réponse>, "provider": <nom>}
    ou
      {"success": False, "error": "all vision providers failed"}

    Invariant strict : dom / uia / ocr ne passent JAMAIS ici.
    """
    policy = cascade if cascade is not None else build_vision_policy(capability)

    # Sécurité — retirer silencieusement toute source non-LLM
    policy = [p for p in policy if p not in ("dom", "uia", "ocr")]

    if not policy:
        logger.warning("route_cu_vision: aucun provider disponible (policy vide)")
        return {"success": False, "error": "no vision providers available"}

    last_error = ""
    for provider in policy:
        if not vision._is_provider_available(provider):
            logger.debug(f"route_cu_vision: skip {provider} (cooldown / permanent)")
            continue

        try:
            text = await vision._call_analyze(provider, image_path, prompt)
            logger.debug(f"route_cu_vision: succès via {provider}")
            return {"success": True, "text": text, "provider": provider}
        except Exception as exc:
            last_error = str(exc)
            logger.warning(f"route_cu_vision: échec {provider}: {exc}")
            vision._record_provider_failure(provider, exc)

    logger.error(f"route_cu_vision: tous les providers ont échoué. Dernier: {last_error}")
    return {"success": False, "error": f"all vision providers failed: {last_error}"}
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
