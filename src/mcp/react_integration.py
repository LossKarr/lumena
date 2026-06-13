"""Phase 26 - ReAct integration for the autonomous MCP loop.

This module exposes the pure Phase 22-25 MCP planning chain as native
Lumena tools. It deliberately does not register MCP dynamic handlers and does
not create routes. In live mode, it may execute an approved action only after
ApprovalQueue.approve_if() and AutoApproveEngine.evaluate() both accept it.
"""

from __future__ import annotations

import contextvars
import json
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from src.mcp.autonomous_orchestrator import (
    AutonomousMCPLoopDeps,
    AutonomousMCPLoopPlanner,
)
from src.mcp.capability_resolver import (
    CapabilityResolver,
    CapabilityResolverDeps,
    FilesystemDiscoveryReader,
)
from src.mcp.execution_bridge import MCPExecutionBridge, MCPExecutionBridgeDeps
from src.mcp.proposal_planner import MCPProposalPlanner, MCPProposalPlannerDeps
from src.mcp.proposal_planner import (
    CuratedOfflineCatalogSource,
    LocalFilesystemSource,
)
from src.mcp.network_sources import (
    MCPDirectorySearchSource,
    NpmRegistrySearchSource,
    PyPIProjectLookupSource,
)


CAPABILITY_TOOL_NAME = "request_mcp_capability"
TICKET_TOOL_NAME = "request_mcp_ticket"
RUN_AUTONOMY_TOOL_NAME = "run_mcp_autonomy"
RESUME_TASK_TOOL_NAME = "resume_mcp_task"
MCP_LOOP_CATEGORY = "mcp"  # Phase D : contrat unifie (etait "mcp_loop_integration")
TICKET_CONFIRMATION_PHRASE = "I-CONFIRM-MCP-TICKET"
AUTONOMY_CONFIRMATION_PHRASE = "I-CONFIRM-MCP-AUTONOMY"

# ── Phase F : 5 outils LLM user-facing ──────────────────────────────────────
ADD_MCP_TOOL_NAME = "add_mcp"
DISABLE_MCP_TOOL_NAME = "disable_mcp"
REMOVE_MCP_TOOL_NAME = "remove_mcp"
SET_MCP_PREFERENCE_TOOL_NAME = "set_mcp_preference"
SET_MCP_CATEGORY_TOOL_NAME = "set_mcp_category"

ADD_MCP_CONFIRMATION_PHRASE = "I-CONFIRM-ADD-MCP"
DISABLE_MCP_CONFIRMATION_PHRASE = "I-CONFIRM-DISABLE-MCP"
REMOVE_MCP_CONFIRMATION_PHRASE = "I-CONFIRM-REMOVE-MCP"
SET_MCP_PREFERENCE_CONFIRMATION_PHRASE = "I-CONFIRM-MCP-PREFERENCE"
SET_MCP_CATEGORY_CONFIRMATION_PHRASE = "I-CONFIRM-MCP-CATEGORY"

_INTENT_MIN = 10
_INTENT_MAX = 512
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,127}$")
_TOOL_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_.-]{0,127}$")
_UUID4_HEX_RE = re.compile(r"^[0-9a-f]{32}$")
_AUTO_LOOP_MAX_STEPS = 5
_AGENT_AUTOAPPROVE_ENV = "LUMENA_MCP_AGENT_AUTOAPPROVE_LIVE"

_PHASE26_CALLER_CONTEXT: contextvars.ContextVar[Optional[str]] = (
    contextvars.ContextVar("_PHASE26_CALLER_CONTEXT", default=None)
)

_CAPABILITY_CALLERS = frozenset(
    {
        "react",
        "research_agent",
        "file_agent",
        "browser_agent",
        "planner_agent",
        "general_agent",
        "sub_agent",
    }
)
_TICKET_CALLERS = frozenset({"react"})
_CODE_AGENT_CALLERS = frozenset({"code_agent", "debug_agent", "refactor_agent"})

_RECOMMENDATION_CODES = frozenset(
    {
        "use_existing",
        "needs_install_approval",
        "needs_activation_approval",
        "needs_catalog_approval",
        "needs_local_creation",
        "waiting_approval",
        "ticket_proposed",
        "ticket_would_be_proposed",
        "ticket_descriptive_only",
        "autonomy_would_run",
        "autonomy_ticket_created",
        "autonomy_ready_to_use",
        "auto_approve_not_matched",
        "auto_approve_unavailable",
        "auto_executed",
        "auto_execute_failed",
        "auto_loop_exhausted",
        "resume_ready_to_use",
        "already_applied",
        "no_safe_path",
        "blocked",
        "live_requirements_not_met",
        "code_agent_out_of_scope",
        "caller_kind_not_allowed",
        "confirmation_phrase_invalid",
        "phase24_unavailable",
        "phase25_unavailable",
        "auto_approve_unavailable",
        "auto_execute_failed",
        # ── Phase F ──────────────────────────────────────────────
        "mcp_target_resolved",
        "mcp_added",
        "mcp_disabled",
        "mcp_removed",
        "mcp_preference_set",
        "mcp_category_set",
        "mcp_target_unresolved",
        "mcp_server_unknown",
        "mcp_category_unknown",
        "mcp_server_id_invalid",
        "mcp_target_invalid",
        "mcp_service_unavailable",
        "mcp_action_failed",
        # Phase I-8 (Fix AB) : package npm/PyPI inexistant sur le registry.
        "mcp_package_not_found",
        # Phase I-8 (Fix AS) : repo GitHub sans package npm/PyPI détectable.
        "mcp_github_no_package",
    }
)

_BLOCKER_CODES = frozenset(
    {
        "caller_kind_not_allowed",
        "code_agent_out_of_scope",
        "confirmation_phrase_invalid",
        "live_requirements_not_met",
        "phase24_unavailable",
        "phase25_unavailable",
        "intent_too_short_or_too_long",
        "intent_invalid_format",
        "snapshot_invalid_shape",
        # ── Phase F ──────────────────────────────────────────────
        "mcp_server_unknown",
        "mcp_category_unknown",
        "mcp_server_id_invalid",
        "mcp_target_invalid",
        "mcp_service_unavailable",
        "mcp_action_failed",
        # Phase I-8 (Fix AB)
        "mcp_package_not_found",
        # Phase I-8 (Fix AS)
        "mcp_github_no_package",
    }
)

_SNAPSHOT_WHITELIST = frozenset(
    {
        "phase24_decision",
        "phase24_action_kind",
        "phase25_decision",
        "target_server_id",
        "target_tool_name",
        "recommendation_code",
        "actionable_intent",
        "requires_admin_nod",
        "risk_summary",
        "proposed_ticket_action_id",
        "live_mode_enabled",
        "dry_run",
    }
)

_PHASE26_CAPABILITY_DESC = (
    "Verifier si une capacite MCP est disponible localement ou doit etre "
    "recherchee. Lecture pure, ne declenche aucune installation."
)
_PHASE26_TICKET_DESC = (
    "Demander la creation d'un ticket MCP pour une capacite absente. "
    "Cree seulement un ticket pending via la chaine MCP; l'approbation "
    "humaine reste obligatoire dans le panel admin."
)
_PHASE26_RUN_DESC = (
    "Piloter la boucle MCP autonome pour une intention utilisateur: verifier "
    "les capacites, proposer ou creer le ticket necessaire, et indiquer "
    "l'outil pret a utiliser si disponible."
)
_PHASE26_RESUME_DESC = (
    "Reprendre une tache utilisateur apres approbation/install/activation MCP "
    "en verifiant a nouveau si l'outil MCP est maintenant disponible."
)
_PHASE26_CAPABILITY_PARAMS: Dict[str, Dict[str, str]] = {
    "intent": {
        "type": "string",
        "description": "Description courte de la capacite MCP recherchee.",
    },
}
_PHASE26_TICKET_PARAMS: Dict[str, Dict[str, str]] = {
    "intent": {
        "type": "string",
        "description": "Description courte de la capacite MCP demandee.",
    },
    "confirmation_phrase": {
        "type": "string",
        "description": "Phrase exacte requise: I-CONFIRM-MCP-TICKET.",
    },
    "live": {
        "type": "boolean",
        "description": "False = dry-run. True = creer un ticket pending si LUMENA_MCP_LIVE=1.",
    },
}
_PHASE26_RUN_PARAMS: Dict[str, Dict[str, str]] = {
    "intent": {
        "type": "string",
        "description": "Tache utilisateur ou capacite MCP a resoudre.",
    },
    "live": {
        "type": "boolean",
        "description": "False = dry-run. True = creer le ticket si LUMENA_MCP_LIVE=1.",
    },
    "confirmation_phrase": {
        "type": "string",
        "description": "Phrase exacte requise si live=True: I-CONFIRM-MCP-AUTONOMY.",
    },
}
_PHASE26_RESUME_PARAMS: Dict[str, Dict[str, str]] = {
    "intent": {
        "type": "string",
        "description": "Tache utilisateur initiale a reprendre apres changement MCP.",
    },
}
_PHASE26_CAPABILITY_REQUIRED = ("intent",)
_PHASE26_TICKET_REQUIRED = ("intent", "confirmation_phrase")
_PHASE26_RUN_REQUIRED = ("intent",)
_PHASE26_RESUME_REQUIRED = ("intent",)


# ── Phase F : descriptions + params + required ──────────────────────────────
_PHASE_F_ADD_DESC = (
    "Resoudre n'importe quelle cible MCP (URL GitHub, package npm/pypi, "
    "snippet de config, chemin local, ou intention libre). En dry-run "
    "(live=False), retourne le ResolvedTarget pour discussion. En live=True, "
    "necessite une confirmation_phrase explicite."
)
_PHASE_F_DISABLE_DESC = (
    "Desactiver un serveur MCP ACTIVE (sans le supprimer du catalog). "
    "Necessite confirmation_phrase exacte."
)
_PHASE_F_REMOVE_DESC = (
    "Supprimer (soft-delete) un serveur MCP du catalog. Necessite "
    "confirmation_phrase exacte. Irreversible (status REMOVED terminal)."
)
_PHASE_F_PREF_DESC = (
    "Toggle la preference cohabitation natifs/MCP : prefer_over_native=True "
    "donne la priorite au MCP quand un natif equivalent existe."
)
_PHASE_F_CATEGORY_DESC = (
    "Recategoriser un MCP en utilisant le langage humain de l'utilisateur "
    "(ex: 'messagerie' -> 'mail', 'boulot' -> 'project'). La traduction est "
    "deterministe via HUMAN_TO_CATEGORY."
)

