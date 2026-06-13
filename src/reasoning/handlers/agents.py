"""
agents.py - Handlers agents fragmentés depuis react.py.

Handlers: delegate_task, get_agents_status, fork_analyze,
          bg_start, bg_status, bg_list, bg_cancel,
          process_run, process_status, process_input, process_kill, process_list.

Chaque handler est une fonction async standalone:
    async def handler_name(ctx: HandlerContext, **kwargs) -> HandlerResult
"""

from __future__ import annotations

import asyncio
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


def _get_task_orchestrator():
    """Retourne le TaskOrchestrator global, ou None si non disponible."""
    try:
        from ...core_services import deps
        return getattr(deps, "_TASK_ORCHESTRATOR", None)
    except Exception:
        return None


async def _watch_delegate_cancel(
    parent_task_id: str,
    exec_task: "asyncio.Task[Any]",
    poll_interval: float = 0.3,
) -> None:
    """Surveille le cancel du parent SSE et annule exec_task si demandé."""
    try:
        orch = _get_task_orchestrator()
        if orch is None:
            return
        while not exec_task.done():
            await asyncio.sleep(poll_interval)
            try:
                if orch.is_cancel_requested(parent_task_id):
                    exec_task.cancel()
                    return
            except Exception:
                pass
    except asyncio.CancelledError:
        pass


# ─── Sub-Agent Handlers ───────────────────────────────────────────────────

_MCP_DELEGATE_GUARD_TOKENS = (
    "mcp_local_create",
    "mcp_install:",
    "mcp_activate:",
    "run_mcp_autonomy",
    "resume_mcp_task",
    "request_mcp_ticket",
    "request_mcp_capability",
    "i-confirm-mcp-ticket",
    "i-confirm-mcp-autonomy",
    "ticket mcp",
    "panel mcp",
    "materialiser local mcp",
    "matérialiser local mcp",
)


def _looks_like_mcp_control_flow(description: str, context: Any) -> bool:
    parts = [description or ""]
    if isinstance(context, dict):
        for value in context.values():
            if isinstance(value, (str, int, float, bool)):
                parts.append(str(value))
    elif isinstance(context, str):
        parts.append(context)
    haystack = "\n".join(parts).lower()
    return any(token in haystack for token in _MCP_DELEGATE_GUARD_TOKENS)


def _path_within(child: Path, root: Path) -> bool:
    try:
        child.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _path_mentioned_by_user(path: Path, original_user_query: str) -> bool:
    if not original_user_query:
        return False
    raw = str(path)
    return (
        raw in original_user_query
        or raw.replace("\\", "/") in original_user_query
        or raw in original_user_query.replace("/", "\\")
    )


_CODE_AGENT_KINDS = {"code", "debug", "refactor"}


def _fold_delegate_text(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text or "")
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return folded.lower()


def _coerce_positive_int(value: Any) -> int:
    try:
        if isinstance(value, bool):
            return 0
        return max(0, int(value))
    except Exception:
        return 0


def _suspicious_delegate_success_reason(result: Any, agent_kind: str) -> str:
    """Retourne une raison si un succès CodeAgent ressemble à une non-exécution."""
    if (agent_kind or "").strip().lower() not in _CODE_AGENT_KINDS:
        return ""

    output = str(getattr(result, "output", "") or "")
    folded = _fold_delegate_text(output)
    no_work_markers = (
        "run_tests : test_path requis",
        "test_path requis pour run_tests",
        "test_path required",
        "aucun test runner detecte",
        "aucun test runner détecte",
        "aucun test runner détecté",
    )
    if any(marker in folded for marker in no_work_markers):
        return "rapport CodeAgent contenant une non-exécution d'outil"

    no_result_markers = (
        "aucun resultat pour",
        "aucun resultat trouve",
        "aucun resultat trouve pour",
        "aucun r?sultat pour",
        "aucun r?sultat trouve",
        "no results for",
        "no matching files",
    )
    if any(marker in folded for marker in no_result_markers):
        return "rapport CodeAgent sans action productive (recherche sans résultat)"

    meta = getattr(result, "meta", {}) or {}
    raw_iterations = meta.get("iterations", None)
    iterations = _coerce_positive_int(raw_iterations)
    duration_ms = _coerce_positive_int(getattr(result, "duration_ms", 0))
    artifacts = list(getattr(result, "artifacts", []) or [])

    if raw_iterations in (None, "", "?") and duration_ms < 500 and not artifacts:
        return "rapport CodeAgent sans itérations ni durée crédible"
    if iterations <= 0 and duration_ms < 500 and not artifacts:
        return "rapport CodeAgent avec zéro itération productive"
    return ""


