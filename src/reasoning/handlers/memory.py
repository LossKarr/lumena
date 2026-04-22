"""
memory.py - Handlers mémoire/apprentissage fragmentés depuis react.py.

Handlers: read_journal, memory_search, memory_stats, memory_get,
          learn_instinct, suggest_instincts, curiosity_status.

Chaque handler est une fonction async standalone:
    async def handler_name(ctx: HandlerContext, **kwargs) -> HandlerResult
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from ...utils.persistence import atomic_write_text
from ...utils.paths import JOURNAL_DIR, JOURNAL_JSON

_JOURNAL_LOCK = threading.Lock()

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ─── Handlers ──────────────────────────────────────────────────────────────

async def read_journal_handler(ctx: HandlerContext, date: str = "") -> HandlerResult:
    """Lit le journal quotidien de LUMENA (MD conversations + JSON actions autonomes)."""
    try:
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")

        sections: list[str] = []

        # --- 1. Fichier MD (conversations écrites par core.py) ---
        md_path = JOURNAL_DIR / f"{date}.md"
        if md_path.exists():
            md_text = md_path.read_text(encoding="utf-8").strip()
            if md_text:
                # Garder les 3000 derniers caractères pour éviter la surcharge
                if len(md_text) > 3000:
                    md_text = "…\n" + md_text[-3000:]
                sections.append(f"💬 **Conversations du {date}:**\n{md_text}")

        # --- 2. Fichier JSON (actions autonomes daemon / reflection) ---
        journal_path = JOURNAL_JSON
        if journal_path.exists():
            with open(journal_path, "r", encoding="utf-8") as f:
                journal = json.load(f)
            # Filtrer par date via le champ timestamp
            entries = [e for e in journal if e.get("timestamp", "").startswith(date)]
            if entries:
                lines = [f"🤖 **Actions autonomes du {date}** ({len(entries)} entrée(s)):"]
                for entry in entries[-10:]:
                    ts = entry.get("timestamp", "?")[:19]
                    content = entry.get("content", entry.get("summary", ""))[:200]
                    lines.append(f"  [{ts}] {content}")
                sections.append("\n".join(lines))

        if not sections:
            return HandlerResult.ok(
                f"📔 Aucune entrée pour le {date}. Mon journal est vide ce jour-là.",
                handler_name="read_journal",
            )

        result = f"📔 **Journal du {date}**\n\n" + "\n\n".join(sections)
        return HandlerResult.ok(result, handler_name="read_journal")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur journal: {e}", handler_name="read_journal")


async def memory_search_handler(ctx: HandlerContext, query: str) -> HandlerResult:
    """Recherche sémantique dans les souvenirs de LUMENA + fallback journal."""
    try:
        results = []
        if ctx.lumena is not None:
            results = ctx.lumena.memory.recall(query, limit=5)

        output = ""
        if results:
            output = f"🧠 **Souvenirs trouvés pour \"{query}\":**\n\n"
            for i, mem in enumerate(results, 1):
                if isinstance(mem, dict):
                    content = mem.get("content", "")
                    mem_type = mem.get("memory_type", mem.get("type", ""))
                    ts = (mem.get("timestamp", "") or "")[:10]
                    score = mem.get("score", mem.get("similarity", mem.get("vector_score", 0)))
                    meta = mem.get("metadata", {}) or {}
                    source = meta.get("source_name", meta.get("source", ""))
                    page = meta.get("page", "")
                    section = meta.get("section", "")
                    parts: list = []
                    if mem_type:
                        parts.append(mem_type)
                    if source:
                        parts.append(f"📁 {source}")
                    if page:
                        parts.append(f"p.{page}")
                    if section:
                        parts.append(f"§{str(section)[:30]}")
                    if ts:
                        parts.append(ts)
                    if score:
                        try:
                            parts.append(f"⭐{float(score):.2f}")
                        except (TypeError, ValueError):
                            pass
                    citation = f"[{', '.join(parts)}] " if parts else ""
                    output += f"{i}. {citation}{content}\n\n"
                else:
                    output += f"{i}. {str(mem)}\n\n"

        # Fallback: also search journals if ChromaDB returned few results
        if len(results) < 3:
            journal_dir = JOURNAL_DIR
            if journal_dir.exists():
                query_lower = query.lower()
                keywords = query_lower.split()
                journal_hits: list[tuple[str, str, int]] = []
                for md_file in sorted(journal_dir.glob("*.md")):
                    text = md_file.read_text(encoding="utf-8", errors="replace")
                    text_lower = text.lower()
                    match_count = sum(text_lower.count(kw) for kw in keywords)
                    if match_count == 0:
                        continue
                    best_pos = -1
                    for kw in keywords:
                        pos = text_lower.find(kw)
                        if pos != -1 and (best_pos == -1 or pos < best_pos):
                            best_pos = pos
                    start = max(0, best_pos - 80)
                    end = min(len(text), best_pos + 250)
                    snippet = text[start:end].strip().replace("\n", " ")
                    if start > 0:
                        snippet = "…" + snippet
                    if end < len(text):
                        snippet = snippet + "…"
                    journal_hits.append((md_file.stem, snippet, match_count))
                journal_hits.sort(key=lambda x: x[2], reverse=True)
                journal_hits = journal_hits[:3]
                if journal_hits:
                    output += "\n📔 **Aussi trouvé dans les journaux:**\n\n"
                    for date, snippet, count in journal_hits:
                        output += f"• **{date}**: {snippet}\n\n"

        if not output:
            return HandlerResult.ok(
                f"🔍 Aucun souvenir trouvé pour: {query}",
                handler_name="memory_search",
            )
        return HandlerResult.ok(output.rstrip(), handler_name="memory_search")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur mémoire: {e}", handler_name="memory_search")


async def memory_stats_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Retourne les statistiques réelles de mémoire."""
    try:
        if ctx.lumena is None:
            return HandlerResult.fail(
                "❌ Module mémoire non disponible (pas de lumena)",
                handler_name="memory_stats",
            )
        stats = ctx.lumena.memory.get_stats()
        output = "📊 **Statistiques mémoire LUMENA:**\n\n"
        for key, value in stats.items():
            output += f"  • {key}: {value}\n"
        return HandlerResult.ok(output, handler_name="memory_stats")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur stats mémoire: {e}", handler_name="memory_stats")


