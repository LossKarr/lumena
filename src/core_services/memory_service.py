"""
MemoryService — Gestion de la mémoire persistante.

Migré depuis LumenaCore (9 méthodes, dépendances self.memory + self.llm).
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .base_service import BaseService


class MemoryService(BaseService):
    """Mémoire persistante (remember, recall, facts, stats, MEMORY.md)."""

    def __init__(self, ctx):
        super().__init__(ctx)
        self._permanent_memory: str = ""

    # --- Compaction helper (utilisé par ContextCompactor) ---

    async def _llm_summarize(self, messages: List[Dict[str, Any]]) -> str:
        """Résume un bloc de vieux messages pour la compaction de contexte."""
        text = "\n".join(
            f"{m.get('role', '?').upper()}: {str(m.get('content', ''))[:600]}"
            for m in messages
            if m.get("role") not in ("system",)
        )
        prompt = [
            {
                "role": "system",
                "content": (
                    "Tu es un assistant qui résume des conversations. "
                    "Sois concis et factuel. Maximum 10 lignes."
                ),
            },
            {
                "role": "user",
                "content": (
                    "Résume ce bloc de conversation en 5-10 lignes "
                    "(points clés, décisions, contexte important) :\n\n" + text
                ),
            },
        ]
        try:
            return await self.llm.chat(prompt)
        except Exception as e:
            logger.warning(f"⚠️ LLM summarizer échoué: {e}")
            return f"Conversation précédente de {len(messages)} messages."

    def _load_memory_file(self):
        """Charge le fichier MEMORY.md pour enrichir le contexte permanent."""
        memory_file = self.data_dir / "MEMORY.md"
        if memory_file.exists():
            try:
                content = memory_file.read_text(encoding="utf-8")
                self._permanent_memory = content
                logger.info(f"📖 MEMORY.md chargé ({len(content)} caractères)")
            except Exception as e:
                logger.warning(f"Erreur lecture MEMORY.md: {e}")
                self._permanent_memory = ""
        else:
            self._permanent_memory = ""
            logger.debug("📖 Pas de MEMORY.md trouvé")

    def get_permanent_memory_context(self) -> str:
        """Retourne le contexte de mémoire permanente pour le prompt."""
        if self._permanent_memory:
            return f"\n\n📖 MÉMOIRE PERMANENTE:\n{self._permanent_memory[:2000]}"
        return ""

    def learn_from_interaction(self, pattern: str, response: str, success: bool):
        """Apprend de l'interaction pour les futurs instincts."""
        instinct_system = self.ctx.instinct_system
        if instinct_system is None:
            return
        try:
            instinct_system.learn(pattern, response, success)
        except Exception as e:
            logger.debug(f"Erreur apprentissage: {e}")

    async def remember(self, content: str, memory_type: str = "episodic", importance: float = 0.5) -> bool:
        """Enregistre quelque chose en mémoire."""
        if not self.memory:
            logger.warning("Mémoire non disponible")
            return False
        try:
            self.memory.remember(content, memory_type=memory_type, importance=importance)
            logger.info(f"💾 Mémorisé: {content[:50]}... (importance: {importance})")
            return True
        except Exception as e:
            logger.error(f"❌ Erreur mémorisation: {e}")
            return False

    async def recall(self, query: str, limit: int = 5) -> List[Any]:
        """Rappelle des souvenirs liés à une requête."""
        if not self.memory:
            return []
        try:
            memories = self.memory.recall(query, limit=limit)
            logger.info(f"🧠 {len(memories)} souvenirs rappelés pour: {query[:30]}")
            return memories
        except Exception as e:
            logger.error(f"❌ Erreur rappel mémoire: {e}")
            return []

    def get_memory_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques de la mémoire."""
        if not self.memory:
            return {"available": False}
        try:
            stats = self.memory.get_stats()
            count = stats.get("count")
            if count is None:
                count = stats.get("total_memories", 0)
            return {
                "available": True,
                "count": count,
                "types": stats.get("types", {}),
                "last_access": stats.get("last_access")
            }
        except Exception as e:
            return {"available": True, "error": str(e)}

    def learn_fact(self, key: str, value: Any) -> bool:
        """Apprend un fait permanent."""
        if not self.memory:
            return False
        try:
            self.memory.learn_fact(key, value)
            logger.info(f"📚 Fait appris: {key} = {value}")
            return True
        except Exception as e:
            logger.error(f"❌ Erreur apprentissage fait: {e}")
            return False

    def get_fact(self, key: str) -> Optional[Any]:
        """Récupère un fait permanent."""
        if not self.memory:
            return None
        try:
            return self.memory.get_fact(key)
        except Exception as e:
            logger.debug(f"Erreur récupération fait '{key}': {e}")
            return None
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
