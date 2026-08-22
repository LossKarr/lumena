"""Opt-in Agent/Mission execution through Codex plus Lumena's tool registry.

This module is a routing adapter, not a second agent runtime.  Codex chooses the
next tool; the live ReAct instance still owns policies, mission context, ledger,
truth locks, task state and the final delivery chokepoint.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Mapping, Sequence

from loguru import logger

from src.llm.codex_app_server import (
    CodexAppServerConfig,
    CodexAppServerError,
    CodexAppServerSupervisor,
    CodexAppServerTimeout,
    codex_compatibility_config_overrides,
    get_shared_codex_app_server,
)
from src.llm.codex_mcp_bridge import LumenaCodexToolBridge
from src.llm.codex_subscription import (
    CodexSubscriptionGateway,
    CodexSubscriptionSettings,
    CodexSurface,
    load_codex_subscription_settings,
)
from src.reasoning.react_config import Action, ActionType, Observation, ReActStep, Thought
from src.reasoning.test_proof import is_test_command, parse_test_outcome
from src.runtime.execution_ledger import _extract_proof, _extract_target
from src.utils.paths import ROOT_DIR


THREAD_START_METHOD = "thread/start"
TURN_START_METHOD = "turn/start"
TURN_STEER_METHOD = "turn/steer"
TURN_INTERRUPT_METHOD = "turn/interrupt"

_CONTROL_TOOLS = frozenset({"final_answer", "ask_user"})
_CODEX_RESPONSE_META: ContextVar[dict[str, Any] | None] = ContextVar(
    "lumena_codex_response_meta",
    default=None,
)
# LOT Z34 phase 1 — `website` manquait, et ça a coûté une preuve.
#
# Run du 21/08, « genere moi un site web mais en motion design » :
#   sélection contextuelle : 84 outils (files, web, agents, system, project…)
#     → la catégorie `website` n'est PAS retenue
#   expansion Codex        : 260 outils — web → {browser, files, documents}
#     → jamais `website`
#   résultat : generate_website ABSENT, serve_website ABSENT
#
# Codex a trouvé `generate_website` via `discover_tools` (qui indexe les 732),
# a cherché « un outil générique pour appeler un outil découvert par son nom »,
# n'a rien trouvé, et a écrit le site à la main. Surtout : sans `serve_website`
# il n'a pas pu servir la preview, donc pas de `browser_navigate`, donc pas de
# preuve — le truth-lock a collé « Navigateur NON vérifié » sur un livrable
# pourtant correct (32 ko, 3 fichiers liés).
#
# Ajout CIBLÉ, pas massif : déclarer les 597 outils coûterait 78 k tokens de
# contexte À CHAQUE TOUR (mesuré). On relie `website` là où il a un sens —
# faire du web, ou mener un projet — et le reste passe par `invoke_tool`
# (phase 2), qui supprime le mur sans gonfler la déclaration.
_TOOL_TRANSITIONS: Mapping[str, frozenset[str]] = {
    "browser": frozenset({"files", "documents"}),
    "files": frozenset({"system", "mail"}),
    "web": frozenset({"browser", "files", "documents", "website"}),
    "website": frozenset({"browser", "files", "web"}),
    "mail": frozenset({"files", "social"}),
    "system": frozenset({"files", "mail"}),
    "project": frozenset({"git", "files", "codebase", "website"}),
    "social": frozenset({"web", "files"}),
    "automation": frozenset({"web", "system", "mail"}),
}


class CodexReActUnavailable(RuntimeError):
    """Selected Codex Agent/Mission surface cannot execute; never API-fallback."""


@asynccontextmanager
async def _dedicated_codex_turn_scope():
    """Concurrency scope for Agent/Mission runs owning a private App Server.

    Chat keeps the shared notification lock.  Agent and Mission runs create a
    dedicated supervisor and queue below, so serializing them globally only
    destroys worker parallelism without protecting shared state.
    """

    yield


def reset_codex_response_meta() -> None:
    """Clear request-local Codex attribution before starting a Lumena call."""

    _CODEX_RESPONSE_META.set(None)


def consume_codex_response_meta() -> dict[str, Any]:
    """Return and clear request-local Codex attribution for the completed call."""

    meta = _CODEX_RESPONSE_META.get()
    _CODEX_RESPONSE_META.set(None)
    return dict(meta) if isinstance(meta, Mapping) else {}


def peek_codex_response_meta() -> dict[str, Any]:
    """Return request-local Codex attribution without consuming it.

    AgentService needs the authoritative provider before it persists memory and
    telemetry.  The web route remains the sole consumer so retry boundaries and
    response metadata keep their existing semantics.
    """

    meta = _CODEX_RESPONSE_META.get()
    return dict(meta) if isinstance(meta, Mapping) else {}


def _record_codex_response_meta(
    *, configured_model: str, selected_model: str
) -> None:
    requested = str(configured_model or selected_model or "auto")
    used = str(selected_model or "server-default")
    model_fallback = bool(configured_model and selected_model != configured_model)
    _CODEX_RESPONSE_META.set(
        {
            "provider_requested": "openai-codex",
            "provider_used": "openai-codex",
            "model_requested": requested,
            "model_used": used,
            "fallback_used": model_fallback,
            "fallback_reason": "codex_model_unavailable" if model_fallback else None,
            "continuation_used": False,
            "continuation_steps": 0,
            "finish_reason": "stop",
            "prompt_tokens": None,
            "completion_tokens": None,
        }
    )


def should_route_react_to_codex(
    *, is_mission_run: bool, settings: CodexSubscriptionSettings
) -> bool:
    surface = CodexSurface.MISSIONS if is_mission_run else CodexSurface.AGENT
    return settings.surface_requested(surface)


def _toml_literal(value: Any) -> str:
    """JSON scalar/list syntax is a valid, safely escaped TOML subset here."""

    return json.dumps(value, ensure_ascii=False)


def build_codex_tool_app_server_command(
    executable: str,
    *,
    python_executable: str,
    project_root: str | Path,
    tool_timeout_s: float,
    config_overrides: Mapping[str, Any] | None = None,
) -> tuple[str, ...]:
    """Build an ephemeral MCP configuration without writing Codex config files."""

    root = str(Path(project_root).resolve())
    settings = tuple((config_overrides or {}).items()) + (
        ("mcp_servers.lumena.command", python_executable),
        ("mcp_servers.lumena.args", ["-m", "src.llm.codex_mcp_bridge"]),
        ("mcp_servers.lumena.cwd", root),
        (
            "mcp_servers.lumena.env_vars",
            [
                "LUMENA_CODEX_BRIDGE_HOST",
                "LUMENA_CODEX_BRIDGE_PORT",
                "LUMENA_CODEX_BRIDGE_TOKEN",
            ],
        ),
        ("mcp_servers.lumena.required", True),
        ("mcp_servers.lumena.enabled", True),
        ("mcp_servers.lumena.default_tools_approval_mode", "approve"),
        ("mcp_servers.lumena.startup_timeout_sec", 15),
        ("mcp_servers.lumena.tool_timeout_sec", max(30, int(tool_timeout_s))),
    )
    command: list[str] = [str(executable)]
    for key, value in settings:
        command.extend(("--config", f"{key}={_toml_literal(value)}"))
    command.append("app-server")
    return tuple(command)


def _id_from_result(result: Any, key: str) -> str:
    if not isinstance(result, Mapping):
        return ""
    nested = result.get(key)
    if isinstance(nested, Mapping):
        return str(nested.get("id", "") or "")
    return str(result.get(f"{key}Id", "") or "")


def _event_matches(params: Any, *, thread_id: str, turn_id: str) -> bool:
    if not isinstance(params, Mapping):
        return False
    event_thread = str(params.get("threadId", "") or "")
    event_turn = str(params.get("turnId", "") or "")
    turn = params.get("turn")
    if isinstance(turn, Mapping):
        event_turn = event_turn or str(turn.get("id", "") or "")
    return (not event_thread or event_thread == thread_id) and (
        not event_turn or event_turn == turn_id
    )


def _select_model(models: Sequence[Any], configured: str) -> str:
    available = {str(item.model_id): item for item in models if item.model_id}
    if configured and configured in available:
        return configured
    default = next((item.model_id for item in models if item.is_default), "")
    return default or (next(iter(available)) if available else "")


def _visible_tool_names(react: Any) -> frozenset[str]:
    """Mirror ReAct's contextual filter and safe category transitions."""

    registry = react.tools
    schemas = registry.get_tools_schema()
    all_names = {
        str((schema.get("function") or {}).get("name", "") or "")
        for schema in schemas
        if isinstance(schema, Mapping)
    }
    configured = getattr(registry, "_allowed_tools", None)
    if configured is None:
        return frozenset(name for name in all_names if name and name not in _CONTROL_TOOLS)
    allowed = {str(name) for name in configured if str(name) in all_names}
    if not getattr(registry, "_allowed_tools_hard", False):
        categories = {
            str(getattr(registry, "_tool_modules", {}).get(name, "") or "")
            for name in allowed
        }
        expanded_categories = set(categories)
        for category in tuple(categories):
            expanded_categories.update(_TOOL_TRANSITIONS.get(category, ()))
        for name, category in getattr(registry, "_tool_modules", {}).items():
            if category in expanded_categories and name in all_names:
                allowed.add(name)
    return frozenset(name for name in allowed if name and name not in _CONTROL_TOOLS)


