"""
🌟 LUMENA - Module Voice

Gestion de la voix: Text-to-Speech et Speech-to-Text.
"""

from .tts import LumenaTTS, get_tts
from .stt import LumenaSTT, get_stt

__all__ = ["LumenaTTS", "get_tts", "LumenaSTT", "get_stt"]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
