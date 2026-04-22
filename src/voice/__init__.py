"""
🌟 LUMENA - Module Voice

Gestion de la voix: Text-to-Speech et Speech-to-Text.
"""

from .tts import LumenaTTS, get_tts
from .stt import LumenaSTT, get_stt

__all__ = ["LumenaTTS", "get_tts", "LumenaSTT", "get_stt"]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
