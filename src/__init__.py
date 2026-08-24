"""
🌟 LUMENA - Une IA qui vit avec toi

LUMENA est une IA 3D autonome qui fonctionne 24/7 sur PC Windows.
Elle observe, réfléchit, décide, agit et apprend - même sans interaction.

Version: canonique dans src.version
"""

from .version import __version__
__codename__ = "Lumena Aurora"
__author__ = "Losskarr-G.C"

__all__ = [
    "LumenaCore", 
    "LumenaPersonality", 
    "EmotionManager",
    "get_emotion_manager",
    "__version__", 
    "__codename__"
]


def __getattr__(name: str):
    """Imports lourds charges a la demande.

    Les tests de sous-modules (`src.agents.*`, `src.llm.*`) ne doivent pas
    initialiser tout Lumena simplement parce que le package racine est importe.
    """
    if name == "LumenaCore":
        from .core import LumenaCore
        return LumenaCore
    if name == "LumenaPersonality":
        from .personality import LumenaPersonality
        return LumenaPersonality
    if name in ("EmotionManager", "get_emotion_manager"):
        from .emotion import EmotionManager, get_emotion_manager
        return {"EmotionManager": EmotionManager, "get_emotion_manager": get_emotion_manager}[name]
    raise AttributeError(name)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
