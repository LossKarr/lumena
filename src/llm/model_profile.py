"""
P5 — Profils comportementaux par modèle.

Chaque modèle ou famille de modèles a des quirks connus :
format compliance, thought_leak, action_inline, qualité tool-calling,
stabilité ReAct, taux de réponse vide, timeouts.

Ce module expose un profil statique (knowledge-based) par modèle/provider
et des helpers pour adapter le runtime dynamiquement sans changer la logique
centrale.

Usage :
    from src.llm.model_profile import get_model_profile
    profile = get_model_profile("kimi-k2.5")
    base_timeout = int(240 * profile.timeout_multiplier)
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass(frozen=True)
class ModelBehaviorProfile:
    """Profil comportemental d'un modèle LLM pour le runtime Lumena.

    Toutes les valeurs sont des estimations stables tirées de l'expérience
    réelle avec chaque modèle. Elles n'ont pas vocation à être parfaites,
    mais à rendre le runtime robuste face aux modèles faibles.
    """

    # ── Format et parsing ────────────────────────────────────────────────────
    # "strict"    → le modèle suit le format ReAct très bien
    # "lenient"   → défauts mineurs tolérés (ACTION inline rare, etc.)
    # "forgiving" → défauts fréquents, parser doit compenser agressivement
    parser_severity: str = "lenient"

    # ── Risques comportementaux ──────────────────────────────────────────────
    # Tendance à halluciner des blocs OBSERVATION/ACTION dans le THOUGHT
    thought_leak_risk: str = "low"     # "low" | "medium" | "high"
    # Tendance à écrire ACTION inline (pas en début de ligne)
    action_inline_risk: str = "low"    # "low" | "medium" | "high"
    # Tendance à répéter la même action en boucle
    loop_risk: str = "low"             # "low" | "medium" | "high"

    # ── Qualité outils ───────────────────────────────────────────────────────
    # "excellent" | "good" | "moderate" | "poor"
    tool_call_quality: str = "good"
    # "stable" | "moderate" | "unstable"
    react_stability: str = "stable"
    # "stable" | "moderate" | "unstable"
    sub_agent_stability: str = "stable"

    # ── Réponses vides ───────────────────────────────────────────────────────
    # "rare" | "occasional" | "frequent"
    empty_response_risk: str = "rare"
    # Retenter si réponse vide (False uniquement si le modèle est très fiable)
    retry_on_empty: bool = True

    # ── Timeouts ─────────────────────────────────────────────────────────────
    # Multiplicateur appliqué sur le timeout de base (240s pour la plupart).
    # Ex: 1.25 → timeout_base * 1.25 = 300s pour les grands modèles.
    timeout_multiplier: float = 1.0

    # ── Compaction du contexte ───────────────────────────────────────────────
    # Déclenche la compaction d'urgence quand ctx_used > ctx_max * threshold.
    # Les modèles peu stables bénéficient d'un seuil plus bas (compaction plus tôt).
    compact_ctx_threshold: float = 0.75

    # ── Sectionnement fichiers ───────────────────────────────────────────────
    # Pour les modèles avec petite fenêtre ou instables, sectionner les gros
    # fichiers avant traitement. 0 = pas de sectionnement forcé.
    file_section_threshold: int = 0    # chars; 0 = désactivé

    # ── Plafond itérations sub-agent ─────────────────────────────────────────
    # Les modèles instables convergent moins bien → budget plus faible pour éviter
    # de brûler des tokens. 0 = utiliser la valeur par défaut du code.
    sub_agent_iter_cap: int = 0        # 0 = pas de cap supplémentaire


# ─────────────────────────────────────────────────────────────────────────────
# Profils par provider (fallback quand le modèle exact n'est pas connu)
# ─────────────────────────────────────────────────────────────────────────────

_PROVIDER_DEFAULTS: Dict[str, ModelBehaviorProfile] = {
    "anthropic": ModelBehaviorProfile(
        parser_severity="strict",
        thought_leak_risk="low",
        action_inline_risk="low",
        loop_risk="low",
        tool_call_quality="excellent",
        react_stability="stable",
        sub_agent_stability="stable",
        empty_response_risk="rare",
        retry_on_empty=True,
        timeout_multiplier=1.0,
        compact_ctx_threshold=0.78,
    ),
    "openai": ModelBehaviorProfile(
        parser_severity="strict",
        thought_leak_risk="low",
        action_inline_risk="low",
        loop_risk="low",
        tool_call_quality="excellent",
        react_stability="stable",
        sub_agent_stability="stable",
        empty_response_risk="rare",
        retry_on_empty=True,
        timeout_multiplier=1.0,
        compact_ctx_threshold=0.78,
    ),
    "google": ModelBehaviorProfile(
        parser_severity="lenient",
        thought_leak_risk="low",
        action_inline_risk="medium",
        loop_risk="low",
        tool_call_quality="good",
        react_stability="moderate",
        sub_agent_stability="stable",
        empty_response_risk="rare",
        retry_on_empty=True,
        timeout_multiplier=1.0,
        compact_ctx_threshold=0.75,
    ),
    "deepseek": ModelBehaviorProfile(
        parser_severity="lenient",
        thought_leak_risk="medium",
        action_inline_risk="low",
        loop_risk="low",
        tool_call_quality="good",
        react_stability="stable",
        sub_agent_stability="stable",
        empty_response_risk="rare",
        retry_on_empty=True,
        timeout_multiplier=1.0,
        compact_ctx_threshold=0.75,
    ),
    "moonshot": ModelBehaviorProfile(
        # Kimi K2 est un grand modèle (631B MoE) → plus lent
        # Tendance forte au thought_leak et ACTION inline
        parser_severity="forgiving",
        thought_leak_risk="high",
        action_inline_risk="high",
        loop_risk="medium",
        tool_call_quality="moderate",
        react_stability="moderate",
        sub_agent_stability="moderate",
        empty_response_risk="occasional",
        retry_on_empty=True,
        timeout_multiplier=1.25,
        compact_ctx_threshold=0.70,
        file_section_threshold=80_000,
        sub_agent_iter_cap=35,
    ),
    "xai": ModelBehaviorProfile(
        parser_severity="lenient",
        thought_leak_risk="low",
        action_inline_risk="low",
        loop_risk="low",
        tool_call_quality="good",
        react_stability="stable",
        sub_agent_stability="stable",
        empty_response_risk="rare",
        retry_on_empty=True,
        timeout_multiplier=1.0,
        compact_ctx_threshold=0.75,
    ),
    "nvidia": ModelBehaviorProfile(
        # NVIDIA NIM = variantes gratuites souvent plus petites → instables
        parser_severity="forgiving",
        thought_leak_risk="medium",
        action_inline_risk="medium",
        loop_risk="medium",
        tool_call_quality="moderate",
        react_stability="moderate",
        sub_agent_stability="moderate",
        empty_response_risk="occasional",
        retry_on_empty=True,
        timeout_multiplier=1.0,
        compact_ctx_threshold=0.70,
        sub_agent_iter_cap=30,
    ),
    "minimax": ModelBehaviorProfile(
        parser_severity="lenient",
        thought_leak_risk="low",
        action_inline_risk="low",
        loop_risk="low",
        tool_call_quality="good",
        react_stability="moderate",
        sub_agent_stability="moderate",
        empty_response_risk="rare",
        retry_on_empty=True,
        timeout_multiplier=1.0,
        compact_ctx_threshold=0.75,
    ),
    "zai": ModelBehaviorProfile(
        # Z.AI GLM — bonne qualité mais taux réponse vide plus élevé sur petits modèles
        parser_severity="lenient",
        thought_leak_risk="low",
        action_inline_risk="medium",
        loop_risk="low",
        tool_call_quality="good",
        react_stability="moderate",
        sub_agent_stability="moderate",
        empty_response_risk="occasional",
        retry_on_empty=True,
        timeout_multiplier=1.0,
        compact_ctx_threshold=0.75,
    ),
    "ollama": ModelBehaviorProfile(
        # Modèles locaux — très variables selon la taille et le quantization
        parser_severity="forgiving",
        thought_leak_risk="high",
        action_inline_risk="high",
        loop_risk="high",
        tool_call_quality="poor",
        react_stability="unstable",
        sub_agent_stability="unstable",
        empty_response_risk="frequent",
        retry_on_empty=True,
        timeout_multiplier=1.5,
        compact_ctx_threshold=0.65,
        file_section_threshold=40_000,
        sub_agent_iter_cap=20,
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# Overrides par modèle exact (surpassent le default provider)
# ─────────────────────────────────────────────────────────────────────────────

_MODEL_OVERRIDES: Dict[str, ModelBehaviorProfile] = {
    # ── Anthropic flagship (très stable) ────────────────────────────────────
    "claude-opus-4.8": ModelBehaviorProfile(
        parser_severity="strict",
        thought_leak_risk="low",
        action_inline_risk="low",
        loop_risk="low",
        tool_call_quality="excellent",
        react_stability="stable",
        sub_agent_stability="stable",
        empty_response_risk="rare",
        retry_on_empty=False,
        timeout_multiplier=1.0,
        compact_ctx_threshold=0.80,
    ),
    "claude-opus-4.7": ModelBehaviorProfile(
        parser_severity="strict",
        thought_leak_risk="low",
        action_inline_risk="low",
        loop_risk="low",
        tool_call_quality="excellent",
        react_stability="stable",
        sub_agent_stability="stable",
        empty_response_risk="rare",
        retry_on_empty=False,
        timeout_multiplier=1.0,
        compact_ctx_threshold=0.80,
    ),
    "claude-opus-4.6": ModelBehaviorProfile(
        parser_severity="strict",
        thought_leak_risk="low",
        action_inline_risk="low",
        loop_risk="low",
        tool_call_quality="excellent",
        react_stability="stable",
        sub_agent_stability="stable",
        empty_response_risk="rare",
        retry_on_empty=False,
        timeout_multiplier=1.0,
        compact_ctx_threshold=0.80,
    ),
    "claude-sonnet-4.6": ModelBehaviorProfile(
        parser_severity="strict",
        thought_leak_risk="low",
        action_inline_risk="low",
        loop_risk="low",
        tool_call_quality="excellent",
        react_stability="stable",
        sub_agent_stability="stable",
        empty_response_risk="rare",
        retry_on_empty=False,
        timeout_multiplier=1.0,
        compact_ctx_threshold=0.80,
    ),
    # ── DeepSeek V4 ─────────────────────────────────────────────────────────
    "deepseek-v4-pro": ModelBehaviorProfile(
        parser_severity="lenient",
        thought_leak_risk="medium",
        action_inline_risk="low",
        loop_risk="low",
        tool_call_quality="good",
        react_stability="stable",
        sub_agent_stability="stable",
        empty_response_risk="rare",
        retry_on_empty=True,
        timeout_multiplier=1.0,
        compact_ctx_threshold=0.75,
    ),
    "deepseek-v4-flash": ModelBehaviorProfile(
        parser_severity="lenient",
        thought_leak_risk="medium",
        action_inline_risk="low",
        loop_risk="low",
        tool_call_quality="good",
        react_stability="stable",
        sub_agent_stability="stable",
        empty_response_risk="rare",
        retry_on_empty=True,
        timeout_multiplier=1.0,
        compact_ctx_threshold=0.75,
    ),
    # ── DeepSeek V3.2 (déprécié) — reasoner instable pour boucles longues ──
    "deepseek-reasoner": ModelBehaviorProfile(
        parser_severity="lenient",
        thought_leak_risk="high",  # le CoT long déborde dans le THOUGHT
        action_inline_risk="medium",
        loop_risk="medium",
        tool_call_quality="moderate",
        react_stability="moderate",
        sub_agent_stability="moderate",
        empty_response_risk="occasional",
        retry_on_empty=True,
        timeout_multiplier=1.10,
        compact_ctx_threshold=0.70,
        sub_agent_iter_cap=30,
    ),
    # ── Kimi K2 — grand modèle MoE, lent et thought-leaky ───────────────────
    "kimi-k2.5": ModelBehaviorProfile(
        parser_severity="forgiving",
        thought_leak_risk="high",
        action_inline_risk="high",
        loop_risk="medium",
        tool_call_quality="moderate",
        react_stability="moderate",
        sub_agent_stability="moderate",
        empty_response_risk="occasional",
        retry_on_empty=True,
        timeout_multiplier=1.25,
        compact_ctx_threshold=0.68,
        file_section_threshold=80_000,
        sub_agent_iter_cap=35,
    ),
    "kimi-k2.6": ModelBehaviorProfile(
        parser_severity="forgiving",
        thought_leak_risk="high",
        action_inline_risk="high",
        loop_risk="medium",
        tool_call_quality="moderate",
        react_stability="moderate",
        sub_agent_stability="moderate",
        empty_response_risk="occasional",
        retry_on_empty=True,
        timeout_multiplier=1.25,
        compact_ctx_threshold=0.68,
        file_section_threshold=80_000,
        sub_agent_iter_cap=35,
    ),
    # ── OpenAI reasoning (o3/o4) — excellent mais output parfois tronqué ────
    "o3": ModelBehaviorProfile(
        parser_severity="strict",
        thought_leak_risk="low",
        action_inline_risk="low",
        loop_risk="low",
        tool_call_quality="excellent",
        react_stability="stable",
        sub_agent_stability="stable",
        empty_response_risk="rare",
        retry_on_empty=False,
        timeout_multiplier=1.15,  # o3 est plus lent que GPT-5.4
        compact_ctx_threshold=0.78,
    ),
    "o4-mini": ModelBehaviorProfile(
        parser_severity="strict",
        thought_leak_risk="low",
        action_inline_risk="low",
        loop_risk="low",
        tool_call_quality="excellent",
        react_stability="stable",
        sub_agent_stability="stable",
        empty_response_risk="rare",
        retry_on_empty=False,
        timeout_multiplier=1.0,
        compact_ctx_threshold=0.78,
    ),
    # ── Modèles Ollama small — les moins fiables ─────────────────────────────
    "qwen3-8b": ModelBehaviorProfile(
        parser_severity="forgiving",
        thought_leak_risk="high",
        action_inline_risk="high",
        loop_risk="high",
        tool_call_quality="moderate",
        react_stability="moderate",
        sub_agent_stability="unstable",
        empty_response_risk="occasional",
        retry_on_empty=True,
        timeout_multiplier=1.5,
        compact_ctx_threshold=0.65,
        file_section_threshold=30_000,
        sub_agent_iter_cap=20,
    ),
    "deepseek-r1-7b": ModelBehaviorProfile(
        parser_severity="forgiving",
        thought_leak_risk="high",
        action_inline_risk="high",
        loop_risk="high",
        tool_call_quality="poor",
        react_stability="unstable",
        sub_agent_stability="unstable",
        empty_response_risk="occasional",
        retry_on_empty=True,
        timeout_multiplier=1.5,
        compact_ctx_threshold=0.60,
        file_section_threshold=25_000,
        sub_agent_iter_cap=15,
    ),
}

# ─────────────────────────────────────────────────────────────────────────────
# Mappings partiel nom→provider (pour les noms qui ne matchent pas le dict)
# ─────────────────────────────────────────────────────────────────────────────

_PARTIAL_PROVIDER_MAP: list[tuple[str, str]] = [
    ("claude", "anthropic"),
    ("gpt", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
    ("gemini", "google"),
    ("kimi", "moonshot"),
    ("deepseek", "deepseek"),
    ("grok", "xai"),
    ("nvidia", "nvidia"),
    ("minimax", "minimax"),
    ("glm", "zai"),
    ("cogview", "zai"),
    ("lumena", "ollama"),
    ("qwen", "ollama"),
]


def get_model_profile(model_name: str) -> ModelBehaviorProfile:
    """Retourne le profil comportemental du modèle.

    Cherche dans l'ordre :
    1. Override exact par nom de modèle
    2. Default du provider déduit du nom
    3. Profil neutre par défaut
    """
    name = (model_name or "").strip().lower()

    # 1. Override exact
    if name in _MODEL_OVERRIDES:
        return _MODEL_OVERRIDES[name]

    # 2. Partial match sur overrides (ex: "deepseek-v4-pro-xl" → "deepseek-v4-pro")
    for key, profile in _MODEL_OVERRIDES.items():
        if key in name or name.startswith(key.split("-")[0]):
            if len(key) > 4:  # éviter faux positifs sur clés courtes
                return profile

    # 3. Provider par partial match sur le nom
    for fragment, provider in _PARTIAL_PROVIDER_MAP:
        if fragment in name:
            return _PROVIDER_DEFAULTS.get(provider, ModelBehaviorProfile())

    return ModelBehaviorProfile()


def describe_profile(profile: ModelBehaviorProfile) -> str:
    """Retourne une description lisible du profil pour les logs."""
    return (
        f"parser={profile.parser_severity} "
        f"thought_leak={profile.thought_leak_risk} "
        f"tool_call={profile.tool_call_quality} "
        f"react={profile.react_stability} "
        f"timeout_mult={profile.timeout_multiplier:.2f} "
        f"ctx_threshold={profile.compact_ctx_threshold:.0%}"
    )
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
