"""
agents.py - Handlers agents fragmentés depuis react.py.

Handlers: delegate_task, get_agents_status, fork_analyze,
          bg_start, bg_status, bg_list, bg_cancel,
          process_run, process_status, process_input, process_kill, process_list.

Chaque handler est une fonction async standalone:
    async def handler_name(ctx: HandlerContext, **kwargs) -> HandlerResult
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ─── Sub-Agent Handlers ───────────────────────────────────────────────────

async def delegate_task_handler(
    ctx: HandlerContext,
    description: str,
    agent_type: str = "general",
    context: dict = None,
) -> HandlerResult:
    """Délègue une tâche à un sub-agent."""
    try:
        from ...agents.sub_agent import delegate_to_agent

        # Le LLM peut passer context comme string JSON — normaliser en dict
        safe_context = context
        if isinstance(safe_context, str):
            import json as _json
            try:
                safe_context = _json.loads(safe_context)
            except (ValueError, TypeError):
                safe_context = {"raw": safe_context}
        if not isinstance(safe_context, dict):
            safe_context = {}

        # ── Injection automatique du contexte projet via resolve_workspace ──
        try:
            if "project_dir" not in safe_context:
                from ...utils.project_registry import resolve_workspace
                _resolution = resolve_workspace(description, context=safe_context, allow_create=False)
                if _resolution.path:
                    safe_context["workspace_path"] = str(_resolution.path)
                    safe_context["project_dir"] = str(_resolution.path)
                    try:
                        safe_context["project_files"] = [
                            f.name for f in _resolution.path.iterdir() if f.is_file()
                        ]
                    except Exception:
                        pass
            if "workspace_path" not in safe_context:
                safe_context["workspace_path"] = str(ctx.runtime_root)
        except Exception as _ctx_err:
            logger.debug(f"delegate_task: auto-context injection partielle: {_ctx_err}")

        result = await delegate_to_agent(description, agent_type, safe_context)
        return HandlerResult.ok(
            f"🤖 Résultat de {agent_type}Agent:\n{result}",
            handler_name="delegate_task",
        )
    except ImportError:
        return HandlerResult.fail(
            "❌ Module sub_agent non disponible",
            handler_name="delegate_task",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur délégation: {e}", handler_name="delegate_task"
        )


async def get_agents_status_handler(ctx: HandlerContext) -> HandlerResult:
    """Affiche le statut des sub-agents."""
    try:
        from ...agents.sub_agent import get_orchestrator

        orchestrator = get_orchestrator()
        output = orchestrator.format_status()
        return HandlerResult.ok(output, handler_name="get_agents_status")
    except ImportError:
        return HandlerResult.fail(
            "❌ Module sub_agent non disponible",
            handler_name="get_agents_status",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur: {e}", handler_name="get_agents_status"
        )


async def fork_analyze_handler(
    ctx: HandlerContext, objective: str, context: str = ""
) -> HandlerResult:
    """Analyse multi-perspective via Consciousness Forking."""
    try:
        from ...agents.sub_agent import get_orchestrator, AgentTask, AgentType
        from datetime import datetime

        orchestrator = get_orchestrator()
        agent = orchestrator.get_agent_by_name("ForkingAgent")
        if not agent:
            return HandlerResult.fail(
                "❌ ForkingAgent non disponible",
                handler_name="fork_analyze",
            )

        task = AgentTask(
            task_id=f"fork_{datetime.now().strftime('%H%M%S')}",
            description=objective,
            agent_type=AgentType.GENERAL,
            context={"forking_context": context} if context else {},
        )
        result = await agent.execute(task)

        meta = result.meta or {}
        header = (
            f"🧠 Consciousness Forking — "
            f"{meta.get('forks_succeeded', '?')}/{meta.get('forks_total', '?')} perspectives\n\n"
        )
        return HandlerResult.ok(
            header + result.output, handler_name="fork_analyze"
        )
    except ImportError:
        return HandlerResult.fail(
            "❌ Module forking_agent non disponible",
            handler_name="fork_analyze",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur fork_analyze: {e}", handler_name="fork_analyze"
        )


# ─── Background Task Handlers ─────────────────────────────────────────────

async def bg_start_handler(
    ctx: HandlerContext, name: str, command: str
) -> HandlerResult:
    """Lance une commande en arrière-plan."""
    try:
        from ...background.manager import get_task_manager

        manager = get_task_manager()
        task = await manager.start_command(name, command)

        return HandlerResult.ok(
            f"🚀 **Tâche lancée en arrière-plan**\n\n"
            f"**ID**: `{task.id}`\n"
            f"**Nom**: {task.name}\n"
            f"**Commande**: `{task.command}`\n"
            f"**Statut**: {task.status.value}\n\n"
            f'💡 Utilise `bg_status("{task.id}")` pour suivre la progression.',
            handler_name="bg_start",
        )
    except ImportError:
        return HandlerResult.fail(
            "❌ Module background non disponible",
            handler_name="bg_start",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur lancement tâche: {e}", handler_name="bg_start"
        )


async def bg_status_handler(
    ctx: HandlerContext, task_id: str
) -> HandlerResult:
    """Vérifie le statut d'une tâche."""
    try:
        from ...background.manager import get_task_manager

        manager = get_task_manager()
        status = await manager.get_status(task_id)

        if not status:
            return HandlerResult.fail(
                f"❌ Tâche `{task_id}` non trouvée",
                handler_name="bg_status",
            )

        status_icon = {
            "pending": "⏳",
            "running": "🔄",
            "completed": "✅",
            "failed": "❌",
            "cancelled": "⚠️",
        }.get(status["status"], "❓")

        result = (
            f"📊 **Statut de la tâche `{task_id}`**\n\n"
            f"**Nom**: {status['name']}\n"
            f"**Statut**: {status_icon} {status['status']}\n"
            f"**Durée**: {status['duration_seconds']:.1f}s"
        )

        if status["output"]:
            result += f"\n\n**Sortie**:\n```\n{status['output'][:500]}\n```"

        if status["error"]:
            result += f"\n\n**Erreur**:\n```\n{status['error'][:200]}\n```"

        return HandlerResult.ok(result, handler_name="bg_status")
    except ImportError:
        return HandlerResult.fail(
            "❌ Module background non disponible",
            handler_name="bg_status",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur: {e}", handler_name="bg_status"
        )