async def memory_get_handler(ctx: HandlerContext, query: str) -> HandlerResult:
    """Récupère des souvenirs spécifiques par leur contenu."""
    try:
        if ctx.lumena is None:
            return HandlerResult.fail(
                "❌ Module mémoire non disponible (pas de lumena)",
                handler_name="memory_get",
            )
        results = ctx.lumena.memory.recall(query, limit=10)
        if not results:
            return HandlerResult.ok(
                f"🔍 Aucun souvenir trouvé pour: {query}",
                handler_name="memory_get",
            )

        output = f"🧠 **{len(results)} souvenir(s) récupéré(s) pour \"{query}\":**\n\n"
        for i, mem in enumerate(results, 1):
            if isinstance(mem, dict):
                content = mem.get("content", "")
                date_str = mem.get("date", "")
                tags = mem.get("tags", [])
                output += f"{i}. [{date_str}] {content}"
                if tags:
                    output += f" (tags: {', '.join(tags)})"
                output += "\n"
            else:
                output += f"{i}. {mem}\n"

        return HandlerResult.ok(output, handler_name="memory_get")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur mémoire: {e}", handler_name="memory_get")


async def learn_instinct_handler(
    ctx: HandlerContext,
    pattern: str,
    response: str,
    was_successful: bool,
    category: str = "general",
) -> HandlerResult:
    """Enregistre un apprentissage dans les instincts."""
    try:
        from ...learning.instincts import get_instinct_system

        instincts = get_instinct_system()
        instinct = instincts.learn(pattern, response, was_successful, category)

        status = "✅ Succès" if was_successful else "❌ Échec"
        output = f"""🧠 Apprentissage enregistré!

**Contexte**: {pattern}
**Action**: {response}
**Résultat**: {status}
**Catégorie**: {category}

L'instinct a une confiance de {instinct.confidence:.0%} après {instinct.times_used} utilisations."""
        return HandlerResult.ok(output, handler_name="learn_from_action")
    except ImportError:
        return HandlerResult.fail(
            "❌ Module instincts non disponible", handler_name="learn_from_action"
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur apprentissage: {e}", handler_name="learn_from_action"
        )


