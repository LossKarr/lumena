"""
🌟 LUMENA - Une IA qui vit avec toi

LUMENA est une IA 3D autonome qui fonctionne 24/7 sur PC Windows.
Elle observe, réfléchit, décide, agit et apprend - même sans interaction.

Version: 1.0.13 (Lumena Aurora)
"""

__version__ = "1.0.13"
__codename__ = "Lumena Aurora"
__author__ = "Losskarr-G.C"

from .core import LumenaCore
from .personality import LumenaPersonality
from .emotion import EmotionManager, get_emotion_manager

__all__ = [
    "LumenaCore", 
    "LumenaPersonality", 
    "EmotionManager",
    "get_emotion_manager",
    "__version__", 
    "__codename__"
]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