async def bg_list_handler(ctx: HandlerContext) -> HandlerResult:
    """Liste toutes les tâches background."""
    try:
        from ...background.manager import get_task_manager

        manager = get_task_manager()
        tasks = await manager.get_all_tasks()

        if not tasks:
            return HandlerResult.ok(
                "📋 Aucune tâche en arrière-plan",
                handler_name="bg_list",
            )

        result = "📋 **Tâches en arrière-plan**\n\n"
        for t in tasks:
            status_icon = {
                "pending": "⏳",
                "running": "🔄",
                "completed": "✅",
                "failed": "❌",
                "cancelled": "⚠️",
            }.get(t["status"], "❓")
            result += f"• `{t['id']}` {status_icon} **{t['name']}** ({t['status']})\n"

        return HandlerResult.ok(result, handler_name="bg_list")
    except ImportError:
        return HandlerResult.fail(
            "❌ Module background non disponible",
            handler_name="bg_list",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur: {e}", handler_name="bg_list"
        )


async def bg_cancel_handler(
    ctx: HandlerContext, task_id: str
) -> HandlerResult:
    """Annule une tâche en cours."""
    try:
        from ...background.manager import get_task_manager

        manager = get_task_manager()
        success = await manager.cancel_task(task_id)

        if success:
            return HandlerResult.ok(
                f"✅ Tâche `{task_id}` annulée",
                handler_name="bg_cancel",
            )
        else:
            return HandlerResult.fail(
                f"❌ Impossible d'annuler `{task_id}` (pas en cours ou non trouvée)",
                handler_name="bg_cancel",
            )
    except ImportError:
        return HandlerResult.fail(
            "❌ Module background non disponible",
            handler_name="bg_cancel",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur: {e}", handler_name="bg_cancel"
        )


# ─── Process Manager Handlers ─────────────────────────────────────────────

