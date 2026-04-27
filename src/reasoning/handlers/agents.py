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
    project_path: str = "",
) -> HandlerResult:
    """Délègue une tâche à un sub-agent."""
    try:
        from ...agents.task_context import TaskContext
        from ...utils.project_registry import resolve_workspace
        from ...agents.sub_agent import delegate_to_agent_full

        _lum = ctx.lumena
        _mem_fn = None
        if _lum:
            _mem = getattr(_lum, "memory", None)
            if _mem and hasattr(_mem, "get_context_for_prompt"):
                _mem_fn = _mem.get_context_for_prompt

        # ── Mission C : si project_path absent, tenter le contexte projet récent ──
        # Permet de réutiliser le dernier projet créé/modifié sans fuzzy-match risqué.
        _effective_project_path = project_path
        if not _effective_project_path:
            try:
                _id_svc = getattr(_lum, "_identity_svc", None) if _lum else None
                _rt_ctx = getattr(ctx, "runtime_ctx", None) or getattr(_lum, "runtime_ctx", None)
                if _id_svc is not None and _rt_ctx is not None:
                    from ...core_services.identity_service import IdentityService as _IDS
                    _ck = _IDS.resolve_channel_key(_rt_ctx)
                    _rpc = _id_svc.get_recent_code_context(_ck) if _ck else None
                    if _rpc:
                        import os as _os
                        _rpc_path = _rpc.get("workspace_path", "")
                        if _rpc_path and _os.path.isdir(_rpc_path):
                            _effective_project_path = _rpc_path
                            logger.info(
                                "delegate_task: project_path depuis contexte récent: {}",
                                _rpc_path[:80],
                            )
            except Exception as _rpc_exc:
                logger.debug("delegate_task: récupération contexte récent échouée: {}", _rpc_exc)

        task_ctx = TaskContext.from_delegate_call(
            description=description,
            context=context,
            project_path=_effective_project_path,
            runtime_root=ctx.runtime_root,
            resolve_workspace_fn=resolve_workspace,
            memory_fn=_mem_fn,
        )

        # ── Guard anti-fuzzy-routing ──────────────────────────────────────
        # Si la résolution vient du registre avec une confiance < seuil et que
        # l'agent n'a pas fourni de project_path explicite → on rejette.
        # Cela empêche de modifier le mauvais projet sur un match ambigu.
        #
        # Seuil dynamique :
        # - 0.80 si le nom du projet résolu apparaît dans la description (signal fort)
        # - 0.90 par défaut (match ambigu)
        _REGISTRY_CONF_THRESHOLD = 0.90
        _REGISTRY_CONF_THRESHOLD_RELAXED = 0.80
        if (
            not project_path
            and task_ctx.resolution_source.startswith("registry:")
            and task_ctx.confidence < _REGISTRY_CONF_THRESHOLD
        ):
            _matched = str(task_ctx.workspace_path or "?")
            # Vérifier si le nom du projet apparaît dans la description — signal de non-ambiguïté
            try:
                from pathlib import Path as _P
                _project_name = _P(_matched).name.lower()
                _desc_lower = (description or "").lower()
                _name_in_desc = bool(_project_name) and len(_project_name) >= 3 and _project_name in _desc_lower
            except Exception:
                _name_in_desc = False

            if _name_in_desc and task_ctx.confidence >= _REGISTRY_CONF_THRESHOLD_RELAXED:
                logger.info(
                    "delegate_task: fuzzy routing accepté (conf={:.2f}, nom '{}' trouvé dans description)",
                    task_ctx.confidence, _project_name,
                )
            else:
                logger.warning(
                    "delegate_task: fuzzy routing rejeté — conf={:.2f} < {:.2f}, match={}",
                    task_ctx.confidence, _REGISTRY_CONF_THRESHOLD, _matched,
                )
                return HandlerResult.fail(
                    f"⛔ Projet ambigu (confiance {task_ctx.confidence:.0%} < 90%). "
                    f"Le registre a trouvé `{_matched}` mais le match n'est pas assez précis. "
                    "Fournis `project_path` explicitement dans ton appel `delegate_task` "
                    "pour éviter de modifier le mauvais projet. "
                    "Exemple : `delegate_task(description='...', project_path='C:\\\\...\\\\workspace\\\\mon-projet')`",
                    handler_name="delegate_task",
                )
        # ─────────────────────────────────────────────────────────────────

        safe_context = task_ctx.to_legacy_dict()

        # ── Injection des skills actifs dans le CodeAgent ──
        try:
            from ...skills.loader import build_active_skills_context
            _skills_ctx = build_active_skills_context(description, max_results=2, max_chars=3000)
            if _skills_ctx:
                safe_context["skills_context"] = _skills_ctx
        except Exception:
            pass

        logger.info("delegate_task: {}", task_ctx.summary())

        result = await delegate_to_agent_full(description, agent_type, safe_context)

        # ── Vérification que les fichiers annoncés existent réellement ──
        _missing_artifacts: list = []
        if result.artifacts and result.success:
            import os as _os_art
            for _art_path in result.artifacts[:20]:
                try:
                    if not (_os_art.path.isfile(str(_art_path)) and _os_art.path.getsize(str(_art_path)) > 0):
                        _missing_artifacts.append(str(_art_path))
                except Exception:
                    pass

        # ── Rapport structuré ──
        _icon = "✅" if result.success else "❌"
        _duration = f"{result.duration_ms / 1000:.1f}s" if result.duration_ms else "N/A"
        _artifacts_str = ""
        if result.artifacts:
            _artifacts_str = "\n**Fichiers** : " + ", ".join(f"`{a}`" for a in result.artifacts[:20])
        if _missing_artifacts:
            _artifacts_str += "\n⚠️ **Fichiers annoncés mais absents ou vides** : " + ", ".join(f"`{p}`" for p in _missing_artifacts)
        _meta = result.meta or {}
        _iterations = _meta.get("iterations", "?")
        _report = (
            f"{_icon} **{agent_type}Agent terminé** ({_duration}, {_iterations} itérations)"
            f"{_artifacts_str}\n\n"
            f"{result.output}"
        )
        # Sur échec : ajouter le contexte exploitable pour la reprise
        if not result.success and _meta.get("blocked_at"):
            _failure_lines = []
            if _meta.get("attempted"):
                _failure_lines.append("**Dernières actions tentées** :\n" + "\n".join(f"- {a}" for a in _meta["attempted"][-3:]))
            _failure_lines.append(f"**Bloqué à** : {_meta['blocked_at']}")
            if _meta.get("next_step"):
                _failure_lines.append(f"**Prochaine étape recommandée** : {_meta['next_step']}")
            _report += "\n\n" + "\n".join(_failure_lines)
        return HandlerResult.ok(_report, handler_name="delegate_task")
    except ImportError:
        return HandlerResult.fail(
            "❌ Module sub_agent non disponible",
            handler_name="delegate_task",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur délégation: {e}", handler_name="delegate_task"
        )


