"""
VoiceService — Synthèse vocale (TTS).

Migré depuis LumenaCore (4 méthodes, zéro couplage mémoire/agent).
"""

import re
from typing import Optional

from loguru import logger

from .base_service import BaseService


class VoiceService(BaseService):
    """Synthèse vocale (TTS) — speak, chat_and_speak, auto_speak."""

    @property
    def tts(self):
        return self.ctx.tts

    @property
    def auto_speak(self) -> bool:
        return self.ctx.auto_speak

    @auto_speak.setter
    def auto_speak(self, value: bool):
        self.ctx.auto_speak = value

    async def _speak_response(self, response_text: str) -> None:
        """Fait parler LUMENA avec le texte donné."""
        if not self.tts:
            return
        logger.info(f"🔊 Déclenchement TTS pour: {response_text[:50]}...")
        try:
            clean_text = re.sub(r'[🌟🔊✅⚠️🚀💡🎭🔥⭐]', '', response_text)
            clean_text = re.sub(r'\*\*(.+?)\*\*', r'\1', clean_text)
            clean_text = re.sub(r'\*(.+?)\*', r'\1', clean_text)
            clean_text = re.sub(r'`(.+?)`', r'\1', clean_text)
            clean_text = re.sub(r'\s+', ' ', clean_text).strip()
            if len(clean_text) > 500:
                clean_text = clean_text[:497] + "..."
            if len(clean_text.strip()) < 3:
                return
            await self.tts.speak_async(clean_text)
        except Exception as e:
            logger.error(f"❌ Erreur synthèse vocale: {e}")

    async def speak(self, text: str, wait: bool = True) -> None:
        """Fait parler LUMENA à voix haute."""
        if not self.tts:
            logger.warning("TTS non disponible")
            return
        await self._speak_response(text)

    async def chat_and_speak(self, user_message: str, chat_fn=None) -> str:
        """Chat avec LUMENA et fait parler la réponse.

        *chat_fn* est passé par le proxy core.py pour éviter un import circulaire.
        """
        if chat_fn is None:
            raise RuntimeError("chat_and_speak nécessite un chat_fn callback")
        response = await chat_fn(user_message)
        await self.speak(response, wait=False)
        return response

    def set_auto_speak(self, enabled: bool):
        """Active ou désactive la parole automatique."""
        if enabled and self.ctx.global_mute:
            logger.debug("🔇 set_auto_speak(True) ignoré — global_mute actif")
            return
        self.auto_speak = enabled
        logger.info(f"🔊 Parole automatique {'activée' if enabled else 'désactivée'}")

    def set_global_mute(self, enabled: bool):
        """Active/désactive le mute global persistant (tous canaux, survit aux resets)."""
        self.ctx.global_mute = enabled
        if enabled:
            self.auto_speak = False
            logger.info("🔇 Mute global activé — parole bloquée tous canaux")
        else:
            self.auto_speak = True
            logger.info("🔊 Mute global levé — parole réactivée")
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
