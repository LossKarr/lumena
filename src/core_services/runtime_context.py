"""
RuntimeContext — Snapshot immutable de l'état runtime pour chaque requête.

Construit une fois par requête via build_runtime_snapshot(), puis propagé
à ReActLoop, SubAgent, et tout composant qui a besoin de connaître
les contraintes runtime (modèle actif, providers sains, budget temps, etc.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class RuntimeContext:
    """Snapshot immutable du contexte runtime pour une requête."""

    # --- Modèle actif ---
    active_model: str                              # nom du modèle (clé AVAILABLE_MODELS)
    active_provider: str                           # "ollama", "openai", etc.
    max_context_window: int = 32000                # fenêtre de contexte en tokens
    max_output_tokens: int = 4096                  # tokens max en sortie

    # --- Santé providers ---
    providers_health: Dict[str, bool] = field(default_factory=dict)
    healthy_providers: List[str] = field(default_factory=list)

    # --- Budget ---
    budget_seconds: float = 900.0                  # timeout global (défaut 15 min)

    # --- Contexte canal ---
    source_channel: str = "web"                    # "web", "telegram", "discord", "whatsapp"
    mode: str = "agent"                            # "chat" ou "agent"

    # --- Intent classifié ---
    intent: str = "react"                          # "react", "project", "tool_direct", "chat"

    # --- Fallback ---
    fallback_order: List[str] = field(default_factory=list)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
