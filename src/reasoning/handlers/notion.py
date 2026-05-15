"""
notion.py - Handlers Notion fragmentés depuis react.py.

Handlers: notion_search, notion_read_page, notion_create_page,
          notion_update_page, notion_list_databases,
          notion_query_database, notion_add_to_database.

Chaque handler est une fonction async standalone:
    async def handler_name(ctx: HandlerContext, **kwargs) -> HandlerResult
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ─── Handlers ──────────────────────────────────────────────────────────────

async def notion_search_handler(
    ctx: HandlerContext, query: str
) -> HandlerResult:
    """Recherche dans le workspace Notion."""
    try:
        hub = ctx.get_notion_hub()
        data = await hub.search(query)
        if not data["results"]:
            return HandlerResult.ok(
                f"📓 Aucun résultat Notion pour « {query} »",
                handler_name="notion_search",
            )
        lines = [f"📓 **{data['count']} résultat(s) Notion pour « {query} »:**\n"]
        for r in data["results"]:
            icon = "📄" if r["type"] == "page" else "🗃️"
            lines.append(f"{icon} **{r['title']}**")
            lines.append(f"   ID: `{r['id']}`")
            lines.append(f"   🔗 {r['url']}\n")
        return HandlerResult.ok(
            "\n".join(lines), handler_name="notion_search"
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur Notion: {e}", handler_name="notion_search"
        )


async def notion_read_page_handler(
    ctx: HandlerContext, page_id: str
) -> HandlerResult:
    """Lit une page Notion et retourne son contenu."""
    try:
        hub = ctx.get_notion_hub()
        data = await hub.read_page(page_id)
        return HandlerResult.ok(
            f"📄 **{data['title']}**\n"
            f"ID: `{data['id']}` | 🔗 {data['url']}\n\n"
            f"---\n\n{data['content'] or '*(page vide)*'}",
            handler_name="notion_read_page",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur Notion: {e}", handler_name="notion_read_page"
        )


async def notion_create_page_handler(
    ctx: HandlerContext, parent_id: str = "", title: str = "", content: str = ""
) -> HandlerResult:
    """Crée une page dans Notion."""
    if not title:
        return HandlerResult.fail(
            "❌ title requis.",
            handler_name="notion_create_page",
        )
    if not parent_id:
        return HandlerResult.fail(
            "❌ parent_id requis. "
            "Notion n'autorise pas la création à la racine pour les intégrations internes. "
            "Utilise d'abord notion_search('') pour trouver une page ou database, "
            "puis fournis son ID.",
            handler_name="notion_create_page",
        )
    try:
        hub = ctx.get_notion_hub()
        data = await hub.create_page(parent_id, title, content)
        return HandlerResult.ok(
            f"✅ Page Notion créée: **{data['title']}**\n"
            f"   ID: `{data['id']}`\n"
            f"   🔗 {data['url']}",
            handler_name="notion_create_page",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur Notion: {e}", handler_name="notion_create_page"
        )


async def notion_update_page_handler(
    ctx: HandlerContext, page_id: str, content: str
) -> HandlerResult:
    """Met à jour le contenu d'une page Notion."""
    try:
        hub = ctx.get_notion_hub()
        data = await hub.update_page(page_id, content)
        return HandlerResult.ok(
            f"✅ Page `{data['id']}` mise à jour ({data['blocks_added']} blocs ajoutés)",
            handler_name="notion_update_page",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur Notion: {e}", handler_name="notion_update_page"
        )


async def notion_list_databases_handler(ctx: HandlerContext) -> HandlerResult:
    """Liste les databases Notion."""
    try:
        hub = ctx.get_notion_hub()
        data = await hub.list_databases()
        if not data["databases"]:
            return HandlerResult.ok(
                "📓 Aucune database trouvée dans le workspace Notion",
                handler_name="notion_list_databases",
            )
        lines = [f"🗃️ **{data['count']} database(s) Notion:**\n"]
        for db in data["databases"]:
            lines.append(f"📊 **{db['title']}**")
            lines.append(f"   ID: `{db['id']}`")
            lines.append(f"   🔗 {db['url']}\n")
        return HandlerResult.ok(
            "\n".join(lines), handler_name="notion_list_databases"
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur Notion: {e}", handler_name="notion_list_databases"
        )


async def notion_query_database_handler(
    ctx: HandlerContext, database_id: str, filter_json: str = ""
) -> HandlerResult:
    """Interroge une database Notion."""
    try:
        hub = ctx.get_notion_hub()
        data = await hub.query_database(database_id, filter_json)
        if data.get("error"):
            return HandlerResult.fail(
                f"❌ {data['error']}",
                handler_name="notion_query_database",
            )
        if not data["rows"]:
            return HandlerResult.ok(
                "📊 Aucune entrée dans cette database (ou filtre trop restrictif)",
                handler_name="notion_query_database",
            )
        lines = [f"📊 **{data['count']} entrée(s):**\n"]
        for row in data["rows"][:15]:
            props_str = " | ".join(
                f"{k}: {v}"
                for k, v in row["properties"].items()
                if v not in ("", [], None)
            )
            lines.append(f"• {props_str or '(sans propriétés)'}")
            lines.append(f"  ID: `{row['id']}`\n")
        if data["count"] > 15:
            lines.append(
                f"*(... et {data['count'] - 15} entrées supplémentaires)*"
            )
        return HandlerResult.ok(
            "\n".join(lines), handler_name="notion_query_database"
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur Notion: {e}", handler_name="notion_query_database"
        )