_PHASE_F_ADD_PARAMS: Dict[str, Dict[str, str]] = {
    "target": {
        "type": "string",
        "description": (
            "Cible brute : URL GitHub, npm:/pypi:/local: package_spec, "
            "snippet JSON claude_desktop, chemin local, ou intention libre."
        ),
    },
    "live": {
        "type": "boolean",
        "description": "False = dry-run resolution. True = install effectif.",
    },
    "confirmation_phrase": {
        "type": "string",
        "description": "Requis si live=True : I-CONFIRM-ADD-MCP.",
    },
}
_PHASE_F_DISABLE_PARAMS: Dict[str, Dict[str, str]] = {
    "server_id": {
        "type": "string",
        "description": "ID du serveur MCP a desactiver (slug catalog).",
    },
    "confirmation_phrase": {
        "type": "string",
        "description": "Phrase exacte requise : I-CONFIRM-DISABLE-MCP.",
    },
}
_PHASE_F_REMOVE_PARAMS: Dict[str, Dict[str, str]] = {
    "server_id": {
        "type": "string",
        "description": "ID du serveur MCP a supprimer du catalog.",
    },
    "confirmation_phrase": {
        "type": "string",
        "description": "Phrase exacte requise : I-CONFIRM-REMOVE-MCP.",
    },
}
_PHASE_F_PREF_PARAMS: Dict[str, Dict[str, str]] = {
    "server_id": {
        "type": "string",
        "description": "ID du serveur MCP cible.",
    },
    "prefer_over_native": {
        "type": "boolean",
        "description": "True = MCP prioritaire, False = natif prioritaire.",
    },
    "confirmation_phrase": {
        "type": "string",
        "description": "Phrase exacte requise : I-CONFIRM-MCP-PREFERENCE.",
    },
}
_PHASE_F_CATEGORY_PARAMS: Dict[str, Dict[str, str]] = {
    "server_id": {
        "type": "string",
        "description": "ID du serveur MCP a recategoriser.",
    },
    "human_phrase": {
        "type": "string",
        "description": (
            "Mot ou phrase de l'utilisateur dans son propre langage "
            "(ex: 'messagerie', 'boulot', 'fichiers')."
        ),
    },
    "confirmation_phrase": {
        "type": "string",
        "description": "Phrase exacte requise : I-CONFIRM-MCP-CATEGORY.",
    },
}

_PHASE_F_ADD_REQUIRED = ("target",)
_PHASE_F_DISABLE_REQUIRED = ("server_id", "confirmation_phrase")
_PHASE_F_REMOVE_REQUIRED = ("server_id", "confirmation_phrase")
_PHASE_F_PREF_REQUIRED = ("server_id", "prefer_over_native", "confirmation_phrase")
_PHASE_F_CATEGORY_REQUIRED = ("server_id", "human_phrase", "confirmation_phrase")


class Phase26RegistrationError(RuntimeError):
    """Raised for fail-safe native registration failures."""


@dataclass(frozen=True)
class MCPReActIntegrationDeps:
    catalog: Optional[Any] = None
    approval_queue: Optional[Any] = None
    catalog_add_orchestrator: Optional[Any] = None
    install_orchestrator: Optional[Any] = None
    activation_service: Optional[Any] = None
    local_creation_orchestrator: Optional[Any] = None
    local_creation_executor: Optional[Any] = None
    auto_approve_engine: Optional[Any] = None
    runtime_watcher: Optional[Any] = None
    policy_resolver: Optional[Any] = None
    policy_attributor: Optional[Any] = None
    discovery_reports_dir: Optional[Path] = None


@dataclass(frozen=True)
class Phase26HandlerOutput:
    handler_name: str
    decision: str
    payload: Dict[str, Any]
    blockers: Tuple[str, ...]
    created_at: str