async def process_run_handler(
    ctx: HandlerContext, command: str, wait_ms: int = 5000
) -> HandlerResult:
    """Lance une commande avec background automatique."""
    try:
        from ...tools.process_manager import get_process_manager

        manager = get_process_manager()
        output, process_id = manager.run_background(
            command=command, wait_ms_before_async=wait_ms
        )

        if process_id:
            return HandlerResult.ok(
                f"⏳ Commande en background\n"
                f"**ID**: `{process_id}`\n"
                f"**Sortie partielle**:\n```\n{output[:500]}\n```\n"
                f'Utilise `process_status("{process_id}")` pour suivre.',
                handler_name="process_run",
            )
        else:
            return HandlerResult.ok(
                f"✅ Commande terminée:\n```\n{output}\n```",
                handler_name="process_run",
            )
    except ImportError:
        return HandlerResult.fail(
            "❌ Module process_manager non disponible",
            handler_name="process_run",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur: {e}", handler_name="process_run"
        )


async def process_status_handler(
    ctx: HandlerContext, process_id: str
) -> HandlerResult:
    """Récupère le statut d'un processus."""
    try:
        from ...tools.process_manager import get_process_manager

        manager = get_process_manager()
        output = await manager.get_status(process_id)
        return HandlerResult.ok(output, handler_name="process_status")
    except ImportError:
        return HandlerResult.fail(
            "❌ Module process_manager non disponible",
            handler_name="process_status",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur: {e}", handler_name="process_status"
        )


async def process_input_handler(
    ctx: HandlerContext, process_id: str, text: str
) -> HandlerResult:
    """Envoie de l'input à un processus."""
    try:
        from ...tools.process_manager import get_process_manager

        manager = get_process_manager()
        output = await manager.send_input(process_id, text)
        return HandlerResult.ok(output, handler_name="process_input")
    except ImportError:
        return HandlerResult.fail(
            "❌ Module process_manager non disponible",
            handler_name="process_input",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur: {e}", handler_name="process_input"
        )


async def process_kill_handler(
    ctx: HandlerContext, process_id: str
) -> HandlerResult:
    """Termine un processus."""
    try:
        from ...tools.process_manager import get_process_manager

        manager = get_process_manager()
        output = await manager.terminate(process_id)
        return HandlerResult.ok(output, handler_name="process_kill")
    except ImportError:
        return HandlerResult.fail(
            "❌ Module process_manager non disponible",
            handler_name="process_kill",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur: {e}", handler_name="process_kill"
        )


async def process_list_handler(ctx: HandlerContext) -> HandlerResult:
    """Liste les processus."""
    try:
        from ...tools.process_manager import get_process_manager

        manager = get_process_manager()
        output = await manager.list_processes()
        return HandlerResult.ok(output, handler_name="process_list")
    except ImportError:
        return HandlerResult.fail(
            "❌ Module process_manager non disponible",
            handler_name="process_list",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur: {e}", handler_name="process_list"
        )


# ─── HandlerDefs ───────────────────────────────────────────────────────────