async def suggest_instincts_handler(ctx: HandlerContext, context: str) -> HandlerResult:
    """Suggère des actions basées sur les instincts appris."""
    try:
        from ...learning.instincts import get_instinct_system

        instincts = get_instinct_system()
        suggestions = instincts.suggest(context)

        if not suggestions:
            return HandlerResult.ok(
                "🤝 Aucun instinct pertinent trouvé pour ce contexte. Je dois encore apprendre!",
                handler_name="suggest_instincts",
            )

        result = f"💡 **Suggestions basées sur mes apprentissages** (contexte: {context}):\n\n"
        for i, inst in enumerate(suggestions[:5], 1):
            result += f"{i}. **{inst.response}** (confiance: {inst.confidence:.0%}, catégorie: {inst.category})\n"

        return HandlerResult.ok(result, handler_name="suggest_instincts")
    except ImportError:
        return HandlerResult.fail(
            "❌ Module instincts non disponible", handler_name="suggest_instincts"
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur suggestions: {e}", handler_name="suggest_instincts"
        )


async def curiosity_status_handler(ctx: HandlerContext) -> HandlerResult:
    """Retourne le statut de curiosité de LUMENA."""
    try:
        from ...autonomy.curiosity import get_curiosity_module

        curiosity = get_curiosity_module()
        status = curiosity.get_status()
        thought = curiosity.get_thought()

        boredom_bar = "█" * int(status["boredom"] / 10) + "░" * (10 - int(status["boredom"] / 10))
        curiosity_bar = "█" * int(status["curiosity"] / 10) + "░" * (10 - int(status["curiosity"] / 10))
        energy_bar = "█" * int(status["energy"] / 10) + "░" * (10 - int(status["energy"] / 10))

        output = f"""🧠 **Mon État Actuel**

**Ennui**: [{boredom_bar}] {status['boredom']:.0f}%
**Curiosité**: [{curiosity_bar}] {status['curiosity']:.0f}%
**Énergie**: [{energy_bar}] {status['energy']:.0f}%

**Centres d'intérêt**: {len(status.get('interests', []))}
**Dernière mise à jour**: {status.get('last_update', 'inconnue')}

💭 *{thought}*"""
        return HandlerResult.ok(output, handler_name="get_curiosity_status")
    except ImportError:
        return HandlerResult.fail(
            "❌ Module curiosity non disponible", handler_name="get_curiosity_status"
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur curiosité: {e}", handler_name="get_curiosity_status"
        )

async def memory_add_handler(
    ctx: HandlerContext,
    content: str = "",
    category: str = "fact",
    importance: float = 0.7,
) -> HandlerResult:
    """Ajoute un souvenir dans la mémoire de LUMENA."""
    if not content or not content.strip():
        return HandlerResult.fail(
            "❌ memory_add: contenu vide.", handler_name="memory_add"
        )
    try:
        _IMPORTANCE_ALIASES = {"low": 0.3, "medium": 0.5, "high": 0.8, "critical": 1.0}
        if isinstance(importance, str) and importance.strip().lower() in _IMPORTANCE_ALIASES:
            importance = _IMPORTANCE_ALIASES[importance.strip().lower()]
        importance = max(0.0, min(1.0, float(importance)))
        if importance < 0.3:
            return HandlerResult.ok(
                f"⏭️ Mémoire ignorée (importance {importance:.1f} < 0.3)",
                handler_name="memory_add",
            )
        memory = getattr(ctx.lumena, "memory", None)
        if memory is None:
            return HandlerResult.fail(
                "❌ memory_add: provider mémoire indisponible.",
                handler_name="memory_add",
            )
        memory.remember(content, memory_type=category, importance=importance)
        return HandlerResult.ok(
            f"✅ Mémorisé ({category}, importance={importance:.1f}): {content[:100]}...",
            handler_name="memory_add",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur mémorisation: {e}", handler_name="memory_add"
        )

async def list_journal_dates_handler(ctx: HandlerContext) -> HandlerResult:
    """Liste toutes les dates de journal disponibles."""
    try:
        journal_dir = JOURNAL_DIR
        if not journal_dir.exists():
            return HandlerResult.ok(
                "📔 Aucun journal trouvé.", handler_name="list_journal_dates"
            )
        files = sorted(journal_dir.glob("*.md"))
        if not files:
            return HandlerResult.ok(
                "📔 Aucun journal trouvé.", handler_name="list_journal_dates"
            )
        dates = [f.stem for f in files]
        output = f"📔 **{len(dates)} journaux disponibles** (du {dates[0]} au {dates[-1]}):\n\n"
        output += ", ".join(dates)
        return HandlerResult.ok(output, handler_name="list_journal_dates")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="list_journal_dates")


