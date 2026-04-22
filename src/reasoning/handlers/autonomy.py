"""
autonomy.py - Handlers V2 pour la planification de tâches.

Expose schedule_task, list_tasks, cancel_task, task_history, remind
au ReAct loop via le registre V2, en wrappant les handlers existants
dans src/tools/task_scheduler.py.
"""

from __future__ import annotations

from typing import Any, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ─── Helpers ───────────────────────────────────────────────────────────────

def _get_task_handlers():
    """Import lazy pour éviter les dépendances circulaires."""
    from ...tools.task_scheduler import (
        handle_schedule_task,
        handle_list_tasks,
        handle_cancel_task,
        handle_task_history,
        handle_schedule_remind,
    )
    return handle_schedule_task, handle_list_tasks, handle_cancel_task, handle_task_history, handle_schedule_remind


def _get_task_modify_handlers():
    """Import lazy pour modify_task et delete_task."""
    from ...tools.task_scheduler import handle_modify_task, handle_delete_task
    return handle_modify_task, handle_delete_task


# ─── Handlers ──────────────────────────────────────────────────────────────

async def schedule_task_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    try:
        fn, _, _, _, _ = _get_task_handlers()
        result = await fn(**kwargs)
        return HandlerResult.ok(result, handler_name="schedule_task")
    except Exception as e:
        return HandlerResult.fail(f"Erreur schedule_task: {e}", handler_name="schedule_task")


async def list_tasks_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    try:
        _, fn, _, _, _ = _get_task_handlers()
        result = await fn(**kwargs)
        return HandlerResult.ok(result, handler_name="list_tasks")
    except Exception as e:
        return HandlerResult.fail(f"Erreur list_tasks: {e}", handler_name="list_tasks")


async def cancel_task_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    try:
        _, _, fn, _, _ = _get_task_handlers()
        result = await fn(**kwargs)
        return HandlerResult.ok(result, handler_name="cancel_task")
    except Exception as e:
        return HandlerResult.fail(f"Erreur cancel_task: {e}", handler_name="cancel_task")


async def task_history_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    try:
        _, _, _, fn, _ = _get_task_handlers()
        result = await fn(**kwargs)
        return HandlerResult.ok(result, handler_name="task_history")
    except Exception as e:
        return HandlerResult.fail(f"Erreur task_history: {e}", handler_name="task_history")


async def remind_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    try:
        _, _, _, _, fn = _get_task_handlers()
        result = await fn(**kwargs)
        return HandlerResult.ok(result, handler_name="remind")
    except Exception as e:
        return HandlerResult.fail(f"Erreur remind: {e}", handler_name="remind")