async def delegate_task_handler(
    ctx: HandlerContext,
    description: str,
    agent_type: str = "general",
    context: dict = None,
    project_path: str = "",
) -> HandlerResult:
    """Délègue une tâche à un sub-agent."""
    try:
        # ── Garde-fou : CodeAgent = développement uniquement ──────────────
        # Le CodeAgent (et ses variantes debug/refactor) ne doit PAS créer de
        # documents (PDF, DOCX, rapport texte…). Ces tâches passent par les
        # outils directs ReAct (create_pdf / create_docx / write_file).
        # Sans ce garde-fou, le modèle délègue "Crée un PDF" au CodeAgent qui
        # interprète la tâche comme une recherche de code et échoue en silence.
        from ..file_categories import looks_like_document_creation
        _dev_agents = {"code", "debug", "refactor"}
        _agent_kind = (agent_type or "").strip().lower()
        if _agent_kind in _dev_agents and _looks_like_mcp_control_flow(description, context):
            logger.warning(
                "delegate_task refusé : flux MCP délégué à '{}'Agent (desc: {})",
                agent_type, (description or "")[:120],
            )
            return HandlerResult.fail(
                "⛔ Le CodeAgent ne doit pas remplacer la chaîne MCP. "
                "Pour un ticket MCP approuvé ou une reprise MCP, utilise "
                "`resume_mcp_task` puis le panel MCP (`Materialiser local MCP`, "
                "install, activation). Aucun fichier MCP ne doit être créé par "
                "CodeAgent hors du rail d'approbation.",
                handler_name="delegate_task",
            )
        if _agent_kind in _dev_agents and looks_like_document_creation(description):
            logger.warning(
                "delegate_task refusé : tâche document déléguée à '{}'Agent (desc: {})",
                agent_type, (description or "")[:120],
            )
            return HandlerResult.fail(
                "⛔ Le CodeAgent est réservé au développement (code source), "
                "pas à la création de documents. "
                "Pour produire un PDF / DOCX / rapport, utilise directement les "
                "outils ReAct : `create_pdf`, `create_docx` ou `write_file`. "
                "N'utilise `delegate_task` que pour écrire ou modifier du code.",
                handler_name="delegate_task",
            )
        # ──────────────────────────────────────────────────────────────────

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

        # Phase 0.6 : injecter la demande utilisateur originale (verbatim)
        # dans le context passé au sub-agent. La reformulation LLM (description)
        # peut perdre l'intent ; on garde la phrase exacte pour Architect/CodeAgent.
        # Cf. DIAGNOSTIC_PROD.md §14.
        _orig_user_q = getattr(ctx, "original_user_query", "") or ""
        if _orig_user_q:
            if context is None:
                context = {}
            if isinstance(context, dict) and "user_original_request" not in context:
                context["user_original_request"] = _orig_user_q

        task_ctx = TaskContext.from_delegate_call(
            description=description,
            context=context,
            project_path=_effective_project_path,
            runtime_root=ctx.runtime_root,
            resolve_workspace_fn=resolve_workspace,
            memory_fn=_mem_fn,
        )

        # Guard anti path hallucination:
        # A dev-agent may use an external path only if it came from an explicit
        # project_path or from the user's original wording. If the LLM invents
        # a path inside the delegated description/context, keep CodeAgent inside
        # the current runtime scope instead of creating files elsewhere.
        if (
            _agent_kind in _dev_agents
            and task_ctx.workspace_path is not None
            and task_ctx.resolution_source in {"explicit_text", "explicit_text_new"}
            and ctx.runtime_root is not None
            and not _path_within(task_ctx.workspace_path, Path(ctx.runtime_root))
            and not _path_mentioned_by_user(task_ctx.workspace_path, _orig_user_q)
        ):
            logger.warning(
                "delegate_task refuse : chemin hors runtime issu de la reformulation LLM ({})",
                task_ctx.workspace_path,
            )
            return HandlerResult.fail(
                "⛔ Chemin de travail hors scope refusé pour CodeAgent. "
                f"Le chemin `{task_ctx.workspace_path}` provient de la "
                "description/contexte généré, pas d'un chemin explicite de "
                "l'utilisateur. Fournis un `project_path` explicite ou travaille "
                "dans le workspace Lumena courant.",
                handler_name="delegate_task",
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

        # ── Canal cancel coopératif ───────────────────────────────────────────
        _parent_task_id = ctx.runtime_task_id
        if _parent_task_id:
            _orch = _get_task_orchestrator()
            # Rejet immédiat si déjà annulé avant de lancer le sous-agent
            if _orch and _orch.is_cancel_requested(_parent_task_id):
                return HandlerResult.fail(
                    "🚫 Tâche annulée — sous-agent non démarré.",
                    handler_name="delegate_task",
                    status_code="cancelled",
                )
            from ...agents.sub_agent import _register_active_delegate, _unregister_active_delegate
            _exec_task: asyncio.Task = asyncio.create_task(
                delegate_to_agent_full(description, agent_type, safe_context)
            )
            _register_active_delegate(_parent_task_id, _exec_task)
            _watcher = asyncio.create_task(
                _watch_delegate_cancel(_parent_task_id, _exec_task)
            )
            try:
                result = await _exec_task
            except asyncio.CancelledError:
                logger.info("[delegate_task] sous-agent annulé (parent={})", _parent_task_id)
                return HandlerResult.fail(
                    "🚫 Sous-agent interrompu : tâche parent annulée.",
                    handler_name="delegate_task",
                    status_code="cancelled",
                )
            finally:
                _unregister_active_delegate(_parent_task_id)
                _watcher.cancel()
        else:
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
        _meta = result.meta or {}
        _suspicious_reason = (
            _suspicious_delegate_success_reason(result, _agent_kind)
            if result.success else ""
        )
        _effective_success = bool(result.success) and not _suspicious_reason
        _icon = "✅" if _effective_success else "❌"
        _duration = f"{result.duration_ms / 1000:.1f}s" if result.duration_ms else "N/A"
        _artifacts_str = ""
        if result.artifacts:
            _artifacts_str = "\n**Fichiers** : " + ", ".join(f"`{a}`" for a in result.artifacts[:20])
        if _missing_artifacts:
            _artifacts_str += "\n⚠️ **Fichiers annoncés mais absents ou vides** : " + ", ".join(f"`{p}`" for p in _missing_artifacts)
        _iterations = _meta.get("iterations", "?")
        _report = (
            f"{_icon} **{agent_type}Agent terminé** ({_duration}, {_iterations} itérations)"
            f"{_artifacts_str}\n\n"
            f"{result.output}"
        )
        if _suspicious_reason:
            _report += (
                "\n\n❌ **Livraison refusée** : "
                f"{_suspicious_reason}. Lumena doit relancer ou continuer au lieu de finaliser."
            )
        # Sur échec : ajouter le contexte exploitable pour la reprise
        if not _effective_success and _meta.get("blocked_at"):
            _failure_lines = []
            if _meta.get("attempted"):
                _failure_lines.append("**Dernières actions tentées** :\n" + "\n".join(f"- {a}" for a in _meta["attempted"][-3:]))
            _failure_lines.append(f"**Bloqué à** : {_meta['blocked_at']}")
            if _meta.get("next_step"):
                _failure_lines.append(f"**Prochaine étape recommandée** : {_meta['next_step']}")
            _report += "\n\n" + "\n".join(_failure_lines)
        # Propager le succès/échec réel du sous-agent dans HandlerResult.
        # Si result.success=False, observation.success sera False → _update_plan_progress
        # ne sera pas appelé et aucune tâche ne sera cochée à tort.
        if _effective_success:
            return HandlerResult.ok(_report, handler_name="delegate_task", status_code=result.status_code)
        return HandlerResult.fail(
            _report,
            handler_name="delegate_task",
            status_code=("suspicious_success" if _suspicious_reason else result.status_code),
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
                    "running": "🔄", "done": "✅", "failed": "❌", "cancelled": "🚫",
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
                # Propager le statut réel dans HandlerResult.
                if _status == "failed":
                    return HandlerResult.fail(
                        result_text, output=result_text,
                        handler_name="bg_status", status_code="failed",
                    )
                if _status == "cancelled":
                    return HandlerResult.ok(
                        result_text, handler_name="bg_status", status_code="cancelled"
                    )
                return HandlerResult.ok(result_text, handler_name="bg_status", status_code=_status)
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
    """Liste toutes les tâches background (agents + shell)."""
    _AGENT_ICONS = {"running": "🔄", "done": "✅", "failed": "❌", "cancelled": "🚫"}
    _SHELL_ICONS = {
        "pending": "⏳", "running": "🔄", "completed": "✅",
        "failed": "❌", "cancelled": "⚠️",
    }
    lines: List[str] = []

    # ── 1. Tâches agent background ──
    try:
        from ...agents.sub_agent import get_orchestrator
        orchestrator = get_orchestrator()
        for tid, info in orchestrator.pending_tasks.items():
            _s = info.get("status", "?")
            _icon = _AGENT_ICONS.get(_s, "❓")
            _desc = (info.get("description") or "")[:60]
            lines.append(f"• `{tid}` {_icon} **agent** ({_s}) — {_desc}")
    except Exception:
        pass

    # ── 2. Tâches shell background ──
    try:
        from ...background.manager import get_task_manager
        manager = get_task_manager()
        shell_tasks = await manager.get_all_tasks()
        for t in shell_tasks:
            _icon = _SHELL_ICONS.get(t["status"], "❓")
            lines.append(f"• `{t['id']}` {_icon} **{t['name']}** ({t['status']})")
    except ImportError:
        pass
    except Exception as e:
        logger.debug("bg_list shell tasks: {}", e)

    if not lines:
        return HandlerResult.ok("📋 Aucune tâche en arrière-plan", handler_name="bg_list")

    return HandlerResult.ok(
        "📋 **Tâches en arrière-plan**\n\n" + "\n".join(lines),
        handler_name="bg_list",
    )


async def bg_cancel_handler(
    ctx: HandlerContext, task_id: str
) -> HandlerResult:
    """Annule une tâche en cours (agent bg ou shell)."""

    # ── 1. Tâches agent background (ca_* / task_*) ──
    if task_id.startswith("ca_") or task_id.startswith("task_"):
        try:
            from ...agents.sub_agent import cancel_bg_agent_task
            if cancel_bg_agent_task(task_id):
                return HandlerResult.ok(
                    f"✅ Tâche agent `{task_id}` annulée",
                    handler_name="bg_cancel",
                    status_code="cancelled",
                )
            else:
                return HandlerResult.fail(
                    f"❌ Tâche agent `{task_id}` introuvable ou déjà terminée",
                    handler_name="bg_cancel",
                )
        except ImportError:
            pass
        except Exception as _e:
            logger.debug("bg_cancel agent lookup: {}", _e)

    # ── 2. Tâches shell background ──
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
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