async def notion_add_to_database_handler(
    ctx: HandlerContext, database_id: str, properties_json: str
) -> HandlerResult:
    """Ajoute une entrée dans une database Notion."""
    try:
        hub = ctx.get_notion_hub()
        data = await hub.add_to_database(database_id, properties_json)
        if data.get("error"):
            return HandlerResult.fail(
                f"❌ {data['error']}",
                handler_name="notion_add_to_database",
            )
        return HandlerResult.ok(
            f"✅ Entrée ajoutée à la database\n"
            f"   ID: `{data['id']}`\n"
            f"   🔗 {data['url']}",
            handler_name="notion_add_to_database",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur Notion: {e}", handler_name="notion_add_to_database"
        )


# ─── HandlerDefs ───────────────────────────────────────────────────────────

def get_notion_handler_defs() -> List[HandlerDef]:
    """Retourne les définitions des 7 handlers Notion."""
    return [
        HandlerDef(
            name="notion_search",
            description=(
                "Recherche des pages et databases dans le workspace Notion. "
                "Nécessite NOTION_API_KEY dans .env."
            ),
            parameters={
                "properties": {
                    "query": {"type": "string", "description": "Texte à rechercher dans Notion"},
                },
                "required": ["query"],
            },
            handler=notion_search_handler,
            category="notion",
            source_module="handlers.notion",
        ),
        HandlerDef(
            name="notion_read_page",
            description="Lit le contenu complet d'une page Notion et le retourne en Markdown",
            parameters={
                "properties": {
                    "page_id": {"type": "string", "description": "ID de la page Notion (32 chars, avec ou sans tirets) ou URL Notion"},
                },
                "required": ["page_id"],
            },
            handler=notion_read_page_handler,
            category="notion",
            source_module="handlers.notion",
        ),
        HandlerDef(
            name="notion_create_page",
            description=(
                "Crée une nouvelle page dans Notion. "
                "Le parent peut être une page ou une database (ID ou URL). "
                "Si parent_id est omis, la page est créée à la racine du workspace Notion."
            ),
            parameters={
                "properties": {
                    "parent_id": {"type": "string", "description": "ID de la page ou database parente (optionnel — omis = racine workspace)"},
                    "title": {"type": "string", "description": "Titre de la nouvelle page"},
                    "content": {"type": "string", "description": "Contenu en Markdown (# titres, - listes, **gras**, etc.)"},
                },
                "required": ["title"],
            },
            handler=notion_create_page_handler,
            category="notion",
            source_module="handlers.notion",
        ),
        HandlerDef(
            name="notion_update_page",
            description="Remplace le contenu d'une page Notion existante par un nouveau contenu Markdown",
            parameters={
                "properties": {
                    "page_id": {"type": "string", "description": "ID de la page à modifier"},
                    "content": {"type": "string", "description": "Nouveau contenu en Markdown (remplace tout l'ancien contenu)"},
                },
                "required": ["page_id", "content"],
            },
            handler=notion_update_page_handler,
            category="notion",
            source_module="handlers.notion",
        ),
        HandlerDef(
            name="notion_list_databases",
            description="Liste toutes les databases disponibles dans le workspace Notion",
            parameters={"properties": {}, "required": []},
            handler=notion_list_databases_handler,
            category="notion",
            source_module="handlers.notion",
        ),
        HandlerDef(
            name="notion_query_database",
            description=(
                "Interroge une database Notion et retourne ses entrées. "
                "Filtre JSON optionnel au format Notion API."
            ),
            parameters={
                "properties": {
                    "database_id": {"type": "string", "description": "ID de la database Notion"},
                    "filter_json": {"type": "string", "description": "Filtre JSON Notion optionnel. Ex: '{\"property\": \"Status\", \"select\": {\"equals\": \"Done\"}}'"},
                },
                "required": ["database_id"],
            },
            handler=notion_query_database_handler,
            category="notion",
            source_module="handlers.notion",
        ),
        HandlerDef(
            name="notion_add_to_database",
            description=(
                "Ajoute une nouvelle entrée dans une database Notion. "
                'Ex properties_json: \'{"Nom": "Ma tâche", "Priorité": "Haute", "Terminé": false}\''
            ),
            parameters={
                "properties": {
                    "database_id": {"type": "string", "description": "ID de la database Notion"},
                    "properties_json": {"type": "string", "description": 'JSON des propriétés: {"NomColonne": "valeur", "Nombre": 42, "Fait": true}'},
                },
                "required": ["database_id", "properties_json"],
            },
            handler=notion_add_to_database_handler,
            category="notion",
            source_module="handlers.notion",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