async def modify_task_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Modifie une tâche planifiée existante (action, nom, horaire)."""
    try:
        fn, _ = _get_task_modify_handlers()
        result = await fn(**kwargs)
        return HandlerResult.ok(result, handler_name="modify_task")
    except Exception as e:
        return HandlerResult.fail(f"Erreur modify_task: {e}", handler_name="modify_task")


async def delete_task_handler(ctx: HandlerContext, **kwargs) -> HandlerResult:
    """Supprime définitivement une tâche planifiée du scheduler."""
    try:
        _, fn = _get_task_modify_handlers()
        result = await fn(**kwargs)
        return HandlerResult.ok(result, handler_name="delete_task")
    except Exception as e:
        return HandlerResult.fail(f"Erreur delete_task: {e}", handler_name="delete_task")


# ─── Registry ──────────────────────────────────────────────────────────────

def get_autonomy_handler_defs() -> List[HandlerDef]:
    """Retourne les définitions de handlers autonomy pour le registre V2."""
    return [
        HandlerDef(
            name="schedule_task",
            description=(
                "⚡⚡ PREMIER OUTIL À APPELER quand l'utilisateur veut enregistrer une tâche récurrente "
                "ou un rappel. Mots-clés déclencheurs OBLIGATOIRES : 'enregistre une tâche', "
                "'rappelle-moi', 'tous les jours à X', 'chaque matin', 'toutes les heures', "
                "'planifie', 'programme', 'tu vas devoir faire X à Xh', 'tâche automatique'. "
                "Ne jamais utiliser plan_create, create_skill ou un fichier Python à la place — "
                "seul schedule_task crée une vraie tâche dans le scheduler. "
                "Le paramètre 'action' est un PROMPT textuel que Lumena exécutera à chaque déclenchement. "
                "Exemples : cron='0 10 * * *' pour tous les jours à 10h, interval=60 pour toutes les heures."
            ),
            parameters={
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "Prompt/instruction que Lumena exécutera au déclenchement (peut utiliser mail_send, web_search, memory_add, etc.)",
                    },
                    "name": {
                        "type": "string",
                        "description": "Nom lisible de la tâche",
                        "default": "",
                    },
                    "delay": {
                        "type": "string",
                        "description": "Délai avant exécution unique (ex: '2h', '30min', '1 jour')",
                        "default": "",
                    },
                    "run_at": {
                        "type": "string",
                        "description": "Date/heure précise (ex: '08:00', 'demain à 9h', '2026-03-10 18:00')",
                        "default": "",
                    },
                    "cron": {
                        "type": "string",
                        "description": "Expression CRON (ex: '0 8 * * *' = tous les jours à 8h, '0 12 * * 1-5' = lundi-vendredi à midi)",
                        "default": "",
                    },
                    "interval": {
                        "type": "integer",
                        "description": "Intervalle récurrent en minutes (ex: 30 pour toutes les 30min)",
                    },
                    "chat_id": {
                        "type": "string",
                        "description": "ID canal (Telegram chat_id ou WhatsApp phone) où renvoyer le résultat (auto-détecté si possible)",
                        "default": "",
                    },
                },
                "required": ["action"],
            },
            handler=schedule_task_handler,
            category="autonomy",
            source_module="handlers.autonomy",
        ),
        HandlerDef(
            name="list_tasks",
            description=(
                "📋 OUTIL OBLIGATOIRE quand l'utilisateur demande 'as-tu des tâches?', "
                "'qu'est-ce qui est planifié?', 'montre tes tâches', 'tu as des rappels?'. "
                "NE PAS lire des fichiers Markdown — ils ne reflètent pas l'état réel du scheduler. "
                "Retourne l'état live de toutes les tâches CRON système et conversationnelles."
            ),
            parameters={
                "properties": {
                    "filter": {
                        "type": "string",
                        "description": "Filtrer : all | conv (conversationnelles) | system",
                        "default": "all",
                    },
                },
                "required": [],
            },
            handler=list_tasks_handler,
            category="autonomy",
            source_module="handlers.autonomy",
        ),
        HandlerDef(
            name="list_scheduled_tasks",
            description="Alias de list_tasks — liste toutes les tâches planifiées actives.",
            parameters={
                "properties": {
                    "filter": {
                        "type": "string",
                        "description": "Filtrer : all | conv | system",
                        "default": "all",
                    },
                },
                "required": [],
            },
            handler=list_tasks_handler,
            category="autonomy",
            source_module="handlers.autonomy",
        ),
        HandlerDef(
            name="cancel_task",
            description="Annule une tâche planifiée par son ID (visible dans list_tasks).",
            parameters={
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "ID de la tâche à annuler",
                    },
                },
                "required": ["task_id"],
            },
            handler=cancel_task_handler,
            category="autonomy",
            source_module="handlers.autonomy",
        ),
        HandlerDef(
            name="task_history",
            description="Affiche l'historique d'exécution des tâches planifiées (dernière run, statut, durée, erreurs).",
            parameters={
                "properties": {
                    "task_id": {
                        "type": "string",
                        "description": "Filtrer par ID ou nom de tâche (optionnel)",
                        "default": "",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Nombre max d'entrées à afficher",
                        "default": 20,
                    },
                },
                "required": [],
            },
            handler=task_history_handler,
            category="autonomy",
            source_module="handlers.autonomy",
        ),
        HandlerDef(
            name="remind",
            description="Crée un rappel simple — Lumena contacte l'utilisateur sur Telegram ou WhatsApp au moment spécifié.",
            parameters={
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "Message du rappel",
                    },
                    "delay": {
                        "type": "string",
                        "description": "Dans combien de temps (ex: '30min', '2h', '1 jour')",
                        "default": "",
                    },
                    "run_at": {
                        "type": "string",
                        "description": "Heure précise (ex: '08:00', 'demain à 9h')",
                        "default": "",
                    },
                    "chat_id": {
                        "type": "string",
                        "description": "ID canal Telegram/WhatsApp (optionnel)",
                        "default": "",
                    },
                },
                "required": ["message"],
            },
            handler=remind_handler,
            category="autonomy",
            source_module="handlers.autonomy",
        ),
        HandlerDef(
            name="modify_task",
            description="Modifie une tâche planifiée existante (change l'action, le nom, l'horaire ou l'intervalle).",
            parameters={
                "properties": {
                    "task_id": {"type": "string", "description": "ID de la tâche à modifier"},
                    "action": {"type": "string", "description": "Nouvelle instruction/prompt à exécuter", "default": ""},
                    "name": {"type": "string", "description": "Nouveau nom de la tâche", "default": ""},
                    "cron": {"type": "string", "description": "Nouvelle expression CRON", "default": ""},
                    "interval": {"type": "integer", "description": "Nouvel intervalle en minutes"},
                    "run_at": {"type": "string", "description": "Nouvelle date/heure précise", "default": ""},
                },
                "required": ["task_id"],
            },
            handler=modify_task_handler,
            category="autonomy",
            source_module="handlers.autonomy",
        ),
        HandlerDef(
            name="delete_task",
            description="Supprime définitivement une tâche planifiée du scheduler (irréversible).",
            parameters={
                "properties": {
                    "task_id": {"type": "string", "description": "ID de la tâche à supprimer"},
                },
                "required": ["task_id"],
            },
            handler=delete_task_handler,
            category="autonomy",
            source_module="handlers.autonomy",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