def _resolve_execution_root(react: Any) -> Path:
    registry = react.tools
    mission_workspace = str(react._mission_workspace_meta() or "").strip()
    if mission_workspace:
        base = Path(getattr(registry, "default_workspace_root", ROOT_DIR / "workspace"))
        candidate = (base / mission_workspace).resolve()
        if candidate.is_dir():
            return candidate
    runtime = getattr(react, "runtime_ctx", None)
    for value in (
        getattr(runtime, "resolved_workspace", None),
        getattr(runtime, "workspace_path", None),
        getattr(getattr(registry, "_v2_context", None), "runtime_root", None),
    ):
        if value:
            candidate = Path(value).resolve()
            if candidate.is_dir():
                return candidate
    return ROOT_DIR.resolve()


def _prepare_handler_context(react: Any) -> None:
    context = getattr(react.tools, "_v2_context", None)
    if context is None:
        return
    elapsed = max(0.0, asyncio.get_running_loop().time() - react._loop_start_time)
    context.budget_seconds = max(0.0, float(react.timeout_seconds or 600) - elapsed)
    context.runtime_task_id = react.task_id or None
    context.is_mission_run = bool(react._is_mission_run)
    context.mission_workspace = react._mission_workspace_meta()
    context.mission_allowed_files = react._mission_allowed_files_meta()
    context.original_user_query = str(getattr(react, "_original_query", "") or "")


