"""
Contrats partagés entre les services fragmentés de LumenaCore.

ServiceContext donne à chaque service un accès unifié aux ressources
partagées (llm, memory, data_dir, etc.) sans couplage direct à LumenaCore.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class ServiceContext:
    """Contexte injecté dans chaque service — accès aux ressources partagées."""

    # --- ressources obligatoires ---
    data_dir: Path

    # --- ressources optionnelles (peuvent être None selon le déploiement) ---
    llm: Any = None
    memory: Any = None
    tts: Any = None
    emotion_manager: Any = None
    tool_system: Any = None
    repo_map: Any = None
    code_index: Any = None
    rules_loader: Any = None
    hook_system: Any = None
    instinct_system: Any = None

    # --- état partagé (mutable) ---
    auto_speak: bool = False
    global_mute: bool = False  # mute persistant multi-canaux (survit aux resets de session)
    workspace_path: Optional[Path] = None
    skills: dict = field(default_factory=dict)
    friends: dict = field(default_factory=dict)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
