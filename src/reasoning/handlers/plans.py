"""
plans.py - Handlers de gestion de plans fragmentés depuis tool_system.py.

Handlers (4): plan_create, plan_list, plan_update, plan_done.
"""

from __future__ import annotations

from typing import List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ─── Helpers ───────────────────────────────────────────────────────────────

def _get_plan_handlers():
    """Import lazy des fonctions plan_manager."""
    from ...tools.plan_manager import (
        handle_plan_create,
        handle_plan_update,
        handle_plan_list,
        handle_plan_done,
    )
    return {
        "create": handle_plan_create,
        "update": handle_plan_update,
        "list": handle_plan_list,
        "done": handle_plan_done,
    }


# ─── Handlers ──────────────────────────────────────────────────────────────

async def plan_create_handler(
    ctx: HandlerContext,
    title: str,
    steps: Optional[List[str]] = None,
    description: str = "",
    priority: str = "normal",
) -> HandlerResult:
    """Crée un nouveau plan structuré avec titre, description et étapes."""
    try:
        fns = _get_plan_handlers()
        result = await fns["create"](
            title=title,
            steps=steps or [],
            description=description,
            priority=priority,
        )
        return HandlerResult.ok(str(result), handler_name="plan_create")
    except ImportError:
        return HandlerResult.fail("❌ plan_manager non disponible.", handler_name="plan_create")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur plan_create: {e}", handler_name="plan_create")


async def plan_list_handler(
    ctx: HandlerContext,
    status: str = "all",
) -> HandlerResult:
    """Liste tous les plans existants, avec filtre optionnel par statut."""
    try:
        fns = _get_plan_handlers()
        result = await fns["list"](status=status)
        return HandlerResult.ok(str(result), handler_name="plan_list")
    except ImportError:
        return HandlerResult.fail("❌ plan_manager non disponible.", handler_name="plan_list")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur plan_list: {e}", handler_name="plan_list")


async def plan_update_handler(
    ctx: HandlerContext,
    plan_id: str,
    step_index: Optional[int] = None,
    step_status: str = "done",
    note: str = "",
) -> HandlerResult:
    """Met à jour l'état d'une étape d'un plan (done, in_progress, failed)."""
    try:
        fns = _get_plan_handlers()
        result = await fns["update"](
            plan_id=plan_id,
            step_index=step_index,
            step_status=step_status,
            note=note,
        )
        return HandlerResult.ok(str(result), handler_name="plan_update")
    except ImportError:
        return HandlerResult.fail("❌ plan_manager non disponible.", handler_name="plan_update")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur plan_update: {e}", handler_name="plan_update")


async def plan_done_handler(
    ctx: HandlerContext,
    plan_id: str,
    summary: str = "",
) -> HandlerResult:
    """Marque un plan entier comme terminé, avec résumé optionnel."""
    try:
        fns = _get_plan_handlers()
        result = await fns["done"](plan_id=plan_id, summary=summary)
        return HandlerResult.ok(str(result), handler_name="plan_done")
    except ImportError:
        return HandlerResult.fail("❌ plan_manager non disponible.", handler_name="plan_done")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur plan_done: {e}", handler_name="plan_done")


# ─── Registry ──────────────────────────────────────────────────────────────

def get_plans_handler_defs() -> List[HandlerDef]:
    """Retourne les 4 définitions de handlers plans pour le registre V2."""
    return [
        HandlerDef(
            name="plan_create",
            description=(
                "Crée un plan d'action structuré (étapes à faire MAINTENANT, en ordre). "
                "⚠️ CE N'EST PAS un outil de planification temporelle. "
                "Pour toute tâche récurrente, rappel ou 'faire tous les jours à Xh' → "
                "utilise SCHEDULE_TASK, pas plan_create."
            ),
            parameters={
                "properties": {
                    "title": {"type": "string", "description": "Titre du plan"},
                    "steps": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Liste des étapes à accomplir dans l'ordre",
                        "default": [],
                    },
                    "description": {"type": "string", "description": "Description détaillée du plan", "default": ""},
                    "priority": {"type": "string", "description": "Priorité : low | normal | high", "default": "normal"},
                },
                "required": ["title"],
            },
            handler=plan_create_handler,
            category="autonomy",
            source_module="handlers.plans",
        ),
        HandlerDef(
            name="plan_list",
            description="Liste tous les plans existants. Peut filtrer par statut (active, done, all).",
            parameters={
                "properties": {
                    "status": {"type": "string", "description": "Filtre : all | active | done | failed", "default": "all"},
                },
                "required": [],
            },
            handler=plan_list_handler,
            category="autonomy",
            source_module="handlers.plans",
        ),
        HandlerDef(
            name="plan_update",
            description="Met à jour l'état d'une étape d'un plan (done, in_progress, failed) avec une note optionnelle.",
            parameters={
                "properties": {
                    "plan_id": {"type": "string", "description": "ID du plan à mettre à jour"},
                    "step_index": {"type": "integer", "description": "Index de l'étape (0-based). Null = mise à jour du plan entier.", "default": None},
                    "step_status": {"type": "string", "description": "Nouveau statut : done | in_progress | failed | pending", "default": "done"},
                    "note": {"type": "string", "description": "Note ou commentaire sur la mise à jour", "default": ""},
                },
                "required": ["plan_id"],
            },
            handler=plan_update_handler,
            category="autonomy",
            source_module="handlers.plans",
        ),
        HandlerDef(
            name="plan_done",
            description="Marque un plan entier comme terminé avec succès. Peut inclure un résumé des résultats.",
            parameters={
                "properties": {
                    "plan_id": {"type": "string", "description": "ID du plan à clôturer"},
                    "summary": {"type": "string", "description": "Résumé de ce qui a été accompli", "default": ""},
                },
                "required": ["plan_id"],
            },
            handler=plan_done_handler,
            category="autonomy",
            source_module="handlers.plans",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