def _cancel_requested(react: Any) -> bool:
    if not react.task_id or not react.task_orchestrator:
        return False
    try:
        return bool(react.task_orchestrator.is_cancel_requested(react.task_id))
    except Exception:
        return False


def _record_tool_observation(
    react: Any,
    name: str,
    arguments: dict[str, Any],
    observation: Observation,
    duration_s: float,
) -> None:
    """Project MCP tool calls into the same history and ledger as native ReAct."""

    iteration = max(
        int(getattr(react, "_current_iteration", 0) or 0),
        len(getattr(react, "history", ()) or ()),
    )
    react._current_iteration = iteration
    action = Action(ActionType.TOOL_CALL, tool_name=name, tool_args=arguments)
    step_callback = getattr(react, "step_callback", None)
    if callable(step_callback):
        try:
            step_callback(name, dict(arguments))
        except Exception as exc:
            logger.debug("[Agent/Codex] step callback ignore: {}", exc)
    react.history.append(
        ReActStep(
            thought=Thought("Codex a selectionne un outil Lumena expose pour ce run."),
            action=action,
            observation=observation,
        )
    )
    try:
        react._record_document_catalog_evidence(action, observation)
        react._record_document_workflow_evidence(action, observation)
    except Exception:
        pass
    meta: dict[str, Any] = {"duration_ms": round(duration_s * 1000, 1), "via": "codex_mcp"}
    if name in {"run_command", "run_shell", "exec_command"}:
        command = str(arguments.get("command", "") or "")
        meta["command"] = command[:200]
        if is_test_command(command):
            meta["test_outcome"] = parse_test_outcome(
                command,
                str(getattr(observation, "content", "") or ""),
                getattr(observation, "exit_code", None),
            )
    target = _extract_target(name, arguments)
    proof = _extract_proof(
        name,
        str(getattr(observation, "content", "") or ""),
        bool(getattr(observation, "success", False)),
    )
    react.execution_ledger.append(
        iteration=iteration,
        action=name,
        target=target,
        success=bool(getattr(observation, "success", False)),
        proof=proof,
        meta=meta,
    )
    for sub in getattr(observation, "sub_results", ()) or ():
        react.execution_ledger.append(
            iteration=iteration,
            action=sub.tool_name,
            target=_extract_target(sub.tool_name, sub.args),
            success=bool(sub.success),
            proof=_extract_proof(sub.tool_name, sub.content, sub.success),
            meta={"duration_ms": 0.0, "via": "codex_mcp_parallel"},
        )
    test_outcome = meta.get("test_outcome")
    if isinstance(test_outcome, dict) and react.task_id and react.task_orchestrator:
        try:
            react.task_orchestrator.set_task_metadata(
                react.task_id,
                last_test_outcome=dict(test_outcome),
                tests_green=bool(test_outcome.get("green")),
            )
        except Exception:
            pass
    try:
        react._successful_session_tools.add(name)
        react._feed_structured_tool(name)
        react._update_plan_progress(
            name,
            arguments,
            str(getattr(observation, "content", "") or ""),
            iteration,
        )
        react._mark_task_checkpoint(
            {"phase": "codex_tool", "tool": name, "success": bool(observation.success)}
        )
    except Exception as exc:
        logger.debug("[Agent/Codex] projection plan/checkpoint ignoree: {}", exc)