async def search_journal_handler(ctx: HandlerContext, query: str, limit: int = 5) -> HandlerResult:
    """Recherche plein-texte dans tous les journaux de LUMENA."""
    try:
        journal_dir = JOURNAL_DIR
        if not journal_dir.exists():
            return HandlerResult.ok(
                "📔 Aucun journal trouvé.", handler_name="search_journal"
            )
        query_lower = query.lower()
        keywords = query_lower.split()
        results: list[tuple[str, str, int]] = []  # (date, snippet, match_count)

        for md_file in sorted(journal_dir.glob("*.md")):
            text = md_file.read_text(encoding="utf-8", errors="replace")
            text_lower = text.lower()
            match_count = sum(text_lower.count(kw) for kw in keywords)
            if match_count == 0:
                continue
            # Extract best matching section
            best_pos = -1
            for kw in keywords:
                pos = text_lower.find(kw)
                if pos != -1 and (best_pos == -1 or pos < best_pos):
                    best_pos = pos
            start = max(0, best_pos - 100)
            end = min(len(text), best_pos + 300)
            snippet = text[start:end].strip().replace("\n", " ")
            if start > 0:
                snippet = "…" + snippet
            if end < len(text):
                snippet = snippet + "…"
            results.append((md_file.stem, snippet, match_count))

        results.sort(key=lambda x: x[2], reverse=True)
        results = results[:limit]

        if not results:
            return HandlerResult.ok(
                f"🔍 Aucun résultat dans les journaux pour: {query}",
                handler_name="search_journal",
            )

        output = f"📔 **{len(results)} journal(aux) contenant \"{query}\":**\n\n"
        for date, snippet, count in results:
            output += f"**{date}** ({count} occurrences):\n> {snippet}\n\n"
        return HandlerResult.ok(output.rstrip(), handler_name="search_journal")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="search_journal")


async def write_journal_handler(ctx: HandlerContext, content: str, date: str = "") -> HandlerResult:
    """Ajoute une note dans le journal quotidien de LUMENA (data/memory/journal/YYYY-MM-DD.md)."""
    try:
        if not date:
            date = datetime.now().strftime("%Y-%m-%d")
        time_str = datetime.now().strftime("%H:%M:%S")

        journal_dir = JOURNAL_DIR
        journal_dir.mkdir(parents=True, exist_ok=True)
        journal_file = journal_dir / f"{date}.md"

        entry = f"\n## {time_str} \U0001f4dd Note autonome\n\n{content.strip()}\n\n---\n"

        with _JOURNAL_LOCK:
            if not journal_file.exists():
                header = f"# \U0001f4d4 Journal Lumena - {date}\n\nConversations et apprentissages de la journ\u00e9e.\n\n---\n"
                atomic_write_text(journal_file, header + entry)
            else:
                with open(journal_file, "a", encoding="utf-8") as f:
                    f.write(entry)

        return HandlerResult.ok(
            f"\u2705 Note aj out\u00e9e au journal du {date} ({len(content)} chars) — {journal_file}",
            handler_name="write_journal",
        )
    except Exception as e:
        return HandlerResult.fail(f"\u274c Erreur \u00e9criture journal: {e}", handler_name="write_journal")


# ─── Registration ──────────────────────────────────────────────────────────

