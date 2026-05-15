"""
ProjectMemory — Mémoire persistante par projet via ChromaDB.

Persiste entre sessions : fichiers modifiés, décisions, erreurs résolues.
Rechargée automatiquement au début de chaque tâche CodeAgent sur un projet connu.

API réelle utilisée : ChromaMemoryStore(data_dir) de src/memory/chromadb_store.py
"""

from __future__ import annotations

from pathlib import Path
from loguru import logger


class ProjectMemory:
    """
    Mémoire persistante par projet — ChromaMemoryStore dédié.
    Lazy-init pour ne jamais bloquer si ChromaDB absent.
    """

    def __init__(self):
        self._store = None  # lazy init

    def _get_store(self):
        """Lazy init — ChromaMemoryStore(data_dir) est l'API réelle de Lumena."""
        if self._store is None:
            try:
                from src.memory.chromadb_store import ChromaMemoryStore
                from src.utils.paths import DATA_DIR
                self._store = ChromaMemoryStore(DATA_DIR / "project_memory")
            except Exception as e:
                logger.warning("[ProjectMemory] ChromaDB indispo: {}", e)
        return self._store

    def save_session(self, project_id: str, summary: dict) -> None:
        """
        Sauvegarde après chaque tâche CodeAgent réussie.

        summary = {
            "task": str,
            "files_modified": list,
            "decisions": list,
            "errors_resolved": list,
            "workspace": str,
        }
        """
        store = self._get_store()
        if store is None:
            return
        try:
            text = self._format_summary(summary)
            store.add(
                content=text,
                memory_type="episodic",
                importance=0.7,
                metadata={"project_id": project_id},
            )
        except Exception as e:
            logger.debug("[ProjectMemory] save_session failed: {}", e)

    def get_context(self, project_id: str, query: str, max_chars: int = 1500) -> str:
        """
        Récupère les sessions pertinentes pour la tâche en cours.
        search() retourne List[Memory] — on filtre par project_id dans .metadata.
        """
        store = self._get_store()
        if store is None:
            return ""
        try:
            memories = store.search(query, limit=10)
            relevant = [
                m for m in memories
                if m.metadata.get("project_id") == project_id
            ]
            if not relevant:
                return ""
            combined = "\n---\n".join(m.content for m in relevant[:5])
            return f"=== HISTORIQUE DU PROJET ===\n{combined[:max_chars]}"
        except Exception:
            return ""

    @staticmethod
    def _format_summary(s: dict) -> str:
        parts = [f"Tâche: {s.get('task', '')}"]
        if s.get("files_modified"):
            parts.append("Fichiers: " + ", ".join(s["files_modified"]))
        if s.get("decisions"):
            parts.append("Décisions: " + " | ".join(s["decisions"]))
        if s.get("errors_resolved"):
            parts.append("Bugs résolus: " + " | ".join(s["errors_resolved"]))
        return "\n".join(parts)


# Singleton global
_project_memory = ProjectMemory()


def get_project_memory() -> ProjectMemory:
    return _project_memory
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