@dataclass(frozen=True)
class _Phase26Chain:
    tool_registry: Any
    autonomous_planner: AutonomousMCPLoopPlanner
    execution_bridge: MCPExecutionBridge


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_output(output: Phase26HandlerOutput) -> str:
    return json.dumps(
        {
            "handler_name": output.handler_name,
            "decision": output.decision,
            "payload": output.payload,
            "blockers": list(output.blockers),
            "created_at": output.created_at,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def _sanitize_intent(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    text = unicodedata.normalize("NFC", raw)
    if _CONTROL_RE.search(text):
        return None
    text = " ".join(text.strip().split())
    if len(text) < _INTENT_MIN or len(text) > _INTENT_MAX:
        return None
    return text


def _sanitize_caller_kind(raw: Any) -> str:
    if not isinstance(raw, str):
        return "unknown"
    value = raw.strip().lower().replace("-", "_")
    aliases = {
        "code": "code_agent",
        "debug": "debug_agent",
        "refactor": "refactor_agent",
        "research": "research_agent",
        "file": "file_agent",
        "browser": "browser_agent",
        "planner": "planner_agent",
        "general": "general_agent",
    }
    return aliases.get(value, value or "unknown")


def _resolve_caller_kind(explicit: Optional[str]) -> str:
    if explicit is not None:
        return _sanitize_caller_kind(explicit)
    return _sanitize_caller_kind(_PHASE26_CALLER_CONTEXT.get())


_CURATED_INSTALL_PREFIXES: Tuple[str, ...] = (
    "mcp_catalog_add:",
    "mcp_install:",
    "mcp_activate:",
)


def _is_curated_install_ticket(
    expected_tool: Optional[str],
    server_id: Optional[str],
    payload: Dict[str, Any],
) -> bool:
    # Phase I-7 : reconnait les tickets du pipeline d'install pour MCPs curated.
    # Permet l'auto-approve bypass dans _try_auto_approve_and_execute, l'engine
    # AutoApprove standard ne pouvant pas matcher le format `mcp_xxx:<sid>`.
    if not isinstance(expected_tool, str) or not isinstance(server_id, str):
        return False
    if not any(expected_tool.startswith(p) for p in _CURATED_INSTALL_PREFIXES):
        return False
    try:
        from src.mcp.known_mcps import get_known_mcp  # noqa: WPS433
    except Exception:  # noqa: BLE001
        return False
    curated = get_known_mcp(server_id)
    if curated is None:
        return False
    # Pour catalog_add / install on cross-vérifie le package_spec si l'orchestrator
    # l'a exposé dans le payload (anti-substitution de package).
    if expected_tool.startswith(("mcp_catalog_add:", "mcp_install:")):
        ticket_pkg = payload.get("package_spec") if isinstance(payload, dict) else None
        if ticket_pkg and ticket_pkg != curated.package_spec:
            return False
    return True


def _is_cataloged_install_ticket(
    expected_tool: Optional[str],
    server_id: Optional[str],
    payload: Dict[str, Any],
    catalog: Any,
) -> bool:
    """Phase I-8 (Fix AA.1) : bypass auto-approve pour install/activate
    d'entrées DÉJÀ au catalogue (non-curated).

    Frontière de confiance : le catalog_add d'un package non-curated exige
    l'approbation humaine dans le panel — c'est le SEUL gate humain. Une
    fois l'entrée au catalogue (declared/installed), install et activate
    sont des suites mécaniques de cette décision : re-demander un nod
    humain par étape produisait la boucle de tickets observée runtime
    (2026-06-11 00:13→00:18, 3 tickets pour zéro install).

    JAMAIS pour mcp_catalog_add: (le gate humain) ni local_create.
    Cross-check anti-substitution : package_spec du payload vs catalogue.
    """
    if not isinstance(expected_tool, str) or not isinstance(server_id, str):
        return False
    if not expected_tool.startswith(("mcp_install:", "mcp_activate:")):
        return False
    if catalog is None:
        return False
    try:
        entry = catalog.get_server(server_id)
    except Exception:  # noqa: BLE001
        return False
    if entry is None:
        return False
    status = getattr(entry, "status", None)
    status_value = getattr(status, "value", status)
    if status_value not in ("declared", "installed"):
        return False
    ticket_pkg = payload.get("package_spec") if isinstance(payload, dict) else None
    entry_pkg = getattr(entry, "package_spec", None)
    if ticket_pkg and isinstance(entry_pkg, str) and ticket_pkg != entry_pkg:
        return False
    return True


def _is_live_enabled() -> bool:
    return os.getenv("LUMENA_MCP_LIVE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _is_agent_autoapprove_enabled() -> bool:
    return os.getenv(_AGENT_AUTOAPPROVE_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _is_network_search_enabled() -> bool:
    return os.getenv("LUMENA_MCP_NETWORK_SEARCH_ENABLED", "1").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _enum_value(value: Any) -> str:
    raw = getattr(value, "value", value)
    return raw if isinstance(raw, str) else ""


def _valid_server_id(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not _SERVER_ID_RE.fullmatch(value):
        return None
    if ".." in value or "/" in value or "\\" in value:
        return None
    return value


def _valid_tool_name(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    if not _TOOL_NAME_RE.fullmatch(value):
        return None
    if _CONTROL_RE.search(value):
        return None
    return value


def _valid_uuid4_hex(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not _UUID4_HEX_RE.fullmatch(raw):
        return None
    try:
        parsed = uuid.UUID(raw)
    except (TypeError, ValueError):
        return None
    if parsed.version != 4 or parsed.hex != raw:
        return None
    return raw


def _safe_bool(value: Any) -> bool:
    return bool(value) if isinstance(value, bool) else False


def _safe_scalar(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, tuple):
        safe_items = tuple(_safe_scalar(item) for item in value)
        return safe_items
    return None


def make_phase26_snapshot(payload: Dict[str, Any]) -> Tuple[Tuple[str, Any], ...]:
    snapshot = []
    for key in sorted(_SNAPSHOT_WHITELIST):
        if key not in payload:
            continue
        value = _safe_scalar(payload.get(key))
        if value is not None or payload.get(key) is None:
            snapshot.append((key, value))
    return tuple(snapshot)


def phase26_snapshot_as_dict(
    snapshot: Optional[Tuple[Tuple[str, Any], ...]]
) -> Dict[str, Any]:
    if snapshot is None:
        return {}
    if not isinstance(snapshot, tuple):
        raise TypeError("snapshot must be a tuple-of-tuples or None")
    out: Dict[str, Any] = {}
    for item in snapshot:
        if (
            not isinstance(item, tuple)
            or len(item) != 2
            or not isinstance(item[0], str)
            or item[0] not in _SNAPSHOT_WHITELIST
        ):
            raise TypeError("invalid snapshot shape")
        out[item[0]] = _safe_scalar(item[1])
    return out


def _blocked(
    handler_name: str,
    blocker: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
) -> str:
    blocker_clean = blocker if blocker in _BLOCKER_CODES else "caller_kind_not_allowed"
    base = {
        "recommendation_code": (
            blocker_clean if blocker_clean in _RECOMMENDATION_CODES else "blocked"
        )
    }
    if payload:
        base.update(payload)
    return _json_output(
        Phase26HandlerOutput(
            handler_name=handler_name,
            decision="blocked",
            payload=base,
            blockers=(blocker_clean,),
            created_at=_now_utc_iso(),
        )
    )


def _ok(handler_name: str, payload: Dict[str, Any]) -> str:
    return _json_output(
        Phase26HandlerOutput(
            handler_name=handler_name,
            decision="ok",
            payload=payload,
            blockers=(),
            created_at=_now_utc_iso(),
        )
    )


def _map_phase24_recommendation(decision: str) -> str:
    return {
        "ready_to_use_existing_capability": "use_existing",
        "needs_install_approval": "needs_install_approval",
        "needs_activation_approval": "needs_activation_approval",
        "needs_catalog_approval": "needs_catalog_approval",
        "needs_local_creation": "needs_local_creation",
        "waiting_approval": "waiting_approval",
        "blocked": "blocked",
        "no_safe_path": "no_safe_path",
    }.get(decision, "blocked")


def _map_phase25_recommendation(decision: str, dry_run: bool) -> str:
    if decision == "ticket_proposed":
        return "ticket_proposed"
    if decision == "ticket_would_be_proposed":
        return "ticket_would_be_proposed"
    return {
        "no_ticket_needed": "already_applied",
        "already_applied": "already_applied",
        "waiting_approval": "waiting_approval",
        "ticket_descriptive_only": "ticket_descriptive_only",
        "execution_would_happen": "execution_would_happen",
        "executed_success_catalog_add": "executed_success",
        "executed_success_install": "executed_success",
        "executed_success_activate": "executed_success",
        "executed_failure": "executed_failure",
        "blocked": "blocked",
    }.get(decision, "blocked")


def _extract_phase24_payload(plan: Any, *, dry_run: bool = True) -> Dict[str, Any]:
    action = getattr(plan, "action", None)
    evidence = getattr(plan, "evidence", {}) or {}
    decision = _enum_value(getattr(plan, "decision", None))
    action_kind = _enum_value(getattr(action, "kind", None))
    target_server_id = _valid_server_id(
        getattr(action, "target_server_id", None)
        or getattr(action, "proposed_target_server_id", None)
    )
    target_tool_name = _valid_tool_name(getattr(action, "target_tool_name", None))
    risk_summary = getattr(action, "proposed_risk_summary", None)
    if not isinstance(risk_summary, str):
        risk_summary = "none"
    actionable = evidence.get("actionable_intent")
    payload = {
        "mapped_decision": decision,
        "action_kind": action_kind,
        "target_server_id": target_server_id,
        "target_tool_name": target_tool_name,
        "proposed_ticket_action_id": None,
        "dry_run": dry_run,
        "live_mode_enabled": _is_live_enabled(),
        "recommendation_code": _map_phase24_recommendation(decision),
        "actionable_intent": actionable if isinstance(actionable, bool) else None,
        "requires_admin_nod": bool(getattr(action, "requires_admin_nod", False)),
        "risk_summary": risk_summary,
    }
    return {k: v for k, v in payload.items() if v is not None}


def _extract_phase25_payload(plan: Any, *, dry_run: bool) -> Dict[str, Any]:
    action = getattr(plan, "action", None)
    evidence = getattr(plan, "evidence", {}) or {}
    decision = _enum_value(getattr(plan, "decision", None))
    ticket_id = _valid_uuid4_hex(getattr(action, "proposed_ticket_action_id", None))
    target_server_id = _valid_server_id(getattr(action, "target_server_id", None))
    risk_summary = getattr(action, "risk_summary", None)
    if not isinstance(risk_summary, str):
        risk_summary = "none"
    payload = {
        "mapped_decision": decision,
        "action_kind": _enum_value(getattr(action, "kind", None)),
        "target_server_id": target_server_id,
        "target_tool_name": None,
        "proposed_ticket_action_id": ticket_id,
        "dry_run": bool(getattr(plan, "dry_run", dry_run)),
        "live_mode_enabled": _safe_bool(evidence.get("live_mode_enabled")),
        "recommendation_code": _map_phase25_recommendation(decision, dry_run),
        "actionable_intent": None,
        "requires_admin_nod": decision not in {"no_ticket_needed", "already_applied"},
        "risk_summary": risk_summary,
    }
    return {k: v for k, v in payload.items() if v is not None}


def _is_local_creation_descriptive_payload(payload: Dict[str, Any]) -> bool:
    return (
        payload.get("recommendation_code") == "ticket_descriptive_only"
        and payload.get("risk_summary") == "local_creation_required"
    )


def _expected_ticket_tool_name(payload: Dict[str, Any]) -> Optional[str]:
    server_id = _valid_server_id(payload.get("target_server_id"))
    if server_id is None:
        return None
    risk = payload.get("risk_summary")
    action_kind = payload.get("action_kind")
    if risk == "install_required" or action_kind == "propose_install":
        return "mcp_install:" + server_id
    if risk == "activation_required" or action_kind == "propose_activation":
        return "mcp_activate:" + server_id
    if risk == "catalog_add_required" or action_kind == "propose_catalog_add":
        return "mcp_catalog_add:" + server_id
    if risk == "local_creation_required" or action_kind == "local_create":
        return "mcp_local_create:" + server_id
    return None


def _local_creation_ticket_payload(
    payload: Dict[str, Any],
    proposal: Any,
    *,
    dry_run: bool,
) -> Dict[str, Any]:
    ticket_id = _valid_uuid4_hex(getattr(proposal, "approval_ticket_id", None))
    server_id = _valid_server_id(getattr(proposal, "suggested_server_id", None))
    out = dict(payload)
    out.update(
        {
            "mapped_decision": (
                "ticket_would_be_proposed" if dry_run else "ticket_proposed"
            ),
            "recommendation_code": (
                "ticket_would_be_proposed" if dry_run else "ticket_proposed"
            ),
            "action_kind": "local_create",
            "target_server_id": server_id,
            "dry_run": dry_run,
            "requires_admin_nod": True,
            "risk_summary": "local_creation_required",
        }
    )
    if ticket_id is not None:
        out["proposed_ticket_action_id"] = ticket_id
    else:
        out.pop("proposed_ticket_action_id", None)
    return {k: v for k, v in out.items() if v is not None}


def _has_unavailable_blocker(plan: Any, codes: Tuple[str, ...]) -> bool:
    for blocker in getattr(plan, "blockers", ()) or ():
        code = getattr(blocker, "blocker_code", "")
        if code in codes:
            return True
    return False


def _register_phase26_native_handler(
    tool_registry: Any,
    *,
    name: str,
    description: str,
    parameters: Dict[str, Dict[str, Any]],
    required: Tuple[str, ...],
    handler: Any,
) -> None:
    if tool_registry is None or not hasattr(tool_registry, "tools"):
        raise Phase26RegistrationError("registry_invalid")
    tools = getattr(tool_registry, "tools", None)
    if not isinstance(tools, dict):
        raise Phase26RegistrationError("registry_invalid")
    native_names = getattr(tool_registry, "_native_handler_names", frozenset())
    if name in native_names:
        raise Phase26RegistrationError("collision_with_native_handler")
    if name in tools:
        raise Phase26RegistrationError("collision_with_existing_tool")
    register = getattr(tool_registry, "register", None)
    if not callable(register):
        raise Phase26RegistrationError("registry_invalid")

    register(
        name=name,
        description=description,
        parameters=parameters,
        handler=handler,
    )
    tool_registry.tools[name]["required"] = list(required)
    if hasattr(tool_registry, "_tool_modules"):
        tool_registry._tool_modules[name] = MCP_LOOP_CATEGORY
    if hasattr(tool_registry, "_sig_cache"):
        tool_registry._sig_cache[name] = (True, None)
    if hasattr(tool_registry, "_tools_desc_cache"):
        tool_registry._tools_desc_cache = None
    if hasattr(tool_registry, "_tool_collection"):
        tool_registry._tool_collection = None


class MCPReActIntegration:
    """Adapter exposing the Phase 24/25 MCP loop as native ReAct tools."""

    def __init__(
        self,
        deps: MCPReActIntegrationDeps,
        *,
        audit_log_path: Optional[Path] = None,
    ) -> None:
        if not isinstance(deps, MCPReActIntegrationDeps):
            raise TypeError("deps must be a MCPReActIntegrationDeps instance")
        if audit_log_path is not None and not isinstance(audit_log_path, Path):
            raise TypeError("audit_log_path must be a pathlib.Path or None")
        self._deps = deps
        self._audit_log_path = audit_log_path

    @staticmethod
    def set_caller_context(caller_kind: str) -> contextvars.Token:
        return _PHASE26_CALLER_CONTEXT.set(_sanitize_caller_kind(caller_kind))

    @staticmethod
    def reset_caller_context(token: contextvars.Token) -> None:
        _PHASE26_CALLER_CONTEXT.reset(token)

    def attach_to_tool_registry(self, tool_registry: Any) -> Tuple[bool, str]:
        if tool_registry is None or not hasattr(tool_registry, "tools"):
            return False, "registry_invalid"
        tools = getattr(tool_registry, "tools", None)
        if not isinstance(tools, dict):
            return False, "registry_invalid"
        expected = {
            CAPABILITY_TOOL_NAME,
            TICKET_TOOL_NAME,
            RUN_AUTONOMY_TOOL_NAME,
            RESUME_TASK_TOOL_NAME,
        }
        if expected.issubset(set(tools)):
            return False, "already_attached"
        try:
            chain = self._build_chain_for_registry(tool_registry)
        except Exception:
            return False, "chain_build_failed"
        try:
            _register_phase26_native_handler(
                tool_registry,
                name=CAPABILITY_TOOL_NAME,
                description=_PHASE26_CAPABILITY_DESC,
                parameters=_PHASE26_CAPABILITY_PARAMS,
                required=_PHASE26_CAPABILITY_REQUIRED,
                handler=self._make_capability_handler(chain),
            )
            _register_phase26_native_handler(
                tool_registry,
                name=TICKET_TOOL_NAME,
                description=_PHASE26_TICKET_DESC,
                parameters=_PHASE26_TICKET_PARAMS,
                required=_PHASE26_TICKET_REQUIRED,
                handler=self._make_ticket_handler(chain),
            )
            _register_phase26_native_handler(
                tool_registry,
                name=RUN_AUTONOMY_TOOL_NAME,
                description=_PHASE26_RUN_DESC,
                parameters=_PHASE26_RUN_PARAMS,
                required=_PHASE26_RUN_REQUIRED,
                handler=self._make_run_autonomy_handler(chain),
            )
            _register_phase26_native_handler(
                tool_registry,
                name=RESUME_TASK_TOOL_NAME,
                description=_PHASE26_RESUME_DESC,
                parameters=_PHASE26_RESUME_PARAMS,
                required=_PHASE26_RESUME_REQUIRED,
                handler=self._make_resume_task_handler(chain),
            )
            # ── Phase F : 5 outils LLM user-facing ──────────────────────
            _register_phase26_native_handler(
                tool_registry,
                name=ADD_MCP_TOOL_NAME,
                description=_PHASE_F_ADD_DESC,
                parameters=_PHASE_F_ADD_PARAMS,
                required=_PHASE_F_ADD_REQUIRED,
                handler=self._make_add_mcp_handler(),
            )
            _register_phase26_native_handler(
                tool_registry,
                name=DISABLE_MCP_TOOL_NAME,
                description=_PHASE_F_DISABLE_DESC,
                parameters=_PHASE_F_DISABLE_PARAMS,
                required=_PHASE_F_DISABLE_REQUIRED,
                handler=self._make_disable_mcp_handler(),
            )
            _register_phase26_native_handler(
                tool_registry,
                name=REMOVE_MCP_TOOL_NAME,
                description=_PHASE_F_REMOVE_DESC,
                parameters=_PHASE_F_REMOVE_PARAMS,
                required=_PHASE_F_REMOVE_REQUIRED,
                handler=self._make_remove_mcp_handler(),
            )
            _register_phase26_native_handler(
                tool_registry,
                name=SET_MCP_PREFERENCE_TOOL_NAME,
                description=_PHASE_F_PREF_DESC,
                parameters=_PHASE_F_PREF_PARAMS,
                required=_PHASE_F_PREF_REQUIRED,
                handler=self._make_set_mcp_preference_handler(),
            )
            _register_phase26_native_handler(
                tool_registry,
                name=SET_MCP_CATEGORY_TOOL_NAME,
                description=_PHASE_F_CATEGORY_DESC,
                parameters=_PHASE_F_CATEGORY_PARAMS,
                required=_PHASE_F_CATEGORY_REQUIRED,
                handler=self._make_set_mcp_category_handler(),
            )
        except Phase26RegistrationError as exc:
            return False, str(exc)
        return True, "attached"

    def _build_chain_for_registry(self, tool_registry: Any) -> _Phase26Chain:
        discovery_reader = None
        reports_dir = self._deps.discovery_reports_dir
        if isinstance(reports_dir, Path) and reports_dir.exists():
            discovery_reader = FilesystemDiscoveryReader(reports_dir)

        cap_resolver = CapabilityResolver(
            CapabilityResolverDeps(
                tool_registry=tool_registry,
                catalog=self._deps.catalog,
                discovery=discovery_reader,
                policy_resolver=self._deps.policy_resolver,
                policy_attributor=self._deps.policy_attributor,
                approval_queue=self._deps.approval_queue,
                auto_approve=self._deps.auto_approve_engine,
                runtime_watcher=self._deps.runtime_watcher,
                drift=None,
            )
        )
        proposal_sources = self._build_proposal_sources()
        proposal_planner = MCPProposalPlanner(
            MCPProposalPlannerDeps(sources=proposal_sources, catalog_lookup=None)
        )
        autonomous_planner = AutonomousMCPLoopPlanner(
            AutonomousMCPLoopDeps(
                capability_resolver=cap_resolver,
                proposal_planner=proposal_planner,
                approval_queue_read=self._deps.approval_queue,
            )
        )
        execution_bridge = MCPExecutionBridge(
            MCPExecutionBridgeDeps(
                catalog_add_orchestrator=self._deps.catalog_add_orchestrator,
                install_orchestrator=self._deps.install_orchestrator,
                activation_service=self._deps.activation_service,
                approval_queue_read=self._deps.approval_queue,
                catalog_read=self._deps.catalog,
                tool_registry_read=tool_registry,
            )
        )
        return _Phase26Chain(
            tool_registry=tool_registry,
            autonomous_planner=autonomous_planner,
            execution_bridge=execution_bridge,
        )

    def _build_proposal_sources(self) -> Tuple[Any, ...]:
        sources = []
        curated = Path(__file__).resolve().parent / "data" / "curated_mcp_catalog.json"
        sources.append(CuratedOfflineCatalogSource(curated))
        local_root = Path(os.getenv("LUMENA_MCP_LOCAL_SEARCH_ROOT", "")).expanduser()
        if str(local_root) not in ("", ".") and local_root.exists():
            sources.append(LocalFilesystemSource(local_root))
        sources.append(
            NpmRegistrySearchSource(
                network_enabled=_is_network_search_enabled(),
                timeout_s=4.0,
            )
        )
        sources.append(
            PyPIProjectLookupSource(
                network_enabled=_is_network_search_enabled(),
                timeout_s=4.0,
            )
        )
        network_enabled = _is_network_search_enabled()
        sources.append(
            MCPDirectorySearchSource(
                name="smithery_directory",
                url_templates=("https://smithery.ai/search?q={query}",),
                network_enabled=network_enabled,
                timeout_s=5.0,
            )
        )
        sources.append(
            MCPDirectorySearchSource(
                name="pulsemcp_directory",
                url_templates=("https://www.pulsemcp.com/servers?q={query}",),
                network_enabled=network_enabled,
                timeout_s=5.0,
            )
        )
        sources.append(
            MCPDirectorySearchSource(
                name="github_web_search",
                url_templates=(
                    "https://github.com/search?q={query}+mcp+server&type=repositories",
                ),
                network_enabled=network_enabled,
                timeout_s=5.0,
            )
        )
        return tuple(sources)

    def _make_capability_handler(self, chain: _Phase26Chain):
        async def _handler(intent: Any = None, **kwargs: Any) -> str:
            caller_kind = _resolve_caller_kind(kwargs.get("caller_kind"))
            return self.handle_request_mcp_capability(
                intent,
                caller_kind=caller_kind,
                chain=chain,
                profile=kwargs.get("profile"),
            )

        return _handler

    def _make_ticket_handler(self, chain: _Phase26Chain):
        async def _handler(
            intent: Any = None,
            confirmation_phrase: Any = "",
            live: Any = False,
            **kwargs: Any,
        ) -> str:
            caller_kind = _resolve_caller_kind(kwargs.get("caller_kind"))
            return self.handle_request_mcp_ticket(
                intent,
                caller_kind=caller_kind,
                confirmation_phrase=confirmation_phrase,
                live=live,
                chain=chain,
                profile=kwargs.get("profile"),
            )

        return _handler

    def _make_run_autonomy_handler(self, chain: _Phase26Chain):
        async def _handler(
            intent: Any = None,
            live: Any = False,
            confirmation_phrase: Any = "",
            **kwargs: Any,
        ) -> str:
            caller_kind = _resolve_caller_kind(kwargs.get("caller_kind"))
            return self.handle_run_mcp_autonomy(
                intent,
                caller_kind=caller_kind,
                live=live,
                confirmation_phrase=confirmation_phrase,
                chain=chain,
                profile=kwargs.get("profile"),
            )

        return _handler

    def _make_resume_task_handler(self, chain: _Phase26Chain):
        async def _handler(intent: Any = None, **kwargs: Any) -> str:
            caller_kind = _resolve_caller_kind(kwargs.get("caller_kind"))
            return self.handle_resume_mcp_task(
                intent,
                caller_kind=caller_kind,
                chain=chain,
                profile=kwargs.get("profile"),
            )

        return _handler

    def handle_request_mcp_capability(
        self,
        intent: Any,
        *,
        caller_kind: Optional[str] = None,
        chain: Optional[_Phase26Chain] = None,
        profile: Optional[str] = None,
        task_context_hash: Optional[str] = None,
    ) -> str:
        handler_name = CAPABILITY_TOOL_NAME
        caller = _resolve_caller_kind(caller_kind)
        if caller in _CODE_AGENT_CALLERS:
            return _blocked(handler_name, "code_agent_out_of_scope")
        if caller not in _CAPABILITY_CALLERS:
            return _blocked(handler_name, "caller_kind_not_allowed")
        cleaned = _sanitize_intent(intent)
        if cleaned is None:
            raw = intent if isinstance(intent, str) else ""
            if isinstance(raw, str) and _CONTROL_RE.search(raw):
                return _blocked(handler_name, "intent_invalid_format")
            return _blocked(handler_name, "intent_too_short_or_too_long")
        if self._deps.catalog is None:
            return _blocked(handler_name, "phase24_unavailable")
        if chain is None:
            return _blocked(handler_name, "phase24_unavailable")
        try:
            plan = chain.autonomous_planner.plan_for_intent(
                cleaned,
                caller_kind=caller,
                profile=profile,
                task_context={"hash": task_context_hash} if task_context_hash else None,
            )
        except Exception:
            return _blocked(handler_name, "phase24_unavailable")
        if _has_unavailable_blocker(plan, ("no_phase22_resolver", "no_phase23_planner")):
            return _blocked(handler_name, "phase24_unavailable")
        payload = _extract_phase24_payload(plan, dry_run=True)
        return _ok(handler_name, payload)

    def handle_request_mcp_ticket(
        self,
        intent: Any,
        *,
        caller_kind: Optional[str] = None,
        confirmation_phrase: Any = "",
        live: Any = False,
        chain: Optional[_Phase26Chain] = None,
        profile: Optional[str] = None,
    ) -> str:
        handler_name = TICKET_TOOL_NAME
        caller = _resolve_caller_kind(caller_kind)
        if caller in _CODE_AGENT_CALLERS:
            return _blocked(handler_name, "code_agent_out_of_scope")
        if caller not in _TICKET_CALLERS:
            return _blocked(handler_name, "caller_kind_not_allowed")
        if confirmation_phrase != TICKET_CONFIRMATION_PHRASE:
            # Phase I-8 (Fix AH) : exposer la phrase attendue comme add_mcp.
            # Observé runtime 2026-06-11 17:41 : DeepSeek a confondu les
            # phrases TICKET/AUTONOMY et a bouclé 3× sur un blocage muet.
            return _blocked(
                handler_name, "confirmation_phrase_invalid",
                payload={
                    "expected_confirmation_phrase": TICKET_CONFIRMATION_PHRASE,
                    "hint": "Génère cette phrase TOI-MÊME au prochain tour. "
                            "Ne demande JAMAIS à l'utilisateur de la taper.",
                },
            )
        cleaned = _sanitize_intent(intent)
        if cleaned is None:
            raw = intent if isinstance(intent, str) else ""
            if isinstance(raw, str) and _CONTROL_RE.search(raw):
                return _blocked(handler_name, "intent_invalid_format")
            return _blocked(handler_name, "intent_too_short_or_too_long")
        dry_run = not (_safe_bool(live) and _is_live_enabled())
        if _safe_bool(live) and dry_run:
            return _blocked(
                handler_name,
                "live_requirements_not_met",
                payload={"dry_run": True, "live_mode_enabled": _is_live_enabled()},
            )
        if self._deps.catalog is None:
            return _blocked(handler_name, "phase24_unavailable")
        if chain is None:
            return _blocked(handler_name, "phase24_unavailable")
        try:
            phase24_plan = chain.autonomous_planner.plan_for_intent(
                cleaned,
                caller_kind=caller,
                profile=profile,
            )
        except Exception:
            return _blocked(handler_name, "phase24_unavailable")
        if _has_unavailable_blocker(
            phase24_plan, ("no_phase22_resolver", "no_phase23_planner")
        ):
            return _blocked(handler_name, "phase24_unavailable")
        try:
            bridge_plan = chain.execution_bridge.request_action_for_plan(
                phase24_plan,
                caller_kind=caller,
                dry_run=dry_run,
            )
        except Exception:
            return _blocked(handler_name, "phase25_unavailable")
        if _has_unavailable_blocker(
            bridge_plan,
            (
                "no_install_orchestrator_dep",
                "no_activation_service_dep",
                "no_approval_queue_read",
            ),
        ):
            return _blocked(handler_name, "phase25_unavailable")
        payload = _extract_phase25_payload(bridge_plan, dry_run=dry_run)
        if _is_local_creation_descriptive_payload(payload):
            local_orchestrator = self._deps.local_creation_orchestrator
            proposer = getattr(local_orchestrator, "propose_local_creation", None)
            if not callable(proposer):
                return _blocked(handler_name, "phase25_unavailable")
            try:
                proposal = proposer(
                    cleaned,
                    caller_kind=caller,
                    profile=profile,
                    dry_run=dry_run,
                )
            except Exception:
                return _blocked(handler_name, "phase25_unavailable")
            payload = _local_creation_ticket_payload(
                payload,
                proposal,
                dry_run=dry_run,
            )
        return _ok(handler_name, payload)

    def _try_auto_approve_and_execute(
        self,
        payload: Dict[str, Any],
        *,
        chain: _Phase26Chain,
        caller: str,
        profile: Optional[str],
    ) -> Tuple[Dict[str, Any], bool]:
        out = dict(payload)
        if not (_is_live_enabled() and _is_agent_autoapprove_enabled()):
            out.setdefault("next_step", "approve_ticket_then_resume")
            return out, False

        ticket_id = _valid_uuid4_hex(out.get("proposed_ticket_action_id"))
        server_id = _valid_server_id(out.get("target_server_id"))
        expected_tool = _expected_ticket_tool_name(out)
        approval_queue = self._deps.approval_queue
        auto_engine = self._deps.auto_approve_engine
        approve_if = getattr(approval_queue, "approve_if", None)
        evaluate = getattr(auto_engine, "evaluate", None)

        # Phase I-7 : bypass curated. Les tickets mcp_catalog_add: / mcp_install:
        # / mcp_activate: utilisent un format `:` que la regex stricte de
        # AutoApproveEngine (^mcp__server__tool$) ne peut pas matcher. Pour les
        # MCPs curated (KNOWN_MCPS) on auto-approve directement : le package_spec
        # est cross-vérifié contre le catalog curated, l'engine n'est pas requis.
        is_curated = _is_curated_install_ticket(expected_tool, server_id, out)
        # Phase I-8 (Fix AA.1) : bypass étendu aux install/activate d'entrées
        # déjà au catalogue (l'humain a approuvé le catalog_add — gate unique).
        is_cataloged = (
            not is_curated
            and _is_cataloged_install_ticket(
                expected_tool, server_id, out, self._deps.catalog,
            )
        )

        if is_curated or is_cataloged:
            # Engine non requis pour les curated, seul approve_if suffit.
            if (
                ticket_id is None
                or server_id is None
                or expected_tool is None
                or not callable(approve_if)
            ):
                out["recommendation_code"] = "auto_approve_unavailable"
                out["next_step"] = "approve_ticket_then_resume"
                return out, False
        else:
            if (
                ticket_id is None
                or server_id is None
                or expected_tool is None
                or not callable(approve_if)
                or not callable(evaluate)
            ):
                out["recommendation_code"] = "auto_approve_unavailable"
                out["next_step"] = "approve_ticket_then_resume"
                return out, False

        pending = None
        get_pending = getattr(approval_queue, "get", None)
        if callable(get_pending):
            try:
                pending = get_pending(ticket_id)
            except Exception:
                pending = None
        pending_tool = getattr(pending, "tool_name", None)
        if pending_tool != expected_tool:
            out["recommendation_code"] = "auto_approve_unavailable"
            out["next_step"] = "approve_ticket_then_resume"
            return out, False

        if is_curated:
            def _evaluator(request: Any) -> bool:
                if getattr(request, "tool_name", "") != expected_tool:
                    return False
                if getattr(request, "caller_kind", "") != caller:
                    return False
                req_args = getattr(request, "args", None)
                if not isinstance(req_args, dict):
                    return False
                if req_args.get("server_id") != server_id:
                    return False
                # Vérification finale : package_spec si présent doit matcher curated
                from src.mcp.known_mcps import get_known_mcp  # noqa: WPS433
                curated = get_known_mcp(server_id)
                if curated is not None:
                    ticket_pkg = req_args.get("package_spec")
                    if ticket_pkg and ticket_pkg != curated.package_spec:
                        return False
                return True
        elif is_cataloged:
            # Phase I-8 (Fix AA.1) : cross-check au moment de l'approbation
            # contre le CATALOGUE (source de vérité approuvée par l'humain).
            catalog_dep = self._deps.catalog

            def _evaluator(request: Any) -> bool:
                if getattr(request, "tool_name", "") != expected_tool:
                    return False
                if getattr(request, "caller_kind", "") != caller:
                    return False
                req_args = getattr(request, "args", None)
                if not isinstance(req_args, dict):
                    return False
                if req_args.get("server_id") != server_id:
                    return False
                try:
                    entry = catalog_dep.get_server(server_id)
                except Exception:  # noqa: BLE001
                    return False
                if entry is None:
                    return False
                status_value = getattr(
                    getattr(entry, "status", None), "value", None
                )
                if status_value not in ("declared", "installed"):
                    return False
                ticket_pkg = req_args.get("package_spec")
                entry_pkg = getattr(entry, "package_spec", None)
                if ticket_pkg and isinstance(entry_pkg, str):
                    if ticket_pkg != entry_pkg:
                        return False
                return True
        else:
            def _evaluator(request: Any) -> bool:
                if getattr(request, "tool_name", "") != expected_tool:
                    return False
                if getattr(request, "caller_kind", "") != caller:
                    return False
                req_args = getattr(request, "args", None)
                if not isinstance(req_args, dict) or req_args.get("server_id") != server_id:
                    return False
                evaluation = evaluate(
                    profile=profile or "default",
                    tool_name=getattr(request, "tool_name", ""),
                    args=req_args,
                    policy=getattr(request, "policy", None),
                    caller_kind=getattr(request, "caller_kind", ""),
                )
                return _enum_value(getattr(evaluation, "decision", None)) == "matched"

        try:
            approval_result = approve_if(ticket_id, _evaluator)
        except Exception:
            out["recommendation_code"] = "auto_approve_unavailable"
            out["next_step"] = "approve_ticket_then_resume"
            return out, False

        approval_decision = _enum_value(getattr(approval_result, "decision", None))
        if approval_decision != "approved":
            out["recommendation_code"] = "auto_approve_not_matched"
            out["next_step"] = "approve_ticket_then_resume"
            return out, False

        if expected_tool.startswith("mcp_local_create:"):
            executor = self._deps.local_creation_executor
            execute_local = getattr(executor, "execute_approved_local_creation", None)
            if not callable(execute_local):
                out["recommendation_code"] = "auto_execute_failed"
                out["next_step"] = "inspect_mcp_panel"
                return out, False
            try:
                result = execute_local(
                    approval_result,
                    server_id=server_id,
                    dry_run=False,
                )
            except Exception:
                out["recommendation_code"] = "auto_execute_failed"
                out["next_step"] = "inspect_mcp_panel"
                return out, False
            if bool(getattr(result, "success", False)):
                out["recommendation_code"] = "auto_executed"
                out["next_step"] = "retry_capability"
                out["auto_executed_action_kind"] = "local_create"
                out["catalog_status"] = getattr(result, "catalog_status", None)
                return {k: v for k, v in out.items() if v is not None}, True
            out["recommendation_code"] = "auto_execute_failed"
            out["next_step"] = "inspect_mcp_panel"
            return out, False

        try:
            exec_plan = chain.execution_bridge.execute_after_approval(
                ticket_id,
                approval_result,
                server_id,
                caller_kind=caller,
                dry_run=False,
            )
        except Exception:
            out["recommendation_code"] = "auto_execute_failed"
            out["next_step"] = "inspect_mcp_panel"
            return out, False
        exec_payload = _extract_phase25_payload(exec_plan, dry_run=False)
        exec_rec = exec_payload.get("recommendation_code")
        if exec_rec in {"executed_success", "already_applied"}:
            exec_payload["recommendation_code"] = "auto_executed"
            exec_payload["next_step"] = "retry_capability"
            exec_payload["auto_executed_action_kind"] = out.get("action_kind")
            return exec_payload, True
        exec_payload["recommendation_code"] = "auto_execute_failed"
        exec_payload["next_step"] = "inspect_mcp_panel"
        return exec_payload, False

    def handle_run_mcp_autonomy(
        self,
        intent: Any,
        *,
        caller_kind: Optional[str] = None,
        live: Any = False,
        confirmation_phrase: Any = "",
        chain: Optional[_Phase26Chain] = None,
        profile: Optional[str] = None,
    ) -> str:
        handler_name = RUN_AUTONOMY_TOOL_NAME
        caller = _resolve_caller_kind(caller_kind)
        if caller in _CODE_AGENT_CALLERS:
            return _blocked(handler_name, "code_agent_out_of_scope")
        if caller != "react":
            return _blocked(handler_name, "caller_kind_not_allowed")
        if _safe_bool(live) and confirmation_phrase != AUTONOMY_CONFIRMATION_PHRASE:
            # Phase I-8 (Fix AH) : exposer la phrase attendue comme add_mcp.
            return _blocked(
                handler_name, "confirmation_phrase_invalid",
                payload={
                    "expected_confirmation_phrase": AUTONOMY_CONFIRMATION_PHRASE,
                    "hint": "Génère cette phrase TOI-MÊME au prochain tour. "
                            "Ne demande JAMAIS à l'utilisateur de la taper.",
                },
            )

        last_payload: Dict[str, Any] = {}
        auto_tickets_seen = set()
        for _step in range(_AUTO_LOOP_MAX_STEPS):
            cap_raw = self.handle_request_mcp_capability(
                intent,
                caller_kind=caller,
                chain=chain,
                profile=profile,
            )
            try:
                cap = json.loads(cap_raw)
            except json.JSONDecodeError:
                return _blocked(handler_name, "phase24_unavailable")
            if cap.get("decision") != "ok":
                return cap_raw.replace(CAPABILITY_TOOL_NAME, handler_name, 1)
            cap_payload = cap.get("payload") if isinstance(cap.get("payload"), dict) else {}
            rec = cap_payload.get("recommendation_code")
            if rec == "use_existing":
                payload = dict(cap_payload)
                payload["recommendation_code"] = "autonomy_ready_to_use"
                payload["next_step"] = "call_target_tool"
                payload["autonomy_steps"] = _step + 1
                payload = self._force_activate_if_needed(
                    payload, live_requested=_safe_bool(live)
                )
                return _ok(handler_name, payload)

            ticket_raw = self.handle_request_mcp_ticket(
                intent,
                caller_kind=caller,
                confirmation_phrase=TICKET_CONFIRMATION_PHRASE,
                live=live,
                chain=chain,
                profile=profile,
            )
            try:
                ticket = json.loads(ticket_raw)
            except json.JSONDecodeError:
                return _blocked(handler_name, "phase25_unavailable")
            if ticket.get("decision") != "ok":
                return ticket_raw.replace(TICKET_TOOL_NAME, handler_name, 1)
            payload = ticket.get("payload") if isinstance(ticket.get("payload"), dict) else {}
            payload = dict(payload)
            if payload.get("recommendation_code") in {
                "ticket_proposed",
                "waiting_approval",
            }:
                ticket_id = _valid_uuid4_hex(payload.get("proposed_ticket_action_id"))
                if ticket_id is not None and ticket_id in auto_tickets_seen:
                    payload["recommendation_code"] = "auto_loop_exhausted"
                    payload["next_step"] = "inspect_mcp_panel"
                    payload["autonomy_steps"] = _step + 1
                    return _ok(handler_name, payload)
                if ticket_id is not None:
                    auto_tickets_seen.add(ticket_id)
                auto_payload, should_retry = self._try_auto_approve_and_execute(
                    payload,
                    chain=chain,
                    caller=caller,
                    profile=profile,
                )
                auto_payload["autonomy_steps"] = _step + 1
                if should_retry:
                    last_payload = auto_payload
                    continue
                if auto_payload.get("recommendation_code") == "ticket_proposed":
                    auto_payload["recommendation_code"] = "autonomy_ticket_created"
                auto_payload.setdefault("next_step", "approve_ticket_then_resume")
                return _ok(handler_name, auto_payload)
            if payload.get("recommendation_code") == "ticket_would_be_proposed":
                payload["recommendation_code"] = "autonomy_would_run"
                payload["next_step"] = "enable_live_or_confirm"
            elif payload.get("recommendation_code") == "already_applied":
                payload["recommendation_code"] = "autonomy_ready_to_use"
                payload["next_step"] = "retry_capability"
            else:
                payload.setdefault("next_step", "inspect_mcp_panel")
            payload["autonomy_steps"] = _step + 1
            payload = self._force_activate_if_needed(
                payload, live_requested=_safe_bool(live)
            )
            return _ok(handler_name, payload)

        payload = dict(last_payload)
        payload["recommendation_code"] = "auto_loop_exhausted"
        payload["next_step"] = "inspect_mcp_panel"
        payload["autonomy_steps"] = _AUTO_LOOP_MAX_STEPS
        payload = self._force_activate_if_needed(
            payload, live_requested=_safe_bool(live)
        )
        return _ok(handler_name, payload)

    def _force_activate_if_needed(
        self,
        payload: Dict[str, Any],
        *,
        live_requested: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Fix L (Phase I-7) : déclenche l'activation si le MCP target est
        en statut INSTALLED mais pas encore ACTIVE.

        Le pipeline `handle_run_mcp_autonomy` enchaîne capability → ticket
        → execute (install). Mais après succès install, la boucle suivante
        voit le MCP en `installed`, le capability resolver retourne
        `already_applied` et le run_mcp_autonomy retourne `autonomy_ready_to_use`
        SANS jamais déclencher l'activation. Conséquence : process MCP jamais
        spawné, tools jamais registrés dans le ToolRegistry.

        Ce helper s'intercale juste avant les `return _ok` finaux : si le
        target_server_id est connu et que le catalog dit `installed`, on
        appelle ActivationService.activate() pour matérialiser les tools.
        Idempotent (activate sur déjà-active retourne success).
        """
        try:
            sid = payload.get("target_server_id")
            if not isinstance(sid, str) or not sid:
                return payload
            catalog = getattr(self._deps, "catalog", None)
            activation = getattr(self._deps, "activation_service", None)
            if catalog is None or activation is None:
                return payload
            try:
                entry = catalog.get_server(sid)
            except Exception:
                return payload
            if entry is None:
                return payload
            status = getattr(entry, "status", None)
            status_value = getattr(status, "value", status)
            if not isinstance(status_value, str):
                return payload
            # Fix S (Phase I-7) : statut ACTIVE fantôme. Si le catalog dit
            # "active" mais que le process ne tourne pas (crash mi-session,
            # ou reboot sans réconciliation), on reset à INSTALLED pour que
            # l'activation ci-dessous reparte proprement. Self-healing sans
            # redémarrage de Lumena.
            if status_value == "active":
                try:
                    if activation.is_running(sid):
                        return payload  # vraiment actif, rien à faire
                except Exception:
                    return payload
                try:
                    from src.mcp.server_catalog import ServerStatus
                    catalog.update_status(sid, ServerStatus.INSTALLED)
                    payload["force_activate_stale_reset"] = True
                    status_value = "installed"
                except Exception:
                    return payload
            # Phase I-8 (Fix AA.2) : statut DECLARED → l'entrée est au
            # catalogue (donc approuvée par l'humain ou curated) mais jamais
            # installée. Avant I-8 ce chemin retournait tel quel et le payload
            # `autonomy_ready_to_use` MENTAIT (observé runtime 2026-06-11
            # 00:16:59 : « ready » avec data/mcp vide, 0 tool). On enchaîne
            # l'install ici, puis l'activation ci-dessous — même frontière de
            # confiance que le bypass Fix AA.1.
            if status_value == "declared":
                # Garde dry-run : un run_mcp_autonomy(live=false) ou un
                # payload dry_run ne doit JAMAIS déclencher un npm install.
                if payload.get("dry_run") is True or live_requested is False:
                    payload["force_install_attempted"] = False
                    payload["force_install_skipped"] = "dry_run"
                    payload["recommendation_code"] = "needs_install_approval"
                    payload["next_step"] = "enable_live_or_confirm"
                    return payload
                if not _is_live_enabled():
                    payload["force_install_attempted"] = False
                    payload["force_install_skipped"] = "live_mode_disabled"
                    payload["recommendation_code"] = "needs_install_approval"
                    payload["next_step"] = "inspect_mcp_panel"
                    return payload
                installed_ok, install_reason = self._force_install_declared(
                    sid, entry
                )
                payload["force_install_attempted"] = True
                payload["force_install_ok"] = installed_ok
                if not installed_ok:
                    payload["force_install_reason"] = install_reason[:200]
                    # Honnêteté (Fix AA.3) : ne JAMAIS laisser un code
                    # « ready » sur un échec d'install.
                    payload["recommendation_code"] = "autonomy_install_failed"
                    payload["next_step"] = "inspect_mcp_panel"
                    return payload
                # Install OK → relire le statut pour enchaîner l'activation.
                try:
                    entry = catalog.get_server(sid)
                except Exception:
                    return payload
                if entry is None:
                    return payload
                status = getattr(entry, "status", None)
                status_value = getattr(status, "value", status)
                if not isinstance(status_value, str):
                    return payload
            if status_value != "installed":
                # Statut quarantined/removed/etc. — pas activable. Fix AA.3 :
                # ne JAMAIS laisser partir un code « ready » mensonger.
                if payload.get("recommendation_code") in (
                    "autonomy_ready_to_use",
                    "resume_ready_to_use",
                ):
                    payload["recommendation_code"] = "autonomy_blocked_status"
                    payload["next_step"] = "inspect_mcp_panel"
                    payload["catalog_status_observed"] = status_value
                return payload
            # Fix N (Phase I-7) : forge un ApprovalResult APPROVED comme Fix K v2.
            # Sans ça, ActivationService lève ActivationError("approval_required")
            # ligne 548 → exception silencieuse. Même pattern que boot auto-activate.
            try:
                from src.mcp.approval_queue import (
                    ApprovalResult as _FAResult,
                    ApprovalDecision as _FADecision,
                )
                _forced_approval = _FAResult(
                    decision=_FADecision.APPROVED,
                    args={
                        "action": "activate",
                        "server_id": sid,
                        "reason": "force_activate_autonomy",
                    },
                    reason="force_activate_autonomy",
                )
            except Exception as _approval_err:
                payload["force_activate_attempted"] = True
                payload["force_activate_error"] = type(_approval_err).__name__
                payload["force_activate_error_msg"] = str(_approval_err)[:200]
                return payload
            try:
                result = activation.activate(
                    sid, approval_result=_forced_approval
                )
            except Exception as _act_err:
                payload["force_activate_attempted"] = True
                payload["force_activate_error"] = type(_act_err).__name__
                payload["force_activate_error_msg"] = str(_act_err)[:200]
                return payload
            ok = bool(getattr(result, "success", False))
            payload["force_activate_attempted"] = True
            payload["force_activate_ok"] = ok
            if not ok:
                # Propage la raison pour visibilité (runner_start_failed, etc.)
                payload["force_activate_reason"] = str(
                    getattr(result, "reason", "unknown")
                )[:200]
            if ok:
                # Upgrade : le MCP est maintenant ACTIVE, ses tools dispos.
                payload["recommendation_code"] = "autonomy_activated"
                payload["next_step"] = "call_target_tool"
            return payload
        except Exception:
            # Garde-fou ultime : aucun chemin de _force_activate_if_needed
            # ne doit casser le retour run_mcp_autonomy.
            return payload

    def _force_install_declared(
        self,
        sid: str,
        entry: Any,
    ) -> Tuple[bool, str]:
        """Phase I-8 (Fix AA.2) : installe une entrée DECLARED du catalogue.

        Forge un ApprovalResult APPROVED (pattern Fix N) avec les args
        EXACTS qu'exige execute_approved_install (miroir de la dérivation
        de propose_install : transport/package_name parsés depuis le
        package_spec du CATALOGUE — anti-confused-deputy satisfait par
        construction). Retourne (ok, reason).
        """
        orchestrator = getattr(self._deps, "install_orchestrator", None)
        if orchestrator is None:
            return False, "no_install_orchestrator"
        execute = getattr(orchestrator, "execute_approved_install", None)
        if not callable(execute):
            return False, "no_install_orchestrator"
        package_spec = getattr(entry, "package_spec", None)
        if not isinstance(package_spec, str) or not package_spec:
            return False, "package_spec_missing"
        try:
            from src.mcp.install_orchestrator import (  # noqa: WPS433
                _parse_package_spec,
            )
            parsed = _parse_package_spec(package_spec)
        except Exception as exc:  # noqa: BLE001
            return False, f"parse_failed:{type(exc).__name__}"
        if parsed is None:
            return False, "transport_unsupported"
        transport, package_name = parsed
        try:
            from src.mcp.approval_queue import (  # noqa: WPS433
                ApprovalResult as _FIResult,
                ApprovalDecision as _FIDecision,
            )
            forged = _FIResult(
                decision=_FIDecision.APPROVED,
                args={
                    "action": "install",
                    "server_id": sid,
                    "transport": getattr(transport, "value", transport),
                    "package_name": package_name,
                    "package_spec": package_spec,
                    "version": getattr(entry, "version", None),
                    "trust_score": getattr(entry, "trust_score", None),
                    "reason": "force_install_autonomy",
                },
                reason="force_install_autonomy",
            )
        except Exception as exc:  # noqa: BLE001
            return False, f"approval_forge_failed:{type(exc).__name__}"
        try:
            result = execute(sid, forged)
        except Exception as exc:  # noqa: BLE001
            return False, f"{type(exc).__name__}:{str(exc)[:120]}"
        ok = bool(getattr(result, "success", False))
        reason = str(getattr(result, "reason", "") or "")
        return ok, reason if reason else ("ok" if ok else "unknown")

    def handle_resume_mcp_task(
        self,
        intent: Any,
        *,
        caller_kind: Optional[str] = None,
        chain: Optional[_Phase26Chain] = None,
        profile: Optional[str] = None,
    ) -> str:
        handler_name = RESUME_TASK_TOOL_NAME
        caller = _resolve_caller_kind(caller_kind)
        if caller in _CODE_AGENT_CALLERS:
            return _blocked(handler_name, "code_agent_out_of_scope")
        if caller != "react":
            return _blocked(handler_name, "caller_kind_not_allowed")
        cap_raw = self.handle_request_mcp_capability(
            intent,
            caller_kind=caller,
            chain=chain,
            profile=profile,
        )
        try:
            cap = json.loads(cap_raw)
        except json.JSONDecodeError:
            return _blocked(handler_name, "phase24_unavailable")
        if cap.get("decision") != "ok":
            return cap_raw.replace(CAPABILITY_TOOL_NAME, handler_name, 1)
        payload = cap.get("payload") if isinstance(cap.get("payload"), dict) else {}
        payload = dict(payload)
        if payload.get("recommendation_code") == "use_existing":
            payload["recommendation_code"] = "resume_ready_to_use"
            payload["next_step"] = "call_target_tool"
        else:
            payload["next_step"] = "run_mcp_autonomy"
        return _ok(handler_name, payload)

    # ══════════════════════════════════════════════════════════════════════
    # Phase F — 5 outils LLM user-facing (add/disable/remove/preference/category)
    # ══════════════════════════════════════════════════════════════════════

    def handle_add_mcp(
        self,
        target: Any,
        *,
        caller_kind: Optional[str] = None,
        live: Any = False,
        confirmation_phrase: Any = "",
    ) -> str:
        """Dry-run par defaut : resout la cible et renvoie le payload.
        Live=True : necessite confirmation_phrase + catalog_add_orchestrator."""
        from src.mcp.target_resolver import resolve_target
        handler_name = ADD_MCP_TOOL_NAME
        caller = _resolve_caller_kind(caller_kind)
        if caller in _CODE_AGENT_CALLERS:
            return _blocked(handler_name, "code_agent_out_of_scope")
        # Lecture autorisee pour les agents capability, mutation pour react only.
        if not _safe_bool(live):
            if caller not in _CAPABILITY_CALLERS:
                return _blocked(handler_name, "caller_kind_not_allowed")
        else:
            if caller not in _TICKET_CALLERS:
                return _blocked(handler_name, "caller_kind_not_allowed")
        if not isinstance(target, str) or not target.strip():
            return _blocked(handler_name, "mcp_target_invalid")
        try:
            resolved = resolve_target(target)
        except Exception:  # noqa: BLE001
            return _blocked(handler_name, "mcp_target_invalid")
        payload: Dict[str, Any] = {
            "recommendation_code": "mcp_target_resolved",
            "kind": resolved.kind,
            "package_spec": resolved.package_spec,
            "version": resolved.version,
            "source_url": resolved.source_url,
        }
        if resolved.kind == "unknown":
            payload["recommendation_code"] = "mcp_target_unresolved"
            return _ok(handler_name, payload)
        # Dry-run : on s'arrete la.
        if not _safe_bool(live):
            payload["dry_run"] = True
            return _ok(handler_name, payload)
        # Live : exige confirmation_phrase + catalog_add_orchestrator dispo.
        if confirmation_phrase != ADD_MCP_CONFIRMATION_PHRASE:
            return _blocked(
                handler_name, "confirmation_phrase_invalid",
                payload={
                    "expected_confirmation_phrase": ADD_MCP_CONFIRMATION_PHRASE,
                    "hint": "Génère cette phrase TOI-MÊME au prochain tour. "
                            "Ne demande JAMAIS à l'utilisateur de la taper.",
                },
            )
        # Phase I-8 (Fix AS) : URL GitHub dont le README ne mentionne aucun
        # package npm/PyPI installable → blocage HONNÊTE avec hint, au lieu
        # du crash `mcp_action_failed` générique de propose() sur spec vide.
        # Lumena n'installe JAMAIS depuis les sources (registres only) — le
        # README ne sert qu'à retrouver le nom du package publié.
        if resolved.kind == "github_url" and not resolved.package_spec:
            return _blocked(
                handler_name, "mcp_github_no_package",
                payload={
                    "source_url": resolved.source_url,
                    "hint": (
                        "Le README de ce repo GitHub ne mentionne aucun "
                        "package npm/PyPI installable (ou est inaccessible). "
                        "Lumena n'installe pas depuis les sources : demande "
                        "à l'utilisateur le nom EXACT du package "
                        "(npm:<nom> ou pypi:<nom>) — ne le devine JAMAIS."
                    ),
                },
            )
        orchestrator = self._deps.catalog_add_orchestrator
        if orchestrator is None or not callable(getattr(orchestrator, "propose", None)):
            return _blocked(handler_name, "mcp_service_unavailable")
        # Phase I-8 (Fix AB) : vérification d'existence registry AVANT toute
        # mutation catalogue. Bloque les packages hallucinés par le LLM
        # (False = 404 confirmé). None (réseau indisponible / local:) ne
        # bloque jamais — on trace seulement.
        if resolved.kind != "known_mcp":
            try:
                from src.mcp.target_resolver import probe_package_exists
                exists = probe_package_exists(resolved.package_spec)
            except Exception:  # noqa: BLE001
                exists = None
            if exists is False:
                return _blocked(
                    handler_name, "mcp_package_not_found",
                    payload={
                        "package_spec": resolved.package_spec,
                        "hint": (
                            "Ce package n'existe pas sur le registry "
                            "(npm/PyPI). Ne devine JAMAIS un nom de package : "
                            "utilise run_mcp_autonomy(intent=...) qui fait "
                            "une vraie recherche, ou demande l'URL exacte "
                            "à l'utilisateur."
                        ),
                    },
                )
            payload["existence_check"] = (
                "confirmed" if exists is True else "unverified_network"
            )
        try:
            proposal = orchestrator.propose(
                package_spec=resolved.package_spec,
                source_kind=resolved.kind,
                source_url=resolved.source_url,
                slug=resolved.slug,
                display_name=resolved.display_name,
                version=resolved.version,
                trust_score=resolved.trust_score,
            )
        except Exception:  # noqa: BLE001
            return _blocked(handler_name, "mcp_action_failed")
        payload["recommendation_code"] = "mcp_added"
        payload["proposal"] = _safe_scalar(proposal) if not isinstance(
            proposal, (dict, list, str, int, float, bool, type(None))
        ) else proposal
        payload["dry_run"] = False
        # Phase I-8 (Fix AL) : add_mcp ne fait QUE cataloguer — le payload
        # doit dire ce qui reste à faire. Observé runtime 2026-06-11 22:44 :
        # sans ticket_id ni statut, le LLM a inventé une étape d'approbation
        # inexistante puis n'a jamais enchaîné l'install/activation.
        ticket_id = getattr(proposal, "approval_ticket_id", None)
        payload["approval_ticket_id"] = (
            ticket_id if isinstance(ticket_id, str) else None
        )
        sid = getattr(proposal, "server_id", None)
        if isinstance(sid, str) and sid:
            payload["target_server_id"] = sid
            catalog = self._deps.catalog
            if catalog is not None:
                try:
                    entry = catalog.get_server(sid)
                    status = getattr(
                        getattr(entry, "status", None), "value", None
                    )
                    if isinstance(status, str):
                        payload["catalog_status"] = status
                except Exception:  # noqa: BLE001
                    pass
        payload["next_step"] = (
            "approve_ticket_then_resume"
            if payload["approval_ticket_id"]
            else "run_mcp_autonomy"
        )
        return _ok(handler_name, payload)

    def handle_disable_mcp(
        self,
        server_id: Any,
        *,
        caller_kind: Optional[str] = None,
        confirmation_phrase: Any = "",
    ) -> str:
        handler_name = DISABLE_MCP_TOOL_NAME
        caller = _resolve_caller_kind(caller_kind)
        if caller in _CODE_AGENT_CALLERS:
            return _blocked(handler_name, "code_agent_out_of_scope")
        if caller not in _TICKET_CALLERS:
            return _blocked(handler_name, "caller_kind_not_allowed")
        if confirmation_phrase != DISABLE_MCP_CONFIRMATION_PHRASE:
            return _blocked(
                handler_name, "confirmation_phrase_invalid",
                payload={
                    "expected_confirmation_phrase": DISABLE_MCP_CONFIRMATION_PHRASE,
                    "hint": "Génère cette phrase TOI-MÊME au prochain tour. "
                            "Ne demande JAMAIS à l'utilisateur de la taper.",
                },
            )
        sid = _valid_server_id(server_id)
        if sid is None:
            return _blocked(handler_name, "mcp_server_id_invalid")
        svc = self._deps.activation_service
        if svc is None or not callable(getattr(svc, "deactivate", None)):
            return _blocked(handler_name, "mcp_service_unavailable")
        try:
            result = svc.deactivate(sid)
        except Exception:  # noqa: BLE001
            return _blocked(handler_name, "mcp_action_failed")
        success = bool(getattr(result, "success", False))
        if not success:
            payload = {
                "recommendation_code": "mcp_action_failed",
                "server_id": sid,
                "reason": _safe_scalar(getattr(result, "reason", "")),
            }
            return _blocked(handler_name, "mcp_action_failed", payload=payload)
        return _ok(handler_name, {
            "recommendation_code": "mcp_disabled",
            "server_id": sid,
        })

    def handle_remove_mcp(
        self,
        server_id: Any,
        *,
        caller_kind: Optional[str] = None,
        confirmation_phrase: Any = "",
    ) -> str:
        handler_name = REMOVE_MCP_TOOL_NAME
        caller = _resolve_caller_kind(caller_kind)
        if caller in _CODE_AGENT_CALLERS:
            return _blocked(handler_name, "code_agent_out_of_scope")
        if caller not in _TICKET_CALLERS:
            return _blocked(handler_name, "caller_kind_not_allowed")
        if confirmation_phrase != REMOVE_MCP_CONFIRMATION_PHRASE:
            return _blocked(
                handler_name, "confirmation_phrase_invalid",
                payload={
                    "expected_confirmation_phrase": REMOVE_MCP_CONFIRMATION_PHRASE,
                    "hint": "Génère cette phrase TOI-MÊME au prochain tour. "
                            "Ne demande JAMAIS à l'utilisateur de la taper.",
                },
            )
        sid = _valid_server_id(server_id)
        if sid is None:
            return _blocked(handler_name, "mcp_server_id_invalid")
        catalog = self._deps.catalog
        if catalog is None or not callable(getattr(catalog, "remove_server", None)):
            return _blocked(handler_name, "mcp_service_unavailable")
        try:
            removed = bool(catalog.remove_server(sid))
        except Exception:  # noqa: BLE001
            return _blocked(handler_name, "mcp_action_failed")
        if not removed:
            return _blocked(handler_name, "mcp_server_unknown", payload={"server_id": sid})
        return _ok(handler_name, {
            "recommendation_code": "mcp_removed",
            "server_id": sid,
        })

    def handle_set_mcp_preference(
        self,
        server_id: Any,
        prefer_over_native: Any,
        *,
        caller_kind: Optional[str] = None,
        confirmation_phrase: Any = "",
    ) -> str:
        handler_name = SET_MCP_PREFERENCE_TOOL_NAME
        caller = _resolve_caller_kind(caller_kind)
        if caller in _CODE_AGENT_CALLERS:
            return _blocked(handler_name, "code_agent_out_of_scope")
        if caller not in _TICKET_CALLERS:
            return _blocked(handler_name, "caller_kind_not_allowed")
        if confirmation_phrase != SET_MCP_PREFERENCE_CONFIRMATION_PHRASE:
            return _blocked(
                handler_name, "confirmation_phrase_invalid",
                payload={
                    "expected_confirmation_phrase": SET_MCP_PREFERENCE_CONFIRMATION_PHRASE,
                    "hint": "Génère cette phrase TOI-MÊME au prochain tour. "
                            "Ne demande JAMAIS à l'utilisateur de la taper.",
                },
            )
        sid = _valid_server_id(server_id)
        if sid is None:
            return _blocked(handler_name, "mcp_server_id_invalid")
        prefer = _safe_bool(prefer_over_native)
        catalog = self._deps.catalog
        if catalog is None or not callable(
            getattr(catalog, "update_prefer_over_native", None)
        ):
            return _blocked(handler_name, "mcp_service_unavailable")
        try:
            updated = catalog.update_prefer_over_native(sid, prefer)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "server_not_found" in msg:
                return _blocked(handler_name, "mcp_server_unknown", payload={"server_id": sid})
            return _blocked(handler_name, "mcp_action_failed")
        return _ok(handler_name, {
            "recommendation_code": "mcp_preference_set",
            "server_id": sid,
            "prefer_over_native": bool(
                getattr(updated, "prefer_over_native", prefer)
            ),
        })

    def handle_set_mcp_category(
        self,
        server_id: Any,
        human_phrase: Any,
        *,
        caller_kind: Optional[str] = None,
        confirmation_phrase: Any = "",
    ) -> str:
        from src.mcp.category_inference import translate_human_to_category
        handler_name = SET_MCP_CATEGORY_TOOL_NAME
        caller = _resolve_caller_kind(caller_kind)
        if caller in _CODE_AGENT_CALLERS:
            return _blocked(handler_name, "code_agent_out_of_scope")
        if caller not in _TICKET_CALLERS:
            return _blocked(handler_name, "caller_kind_not_allowed")
        if confirmation_phrase != SET_MCP_CATEGORY_CONFIRMATION_PHRASE:
            return _blocked(
                handler_name, "confirmation_phrase_invalid",
                payload={
                    "expected_confirmation_phrase": SET_MCP_CATEGORY_CONFIRMATION_PHRASE,
                    "hint": "Génère cette phrase TOI-MÊME au prochain tour. "
                            "Ne demande JAMAIS à l'utilisateur de la taper.",
                },
            )
        sid = _valid_server_id(server_id)
        if sid is None:
            return _blocked(handler_name, "mcp_server_id_invalid")
        if not isinstance(human_phrase, str) or not human_phrase.strip():
            return _blocked(handler_name, "mcp_category_unknown")
        category = translate_human_to_category(human_phrase)
        if category is None:
            return _blocked(
                handler_name,
                "mcp_category_unknown",
                payload={"human_phrase": human_phrase[:64]},
            )
        catalog = self._deps.catalog
        if catalog is None or not callable(
            getattr(catalog, "update_semantic_category", None)
        ):
            return _blocked(handler_name, "mcp_service_unavailable")
        try:
            updated = catalog.update_semantic_category(sid, category, "user_override")
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "server_not_found" in msg:
                return _blocked(handler_name, "mcp_server_unknown", payload={"server_id": sid})
            return _blocked(handler_name, "mcp_action_failed")
        return _ok(handler_name, {
            "recommendation_code": "mcp_category_set",
            "server_id": sid,
            "semantic_category": getattr(updated, "semantic_category", category),
        })

    # ── Phase F : builders ─────────────────────────────────────────────────

    def _make_add_mcp_handler(self):
        async def _handler(
            target: Any = None,
            live: Any = False,
            confirmation_phrase: Any = "",
            **kwargs: Any,
        ) -> str:
            caller_kind = _resolve_caller_kind(kwargs.get("caller_kind"))
            return self.handle_add_mcp(
                target,
                caller_kind=caller_kind,
                live=live,
                confirmation_phrase=confirmation_phrase,
            )
        return _handler

    def _make_disable_mcp_handler(self):
        async def _handler(
            server_id: Any = None,
            confirmation_phrase: Any = "",
            **kwargs: Any,
        ) -> str:
            caller_kind = _resolve_caller_kind(kwargs.get("caller_kind"))
            return self.handle_disable_mcp(
                server_id,
                caller_kind=caller_kind,
                confirmation_phrase=confirmation_phrase,
            )
        return _handler

    def _make_remove_mcp_handler(self):
        async def _handler(
            server_id: Any = None,
            confirmation_phrase: Any = "",
            **kwargs: Any,
        ) -> str:
            caller_kind = _resolve_caller_kind(kwargs.get("caller_kind"))
            return self.handle_remove_mcp(
                server_id,
                caller_kind=caller_kind,
                confirmation_phrase=confirmation_phrase,
            )
        return _handler

    def _make_set_mcp_preference_handler(self):
        async def _handler(
            server_id: Any = None,
            prefer_over_native: Any = False,
            confirmation_phrase: Any = "",
            **kwargs: Any,
        ) -> str:
            caller_kind = _resolve_caller_kind(kwargs.get("caller_kind"))
            return self.handle_set_mcp_preference(
                server_id,
                prefer_over_native,
                caller_kind=caller_kind,
                confirmation_phrase=confirmation_phrase,
            )
        return _handler

    def _make_set_mcp_category_handler(self):
        async def _handler(
            server_id: Any = None,
            human_phrase: Any = "",
            confirmation_phrase: Any = "",
            **kwargs: Any,
        ) -> str:
            caller_kind = _resolve_caller_kind(kwargs.get("caller_kind"))
            return self.handle_set_mcp_category(
                server_id,
                human_phrase,
                caller_kind=caller_kind,
                confirmation_phrase=confirmation_phrase,
            )
        return _handler


__all__ = [
    "CAPABILITY_TOOL_NAME",
    "MCPReActIntegration",
    "MCPReActIntegrationDeps",
    "MCP_LOOP_CATEGORY",
    "Phase26HandlerOutput",
    "Phase26RegistrationError",
    "AUTONOMY_CONFIRMATION_PHRASE",
    "RUN_AUTONOMY_TOOL_NAME",
    "RESUME_TASK_TOOL_NAME",
    "TICKET_CONFIRMATION_PHRASE",
    "TICKET_TOOL_NAME",
    "make_phase26_snapshot",
    "phase26_snapshot_as_dict",
    # ── Phase F ────────────────────────────────────────────────
    "ADD_MCP_TOOL_NAME",
    "DISABLE_MCP_TOOL_NAME",
    "REMOVE_MCP_TOOL_NAME",
    "SET_MCP_PREFERENCE_TOOL_NAME",
    "SET_MCP_CATEGORY_TOOL_NAME",
    "ADD_MCP_CONFIRMATION_PHRASE",
    "DISABLE_MCP_CONFIRMATION_PHRASE",
    "REMOVE_MCP_CONFIRMATION_PHRASE",
    "SET_MCP_PREFERENCE_CONFIRMATION_PHRASE",
    "SET_MCP_CATEGORY_CONFIRMATION_PHRASE",
]