def get_memory_handler_defs() -> List[HandlerDef]:
    """Retourne toutes les définitions de handlers memory pour le registre V2."""
    return [
        HandlerDef(
            name="read_journal",
            description="Lit le journal quotidien de LUMENA (conversations et apprentissages).",
            parameters={
                "properties": {
                    "date": {
                        "type": "string",
                        "description": "Date au format YYYY-MM-DD (defaut: aujourd'hui)",
                    },
                },
                "required": [],
            },
            handler=read_journal_handler,
            category="memory",
            source_module="handlers.memory",
        ),
        HandlerDef(
            name="memory_search",
            description="Recherche semantique dans les souvenirs de LUMENA.",
            parameters={
                "properties": {
                    "query": {"type": "string", "description": "Requete de recherche"},
                },
                "required": ["query"],
            },
            handler=memory_search_handler,
            category="memory",
            source_module="handlers.memory",
        ),
        HandlerDef(
            name="memory_stats",
            description="Retourne les statistiques reelles de memoire (count exact, facts).",
            parameters={"properties": {}, "required": []},
            handler=memory_stats_handler,
            category="memory",
            source_module="handlers.memory",
        ),
        HandlerDef(
            name="memory_get",
            description="Recupere des souvenirs specifiques par leur contenu.",
            parameters={
                "properties": {
                    "query": {"type": "string", "description": "Contenu a rechercher"},
                },
                "required": ["query"],
            },
            handler=memory_get_handler,
            category="memory",
            source_module="handlers.memory",
        ),
        HandlerDef(
            name="learn_from_action",
            description="Enregistre un apprentissage base sur le succes ou echec d'une action.",
            parameters={
                "properties": {
                    "pattern": {"type": "string", "description": "Le contexte/situation"},
                    "response": {"type": "string", "description": "L'action effectuee"},
                    "was_successful": {"type": "boolean", "description": "Si l'action a reussi"},
                    "category": {
                        "type": "string",
                        "description": "Categorie (code, file, search, general)",
                    },
                },
                "required": ["pattern", "response", "was_successful"],
            },
            handler=learn_instinct_handler,
            category="memory",
            source_module="handlers.memory",
        ),
        HandlerDef(
            name="suggest_instincts",
            description="Suggere des actions basees sur les apprentissages passes.",
            parameters={
                "properties": {
                    "context": {
                        "type": "string",
                        "description": "Description du contexte actuel",
                    },
                },
                "required": ["context"],
            },
            handler=suggest_instincts_handler,
            category="memory",
            source_module="handlers.memory",
        ),
        HandlerDef(
            name="get_curiosity_status",
            description="Retourne le niveau actuel d'ennui, curiosite et energie.",
            parameters={"properties": {}, "required": []},
            handler=curiosity_status_handler,
            category="memory",
            source_module="handlers.memory",
        ),
        HandlerDef(
            name="memory_add",
            description="Ajoute un souvenir dans la mémoire de LUMENA (fait, apprentissage, événement).",
            parameters={
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Contenu à mémoriser",
                    },
                    "category": {
                        "type": "string",
                        "description": "Type: fact, episodic, semantic, skill (défaut: fact)",
                        "default": "fact",
                    },
                    "importance": {
                        "type": "number",
                        "description": "Importance 0.0-1.0 (ignoré si < 0.3, défaut: 0.7)",
                        "default": 0.7,
                    },
                },
                "required": ["content"],
            },
            handler=memory_add_handler,
            category="memory",
            source_module="handlers.memory",
        ),
        HandlerDef(
            name="write_journal",
            description="Ajoute une note dans le journal quotidien de LUMENA (data/memory/journal/YYYY-MM-DD.md). A utiliser pour les notes autonomes, apprentissages, reflexions.",
            parameters={
                "properties": {
                    "content": {
                        "type": "string",
                        "description": "Contenu de la note a ecrire dans le journal",
                    },
                    "date": {
                        "type": "string",
                        "description": "Date au format YYYY-MM-DD (defaut: aujourd'hui)",
                    },
                },
                "required": ["content"],
            },
            handler=write_journal_handler,
            category="memory",
            source_module="handlers.memory",
        ),
        HandlerDef(
            name="list_journal_dates",
            description="Liste toutes les dates de journaux disponibles (depuis la creation de LUMENA). Utiliser avant read_journal pour savoir quelles dates existent.",
            parameters={"properties": {}, "required": []},
            handler=list_journal_dates_handler,
            category="memory",
            source_module="handlers.memory",
        ),
        HandlerDef(
            name="search_journal",
            description="Recherche plein-texte dans TOUS les journaux de LUMENA. Trouve des conversations, messages, apprentissages par mots-cles a travers tout l'historique.",
            parameters={
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Mots-cles a rechercher dans les journaux",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre max de resultats (defaut: 5)",
                        "default": 5,
                    },
                },
                "required": ["query"],
            },
            handler=search_journal_handler,
            category="memory",
            source_module="handlers.memory",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