async def delegate_task_bg_handler(
    ctx: HandlerContext,
    description: str,
    agent_type: str = "code",
    context: dict = None,
    project_path: str = "",
) -> HandlerResult:
    """Délègue une tâche au CodeAgent en arrière-plan — retourne immédiatement un task_id."""
    try:
        from ...agents.task_context import TaskContext
        from ...utils.project_registry import resolve_workspace
        from ...agents.sub_agent import delegate_to_agent_bg

        _lum = ctx.lumena
        _mem_fn = None
        if _lum:
            _mem = getattr(_lum, "memory", None)
            if _mem and hasattr(_mem, "get_context_for_prompt"):
                _mem_fn = _mem.get_context_for_prompt

        # ── Mission C : si project_path absent, tenter le contexte projet récent ──
        _effective_project_path_bg = project_path
        if not _effective_project_path_bg:
            try:
                _id_svc_bg = getattr(_lum, "_identity_svc", None) if _lum else None
                _rt_ctx_bg = getattr(ctx, "runtime_ctx", None) or getattr(_lum, "runtime_ctx", None)
                if _id_svc_bg is not None and _rt_ctx_bg is not None:
                    from ...core_services.identity_service import IdentityService as _IDS_BG
                    _ck_bg = _IDS_BG.resolve_channel_key(_rt_ctx_bg)
                    _rpc_bg = _id_svc_bg.get_recent_code_context(_ck_bg) if _ck_bg else None
                    if _rpc_bg:
                        import os as _os_bg
                        _rpc_path_bg = _rpc_bg.get("workspace_path", "")
                        if _rpc_path_bg and _os_bg.path.isdir(_rpc_path_bg):
                            _effective_project_path_bg = _rpc_path_bg
                            logger.info(
                                "delegate_task_bg: project_path depuis contexte récent: {}",
                                _rpc_path_bg[:80],
                            )
            except Exception as _rpc_exc_bg:
                logger.debug("delegate_task_bg: récupération contexte récent échouée: {}", _rpc_exc_bg)

        task_ctx = TaskContext.from_delegate_call(
            description=description,
            context=context,
            project_path=_effective_project_path_bg,
            runtime_root=ctx.runtime_root,
            resolve_workspace_fn=resolve_workspace,
            memory_fn=_mem_fn,
        )
        safe_context = task_ctx.to_legacy_dict()

        # ── Injection des skills actifs dans le CodeAgent bg ──
        try:
            from ...skills.loader import build_active_skills_context
            _skills_ctx = build_active_skills_context(description, max_results=2, max_chars=3000)
            if _skills_ctx:
                safe_context["skills_context"] = _skills_ctx
        except Exception:
            pass

        logger.info("delegate_task_bg: {}", task_ctx.summary())

        # ── Construire le progress_callback ──
        _progress_cb = None
        try:
            _lum = ctx.lumena
            if _lum and getattr(_lum, "_on_response_callbacks", None):
                def _push_progress(msg: str):
                    for cb in _lum._on_response_callbacks:
                        try:
                            cb(f"🔄 {msg}")
                        except Exception:
                            pass
                _progress_cb = _push_progress
        except Exception:
            pass

        task_id = await delegate_to_agent_bg(
            description, agent_type, safe_context,
            progress_callback=_progress_cb,
        )
        return HandlerResult.ok(
            f"🚀 **CodeAgent lancé en arrière-plan**\n"
            f"- **ID** : `{task_id}`\n"
            f"- **Tâche** : {description[:200]}\n"
            f"- Utilise `bg_status(task_id=\"{task_id}\")` pour suivre la progression.",
            handler_name="delegate_task_bg",
        )
    except ImportError:
        return HandlerResult.fail(
            "❌ Module sub_agent non disponible",
            handler_name="delegate_task_bg",
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur délégation bg: {e}", handler_name="delegate_task_bg"
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
    """Vérifie le statut d'une tâche (shell ou agent)."""

    # ── 1. Vérifier d'abord les tâches agent (ca_*) ──
    if task_id.startswith("ca_") or task_id.startswith("task_"):
        try:
            from ...agents.sub_agent import get_orchestrator
            orchestrator = get_orchestrator()
            task_info = orchestrator.pending_tasks.get(task_id)
            if task_info:
                _status = task_info.get("status", "unknown")
                _status_icon = {
                    "running": "🔄", "done": "✅", "failed": "❌",
                }.get(_status, "❓")

                # Progression détaillée si disponible
                progress = task_info.get("progress", {})
                if progress and _status == "running":
                    pct = progress.get("pct", 0)
                    bar = "█" * (pct // 10) + "░" * (10 - pct // 10)
                    return HandlerResult.ok(
                        f"⏳ **CodeAgent en cours** [{bar}] {pct}%\n"
                        f"- **Itération**: {progress.get('iteration')}/{progress.get('max_iter')}\n"
                        f"- **Action**: {progress.get('last_action')} `{progress.get('last_path', '')}`\n"
                        f"- **ID**: `{task_id}`",
                        handler_name="bg_status",
                    )

                result_text = (
                    f"📊 **Tâche agent `{task_id}`**\n\n"
                    f"**Statut**: {_status_icon} {_status}\n"
                    f"**Description**: {task_info.get('description', 'N/A')[:200]}"
                )
                if task_info.get("output"):
                    result_text += f"\n\n**Résultat**:\n{task_info['output'][:500]}"
                if task_info.get("started_at"):
                    result_text += f"\n**Démarré**: {task_info['started_at']}"
                if task_info.get("finished_at"):
                    result_text += f"\n**Terminé**: {task_info['finished_at']}"
                return HandlerResult.ok(result_text, handler_name="bg_status")
        except ImportError:
            pass
        except Exception as _e:
            logger.debug(f"bg_status agent lookup: {_e}")

    # ── 2. Fallback: tâches shell background ──
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
        output, process_id = await manager.run_background(
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
            description=(
                "Délègue une tâche de code au CodeAgent et RETOURNE le résultat ici. "
                "Le CodeAgent peut lire/écrire des fichiers, exécuter des commandes, et itérer jusqu'à 50 fois. "
                "QUAND utiliser : créer/modifier un site ou projet, corriger un bug, refactorer du code, "
                "écrire du code complexe, créer des fichiers en masse. "
                "IMPORTANT : Passe une description DETAILLEE de ce que tu veux. "
                "Le contexte (workspace, fichiers, conversation) est injecté automatiquement. "
                "Après le résultat, tu peux enchaîner avec d'autres outils (deploy, mail, etc.)."
            ),
            parameters={
                "properties": {
                    "description": {"type": "string", "description": "Description DÉTAILLÉE de la tâche de code à réaliser. Inclure : quoi faire, quel style, quelles contraintes."},
                    "agent_type": {"type": "string", "description": "code=génération/modification code | research=recherche multi-sources | file=fichiers en masse | debug=traceback/runtime errors | refactor=restructuration | planner=décomposition multi-étapes | general=tâche mixte", "default": "code"},
                    "context": {"type": "object", "description": "Contexte additionnel optionnel. workspace_path, project_dir, conversation_history sont injectés automatiquement si absents."},
                    "project_path": {"type": "string", "description": "Chemin absolu du projet cible. Si fourni, le CodeAgent travaillera EXACTEMENT sur ce dossier (pas de fuzzy match)."},
                },
                "required": ["description"],
            },
            handler=delegate_task_handler,
            category="agents",
            source_module="handlers.agents",
        ),
        HandlerDef(
            name="delegate_task_bg",
            description=(
                "Lance une tâche CodeAgent en ARRIÈRE-PLAN et retourne immédiatement un task_id. "
                "Utiliser pour les tâches longues (création de projet, refactoring complet, etc.) "
                "pour ne pas bloquer la conversation. "
                "Suivi via bg_status(task_id). La progression est affichée automatiquement dans le chat."
            ),
            parameters={
                "properties": {
                    "description": {"type": "string", "description": "Description DÉTAILLÉE de la tâche de code"},
                    "agent_type": {"type": "string", "description": "code | research | file | debug | refactor | planner | general", "default": "code"},
                    "context": {"type": "object", "description": "Contexte additionnel optionnel"},
                    "project_path": {"type": "string", "description": "Chemin absolu du projet cible (pas de fuzzy match si fourni)."},
                },
                "required": ["description"],
            },
            handler=delegate_task_bg_handler,
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
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