def get_agents_handler_defs() -> List[HandlerDef]:
    """Retourne les définitions des 12 handlers agents."""
    return [
        HandlerDef(
            name="delegate_task",
            description="Délègue une tâche à un sub-agent spécialisé. QUAND utiliser: recherche web multi-sources (research), debug/traceback complexe (debug), refacto module entier (refactor), planification multi-étapes (planner), génération code isolée (code), opérations fichiers en masse (file). Utilise dès qu'une sous-tâche prendrait >3 itérations seul.",
            parameters={
                "properties": {
                    "description": {"type": "string", "description": "Description précise de la tâche à déléguer"},
                    "agent_type": {"type": "string", "description": "code=génération/analyse code isolée | research=web/docs multi-sources | file=fichiers en masse | debug=traceback/runtime errors | refactor=restructuration module | planner=décomposition multi-étapes | general=tâche mixte"},
                    "context": {"type": "object", "description": "Contexte optionnel (file_path, query, url, ou tool/args pour appel explicite)"},
                },
                "required": ["description"],
            },
            handler=delegate_task_handler,
            category="agents",
            source_module="handlers.agents",
        ),
        HandlerDef(
            name="get_agents_status",
            description="Affiche le statut de tous les sub-agents (CodeAgent, ResearchAgent, FileAgent)",
            parameters={"properties": {}, "required": []},
            handler=get_agents_status_handler,
            category="agents",
            source_module="handlers.agents",
        ),
        HandlerDef(
            name="fork_analyze",
            description="Analyse une question complexe sous 4 angles (optimiste, paranoïaque, créatif, conservateur) puis synthétise un consensus. Utiliser pour les décisions architecturales, les choix stratégiques, ou quand la réponse n'est pas évidente.",
            parameters={
                "properties": {
                    "objective": {"type": "string", "description": "La question ou tâche à analyser"},
                    "context": {"type": "string", "description": "Contexte additionnel"},
                },
                "required": ["objective"],
            },
            handler=fork_analyze_handler,
            category="agents",
            source_module="handlers.agents",
        ),
        HandlerDef(
            name="bg_start",
            description="Lance une commande en arrière-plan. Pour les tâches longues (scan, backup, download, etc.)",
            parameters={
                "properties": {
                    "name": {"type": "string", "description": "Nom descriptif de la tâche"},
                    "command": {"type": "string", "description": "Commande shell à exécuter"},
                },
                "required": ["name", "command"],
            },
            handler=bg_start_handler,
            category="agents",
            source_module="handlers.agents",
        ),
        HandlerDef(
            name="bg_status",
            description="Vérifie le statut d'une tâche en arrière-plan.",
            parameters={
                "properties": {
                    "task_id": {"type": "string", "description": "ID de la tâche"},
                },
                "required": ["task_id"],
            },
            handler=bg_status_handler,
            category="agents",
            source_module="handlers.agents",
        ),
        HandlerDef(
            name="bg_list",
            description="Liste toutes les tâches en arrière-plan (en cours et terminées).",
            parameters={"properties": {}, "required": []},
            handler=bg_list_handler,
            category="agents",
            source_module="handlers.agents",
        ),
        HandlerDef(
            name="bg_cancel",
            description="Annule une tâche en cours d'exécution.",
            parameters={
                "properties": {
                    "task_id": {"type": "string", "description": "ID de la tâche à annuler"},
                },
                "required": ["task_id"],
            },
            handler=bg_cancel_handler,
            category="agents",
            source_module="handlers.agents",
        ),
        HandlerDef(
            name="process_run",
            description="Lance une commande. Si elle dure > 5s, passe en background automatiquement. Permet d'envoyer de l'input ensuite.",
            parameters={
                "properties": {
                    "command": {"type": "string", "description": "Commande à exécuter"},
                    "wait_ms": {"type": "integer", "description": "Temps d'attente avant background", "default": 5000},
                },
                "required": ["command"],
            },
            handler=process_run_handler,
            category="agents",
            source_module="handlers.agents",
        ),
        HandlerDef(
            name="process_status",
            description="Récupère le statut et la sortie d'un processus en cours.",
            parameters={
                "properties": {
                    "process_id": {"type": "string", "description": "ID du processus"},
                },
                "required": ["process_id"],
            },
            handler=process_status_handler,
            category="agents",
            source_module="handlers.agents",
        ),
        HandlerDef(
            name="process_input",
            description="Envoie du texte à un processus interactif (répondre à un prompt, etc.).",
            parameters={
                "properties": {
                    "process_id": {"type": "string", "description": "ID du processus"},
                    "text": {"type": "string", "description": "Texte à envoyer (+ Enter automatique)"},
                },
                "required": ["process_id", "text"],
            },
            handler=process_input_handler,
            category="agents",
            source_module="handlers.agents",
        ),
        HandlerDef(
            name="process_kill",
            description="Termine un processus en cours.",
            parameters={
                "properties": {
                    "process_id": {"type": "string", "description": "ID du processus à terminer"},
                },
                "required": ["process_id"],
            },
            handler=process_kill_handler,
            category="agents",
            source_module="handlers.agents",
        ),
        HandlerDef(
            name="process_list",
            description="Liste tous les processus en cours et terminés.",
            parameters={"properties": {}, "required": []},
            handler=process_list_handler,
            category="agents",
            source_module="handlers.agents",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