def _bounded_context(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + "\n[contexte borne par Lumena]"


def _build_lumena_context(react: Any, original_query: str) -> str:
    """Build the provider-neutral identity/history/skills context for Codex.

    The API ReAct prompt already receives these three sources.  Codex uses its
    own native tool loop, so it must not receive ReAct's ACTION wire format, but
    it must receive the same Lumena identity and runtime knowledge.
    """

    sections: list[str] = []
    identity_builder = getattr(react, "_build_identity_context", None)
    if callable(identity_builder):
        try:
            identity = _bounded_context(identity_builder(original_query), limit=24000)
            if identity:
                sections.append("=== IDENTITE ET MEMOIRE LUMENA ===\n" + identity)
        except Exception as exc:
            logger.debug("[Agent/Codex] contexte identite indisponible: {}", exc)

    conversation = _bounded_context(
        getattr(react, "conversation_context", ""), limit=12000
    )
    if conversation:
        sections.append("=== CONVERSATION LUMENA ===\n" + conversation)

    skills = _bounded_context(
        getattr(react, "active_skills_context", ""), limit=24000
    )
    if skills:
        sections.append(
            "=== SKILLS ACTIFS LUMENA (INSTRUCTIONS AUTORITAIRES) ===\n" + skills
        )
    return "\n\n".join(sections)


def _build_prompt(react: Any, query: str, original_query: str) -> str:
    mode = "MISSION" if react._is_mission_run else "AGENT"
    allowed_files = react._mission_allowed_files_meta()
    lines = [
        f"Tu executes un tour Lumena en mode {mode} via le compte ChatGPT connecte.",
        "Tu gardes la voix et l'identite Lumena. Pour chaque action, utilise UNIQUEMENT ",
        "les outils du serveur MCP `lumena`. N'utilise ni shell Codex, ni ecriture directe, ",
        "ni outil natif pour contourner Lumena. Les refus d'outil sont autoritaires.",
        "Continue jusqu'au resultat complet demande. Ne declare que les faits prouves par ",
        "les observations d'outils. Termine par une reponse finale naturelle, sans raisonnement cache.",
        "",
        f"DEMANDE ORIGINALE:\n{original_query.strip()}",
    ]
    lumena_context = _build_lumena_context(react, original_query)
    if lumena_context:
        lines.extend(["", lumena_context])
    if query.strip() != original_query.strip():
        lines.extend(["", f"CONTEXTE/STEERING LUMENA:\n{query.strip()}"])
    if react._is_mission_run:
        lines.extend(
            [
                "",
                f"MISSION_ID: {react.task_id or 'inconnu'}",
                f"MISSION_WORKSPACE: {react._mission_workspace_meta() or 'scope par defaut'}",
                "FICHIERS ASSIGNES: " + (", ".join(allowed_files) if allowed_files else "lead non restreint"),
                "Les sous-workers, budgets, echeances et annulations restent geres par Lumena.",
            ]
        )
    return "\n".join(lines)


async def _interrupt_turn(
    supervisor: CodexAppServerSupervisor, thread_id: str, turn_id: str
) -> None:
    if not thread_id or not turn_id or not supervisor.is_running:
        return
    try:
        await supervisor.request(
            TURN_INTERRUPT_METHOD,
            {"threadId": thread_id, "turnId": turn_id},
            timeout=10,
        )
    except Exception as exc:
        logger.warning("[Agent/Codex] interruption non confirmee: {}", exc)


async def _mission_deadline_action(
    react: Any,
    supervisor: CodexAppServerSupervisor,
    thread_id: str,
    turn_id: str,
    *,
    steered: bool,
) -> str:
    if not react._is_mission_run or not react.task_id or not react.task_orchestrator:
        return "none"
    try:
        from src.subagents.mission_budget import (
            deadline_hard_net_fires,
            mission_budget,
            mission_budget_finalize,
        )

        record = react.task_orchestrator.get_task(react.task_id) or {}
        metadata = record.get("metadata") or {}
        budget = mission_budget(record)
        remaining = budget.get("remaining_s")
        if not budget.get("has_deadline") or not isinstance(remaining, (int, float)):
            return "none"
        grace = max(
            0.0,
            float(os.getenv("LUMENA_MISSION_DEADLINE_GRACE_S", "120") or 120),
        )
        if remaining <= 0 and not steered:
            decision = mission_budget_finalize(budget, grace_s=grace)
            instruction = decision[1] if decision and decision[0] == "finalize" else ""
            if instruction:
                await supervisor.request(
                    TURN_STEER_METHOD,
                    {
                        "threadId": thread_id,
                        "expectedTurnId": turn_id,
                        "input": [{"type": "text", "text": instruction}],
                    },
                    timeout=15,
                )
                react.task_orchestrator.set_task_metadata(
                    react.task_id, deadline_steered=True
                )
                return "steered"
        completion_evidence: dict[str, Any] = {}
        completion_probe = getattr(react, "_mission_completion_evidence", None)
        if callable(completion_probe):
            try:
                completion_evidence = dict(completion_probe() or {})
            except Exception as exc:
                logger.debug(
                    "[Agent/Codex] preuve de completion mission indisponible: {}", exc
                )
        completion_proven = bool(completion_evidence.get("complete"))
        artifact_written = bool(metadata.get("deadline_artifact_written"))
        if deadline_hard_net_fires(
            steered=bool(steered or metadata.get("deadline_steered")),
            remaining_s=remaining,
            grace_s=grace,
            artifact_written=artifact_written,
            completion_proven=completion_proven,
        ):
            react.task_orchestrator.set_task_metadata(
                react.task_id,
                deadline_expired=True,
                terminal_reason_code="deadline_expired",
                completion_proof=completion_evidence,
            )
            react.task_orchestrator.cancel_task(react.task_id, propagate=True)
            return "cancel"
        if (
            (artifact_written or completion_proven)
            and remaining <= -grace
            and not metadata.get("deadline_net_disarmed")
        ):
            react.task_orchestrator.set_task_metadata(
                react.task_id,
                deadline_net_disarmed=True,
                completion_proof=completion_evidence,
            )
    except Exception as exc:
        logger.debug("[Agent/Codex] budget mission non evalue: {}", exc)
    return "none"


async def run_react_with_codex_subscription(
    react: Any,
    query: str,
    original_query: str,
    *,
    settings: CodexSubscriptionSettings,
    timeout_s: float | None = None,
) -> str:
    shared = get_shared_codex_app_server()
    if shared is None or not shared.is_running:
        # LOT Z33 phases 1 & 2 — avant d'abandonner, on tente de ROUVRIR. La
        # session est un processus local : elle meurt au redemarrage de Lumena
        # (21/08 02:33:57) ou si le processus tombe en cours de run. L'auth,
        # elle, survit sur disque — il n'y a donc qu'un processus a relancer.
        try:
            from src.llm.codex_app_server import ensure_shared_codex_app_server
            shared = await ensure_shared_codex_app_server()
        except Exception as _cx_exc:
            logger.debug("[Z33] reouverture session Codex impossible: {}", _cx_exc)
            shared = None
        if shared is None or not shared.is_running:
            raise CodexReActUnavailable(
                "Aucune session Codex connectee (reouverture automatique tentee, "
                "sans succes). Ouvre Configuration > Acces OpenAI."
            )
        logger.info("[Z33] session Codex rouverte a chaud — le run continue")
    executable = str(shared.config.command[0]) if shared.config.command else ""
    if not executable:
        raise CodexReActUnavailable("Executable Codex introuvable dans la session connectee")
    allowed_tools = _visible_tool_names(react)
    if not allowed_tools:
        raise CodexReActUnavailable("Aucun outil Lumena autorise pour ce run")
    _prepare_handler_context(react)
    workspace = _resolve_execution_root(react)
    bounded_timeout = max(30.0, float(timeout_s or react.timeout_seconds or 600))
    agent_id = "codex-mission" if react._is_mission_run else "codex-agent"
    bridge = LumenaCodexToolBridge(
        react.tools,
        allowed_tools=allowed_tools,
        agent_id=agent_id,
        before_call=lambda: _prepare_handler_context(react),
        after_call=lambda name, args, obs, duration: _record_tool_observation(
            react, name, args, obs, duration
        ),
        cancel_requested=lambda: _cancel_requested(react),
    )
    thread_id = ""
    turn_id = ""
    final_text = ""
    supervisor: CodexAppServerSupervisor | None = None
    deadline_steered = False
    async with _dedicated_codex_turn_scope():
        async with bridge:
            endpoint = bridge.endpoint
            environment = dict(shared.config.environ or os.environ)
            environment.update(
                {
                    "LUMENA_CODEX_BRIDGE_HOST": endpoint.host,
                    "LUMENA_CODEX_BRIDGE_PORT": str(endpoint.port),
                    "LUMENA_CODEX_BRIDGE_TOKEN": endpoint.token,
                }
            )
            command = build_codex_tool_app_server_command(
                executable,
                python_executable=sys.executable,
                project_root=ROOT_DIR,
                tool_timeout_s=bounded_timeout,
                config_overrides=codex_compatibility_config_overrides(environment),
            )
            supervisor = CodexAppServerSupervisor(
                CodexAppServerConfig(
                    command=command,
                    cwd=str(ROOT_DIR),
                    environ=environment,
                    request_timeout_s=30,
                    handshake_timeout_s=20,
                    max_auto_restarts=1,
                )
            )
            try:
                await supervisor.start()
                gateway = CodexSubscriptionGateway(supervisor)
                await gateway.require_chatgpt_account()
                models = await gateway.list_models()
                if not models:
                    raise CodexReActUnavailable("Le compte Codex ne retourne aucun modele")
                model = _select_model(models, settings.default_model)
                service_name = "lumena-mission" if react._is_mission_run else "lumena-agent"
                thread_params: dict[str, Any] = {
                    "cwd": str(workspace),
                    "approvalPolicy": "never",
                    "sandbox": "read-only",
                    "serviceName": service_name,
                }
                if model:
                    thread_params["model"] = model
                started = await supervisor.request(
                    THREAD_START_METHOD, thread_params, timeout=30
                )
                thread_id = _id_from_result(started, "thread")
                if not thread_id:
                    raise CodexReActUnavailable("Codex n'a retourne aucun thread Agent")
                prompt = _build_prompt(react, query, original_query)
                turn_params: dict[str, Any] = {
                    "threadId": thread_id,
                    "input": [{"type": "text", "text": prompt}],
                    "cwd": str(workspace),
                    "approvalPolicy": "never",
                    "sandboxPolicy": {
                        "type": "readOnly",
                        "networkAccess": False,
                    },
                }
                if model:
                    turn_params["model"] = model
                result = await supervisor.request(TURN_START_METHOD, turn_params, timeout=30)
                turn_id = _id_from_result(result, "turn")
                if not turn_id:
                    raise CodexReActUnavailable("Codex n'a retourne aucun tour Agent")
                async with asyncio.timeout(bounded_timeout):
                    while True:
                        if _cancel_requested(react):
                            raise asyncio.CancelledError
                        deadline_action = await _mission_deadline_action(
                            react,
                            supervisor,
                            thread_id,
                            turn_id,
                            steered=deadline_steered,
                        )
                        deadline_steered = deadline_steered or deadline_action == "steered"
                        if deadline_action == "cancel":
                            raise asyncio.CancelledError
                        try:
                            notification = await supervisor.next_notification(timeout=1.0)
                        except CodexAppServerTimeout:
                            continue
                        if not _event_matches(
                            notification.params, thread_id=thread_id, turn_id=turn_id
                        ):
                            continue
                        params = notification.params if isinstance(notification.params, Mapping) else {}
                        if notification.method == "item/completed":
                            item = params.get("item")
                            if isinstance(item, Mapping) and item.get("type") == "agentMessage":
                                final_text = str(item.get("text", "") or final_text)
                        elif notification.method == "turn/completed":
                            turn = params.get("turn")
                            turn = turn if isinstance(turn, Mapping) else {}
                            status = str(turn.get("status", "") or "")
                            if status != "completed":
                                raise CodexReActUnavailable(
                                    f"Tour Codex termine avec status={status}: {turn.get('error')}"
                                )
                            if not final_text.strip():
                                raise CodexReActUnavailable("Codex a termine sans reponse finale")
                            logger.info(
                                "[Agent/Codex] tour termine surface={} model={} tools={} task={}",
                                service_name,
                                model or "server-default",
                                react.execution_ledger.size,
                                react.task_id,
                            )
                            _record_codex_response_meta(
                                configured_model=settings.default_model,
                                selected_model=model,
                            )
                            return final_text.strip()
            except (asyncio.CancelledError, TimeoutError, CodexAppServerTimeout):
                if supervisor is not None:
                    await _interrupt_turn(supervisor, thread_id, turn_id)
                raise
            except CodexAppServerError as exc:
                raise CodexReActUnavailable(f"Codex App Server indisponible: {exc}") from exc
            finally:
                if supervisor is not None:
                    await supervisor.stop()


async def maybe_run_codex_surface(
    react: Any,
    query: str,
    original_query: str,
    *,
    settings: CodexSubscriptionSettings | None = None,
) -> str | None:
    """Return None for the historical ReAct path, or a truth-locked Codex final."""

    resolved = settings or load_codex_subscription_settings()
    if not should_route_react_to_codex(
        is_mission_run=bool(react._is_mission_run), settings=resolved
    ):
        return None
    result = await run_react_with_codex_subscription(
        react,
        query,
        original_query,
        settings=resolved,
    )
    previous = bool(getattr(react, "_codex_tool_bridge_run", False))
    react._codex_tool_bridge_run = True
    try:
        return react._stream_and_return_final(result)
    finally:
        react._codex_tool_bridge_run = previous
