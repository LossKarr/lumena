"""
codebase.py — Handlers de recherche sémantique dans le code (@codebase).

Expose CodeIndex (ChromaDB + embeddings) comme outils ReAct :
- codebase_search : recherche sémantique dans tout le code
- codebase_index  : (ré)indexe le projet
- codebase_stats  : statistiques de l'index
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef

logger = logging.getLogger("lumena.handlers.codebase")

try:
    from ...context.code_index import CodeIndex, get_code_index, CodeSearchResult
    _CODE_INDEX_AVAILABLE = True
except Exception:
    _CODE_INDEX_AVAILABLE = False

try:
    from ...memory.chromadb_store import ChromaMemoryStore
    _MEMORY_STORE_AVAILABLE = True
except Exception:
    _MEMORY_STORE_AVAILABLE = False


def _get_index(ctx: HandlerContext) -> CodeIndex:
    """Récupère ou crée l'instance CodeIndex."""
    project_root = ctx.lumena_root
    return get_code_index(project_root)


# ── Handlers ─────────────────────────────────────────────────────

async def codebase_search_handler(ctx: HandlerContext, **kwargs: Any) -> HandlerResult:
    """Recherche sémantique dans le code source du projet."""
    if not _CODE_INDEX_AVAILABLE:
        return HandlerResult.fail(
            "CodeIndex non disponible (chromadb manquant?).",
            handler_name="codebase_search",
        )

    query: str = kwargs.get("query", "")
    if not query:
        return HandlerResult.fail("Paramètre 'query' requis.", handler_name="codebase_search")

    n_results: int = min(int(kwargs.get("n_results", 8)), 20)
    filter_type: Optional[str] = kwargs.get("filter_type")
    max_chars: int = int(kwargs.get("max_context", 8000))

    try:
        index = _get_index(ctx)

        # Auto-index si vide
        if index.collection and index.collection.count() == 0:
            logger.info("CodeIndex vide — indexation automatique du projet")
            index.index_project()

        results: List[CodeSearchResult] = index.search(
            query=query,
            n_results=n_results,
            filter_type=filter_type,
        )

        if not results:
            return HandlerResult.ok(
                f"Aucun résultat pour '{query}'.\n"
                "Conseil: relancez codebase_index si le code a changé.",
                handler_name="codebase_search",
            )

        # Formater les résultats avec budget de caractères
        parts = [f"**{len(results)} résultat(s) pour** `{query}`\n"]
        char_count = len(parts[0])

        for i, r in enumerate(results):
            entry = (
                f"### [{i + 1}] {r.chunk.file_path} — "
                f"{r.chunk.symbol_type} `{r.chunk.symbol_name or 'module'}` "
                f"(L{r.chunk.line_start}-{r.chunk.line_end}) — score {r.score:.2f}\n"
                f"```{r.chunk.language}\n{r.chunk.content}\n```\n"
            )
            if char_count + len(entry) > max_chars:
                parts.append(f"\n... {len(results) - i} résultat(s) supplémentaires tronqués (budget {max_chars} chars)")
                break
            parts.append(entry)
            char_count += len(entry)

        return HandlerResult.ok("\n".join(parts), handler_name="codebase_search")

    except Exception as e:
        return HandlerResult.fail(f"Erreur recherche: {e}", handler_name="codebase_search")


async def codebase_index_handler(ctx: HandlerContext, **kwargs: Any) -> HandlerResult:
    """(Ré)indexe le code source du projet dans ChromaDB."""
    if not _CODE_INDEX_AVAILABLE:
        return HandlerResult.fail("CodeIndex non disponible.", handler_name="codebase_index")

    force: bool = bool(kwargs.get("force", False))

    try:
        index = _get_index(ctx)
        count = index.index_project(force_reindex=force)
        return HandlerResult.ok(
            f"Indexation terminée: {count} chunks indexés"
            f"{' (réindexation forcée)' if force else ''}.",
            handler_name="codebase_index",
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur indexation: {e}", handler_name="codebase_index")


async def codebase_stats_handler(ctx: HandlerContext, **kwargs: Any) -> HandlerResult:
    """Statistiques de l'index vectoriel du code."""
    if not _CODE_INDEX_AVAILABLE:
        return HandlerResult.fail("CodeIndex non disponible.", handler_name="codebase_stats")

    try:
        index = _get_index(ctx)
        stats = index.get_stats()

        if not stats.get("available"):
            return HandlerResult.ok("Index non disponible (pas de ChromaDB).", handler_name="codebase_stats")

        lines = [
            "**Code Index Stats:**",
            f"  Chunks indexés: {stats['total_chunks']}",
            f"  Storage: {stats['persist_dir']}",
        ]

        # Ajouter les stats mémoire si disponible
        if _MEMORY_STORE_AVAILABLE:
            try:
                mem_store = ChromaMemoryStore()
                mem_count = mem_store.count() if hasattr(mem_store, 'count') else "?"
                lines.append(f"  Mémoires vectorielles: {mem_count}")
            except Exception as e:
                logger.debug(f"Memory store count: {e}")

        return HandlerResult.ok("\n".join(lines), handler_name="codebase_stats")
    except Exception as e:
        return HandlerResult.fail(f"Erreur: {e}", handler_name="codebase_stats")


# ── Registration ─────────────────────────────────────────────────

def get_codebase_handler_defs() -> List[HandlerDef]:
    """Retourne les définitions de handlers codebase pour le registre V2."""
    return [
        HandlerDef(
            name="codebase_search",
            description=(
                "Recherche semantique dans le code source du projet (@codebase). "
                "Utilise des embeddings vectoriels (ChromaDB) pour trouver du code pertinent "
                "par sens, pas juste par mot-cle. Ideal pour: 'comment fonctionne le scheduler', "
                "'ou est geree l'authentification', 'code qui envoie des mails', etc."
            ),
            parameters={
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Requete de recherche en langage naturel.",
                    },
                    "n_results": {
                        "type": "integer",
                        "description": "Nombre de resultats (defaut 8, max 20).",
                        "default": 8,
                    },
                    "filter_type": {
                        "type": "string",
                        "description": "Filtrer par type: function, class, method, module (optionnel).",
                    },
                    "max_context": {
                        "type": "integer",
                        "description": "Budget max de caracteres pour les resultats (defaut 8000).",
                        "default": 8000,
                    },
                },
                "required": ["query"],
            },
            handler=codebase_search_handler,
            category="codebase",
            source_module="handlers.codebase",
        ),
        HandlerDef(
            name="codebase_index",
            description=(
                "Indexe (ou re-indexe) le code source du projet dans l'index vectoriel. "
                "A lancer apres des changements importants dans le code, ou si codebase_search "
                "ne retourne pas de resultats pertinents."
            ),
            parameters={
                "properties": {
                    "force": {
                        "type": "boolean",
                        "description": "Forcer la reindexation meme si l'index existe (defaut false).",
                        "default": False,
                    },
                },
                "required": [],
            },
            handler=codebase_index_handler,
            category="codebase",
            source_module="handlers.codebase",
        ),
        HandlerDef(
            name="codebase_stats",
            description="Affiche les statistiques de l'index vectoriel du code (nombre de chunks, storage, etc.).",
            parameters={"properties": {}, "required": []},
            handler=codebase_stats_handler,
            category="codebase",
            source_module="handlers.codebase",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
