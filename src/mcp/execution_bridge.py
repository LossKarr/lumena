"""
Phase 25 — MCP Execution Bridge.

Pont d'exécution entre Phase 24 (planificateur pur) et les orchestrators
existants Phase 18 (`MCPInstallOrchestrator`) et Phase 19
(`MCPActivationService`).

Doctrine Phase 25 v1 :
  - Composant pur testable, lecture seule par défaut (dry_run=True).
  - 3 APIs publiques : `request_action_for_plan`, `execute_after_approval`,
    `describe_action_state`.
  - Création de tickets DÉLÉGUÉE à `InstallOrchestrator.propose_install` et
    `ActivationService.propose_activation` (qui appellent eux-mêmes
    ApprovalQueue avec les args/policy/risk_summary corrects). Phase 25
    n'utilise JAMAIS d'appel direct à un canal de création de ticket.
  - ApprovalQueue strictement lecture seule (`list_pending` + `get`),
    utilisée pour la déduplication et pour l'état des tickets.
  - Phase 25 N'APPROUVE JAMAIS, NE REJETTE JAMAIS un ticket. L'ApprovalResult
    est fourni par le caller (UI Phase 20B-1 ou wrapper futur).
  - Aucune mutation catalog, aucune désactivation, aucun enregistrement
    dynamique de handler, aucun call_tool, aucun lancement de processus
    externe, aucun branchement ReAct.
  - Aucun import dur vers Phase 10/18/19/24 dans le code prod (Protocols
    uniquement). Import direct autorisé seulement dans le fichier de test.
  - Aucun import au module-level de clients HTTP tiers.
  - Aucun cipher direct, aucun déchiffrement, aucun service de secrets dédié.
  - Aucun nom de policy MCP référencé : la doctrine policy reste sous
    le contrôle des orchestrators Phase 18/19.
  - Aucun faux action_id généré : le `approval_ticket_id` provient
    toujours d'un `InstallProposal`/`ActivationProposal` réel ou d'un
    ticket pending existant détecté via `list_pending`.
  - Cross-check fort sur ApprovalResult :
      * decision == APPROVED
      * args est dict
      * args["server_id"] == server_id
      * activation path : args["action"] == "activate"
      * install path : keys Phase 18 minimum présentes
  - Sortie sanitizée par whitelist stricte.
  - Audit local optionnel.
  - kill switches LUMENA_MCP_LIVE / LUMENA_MCP_INSTALL_DISABLED /
    LUMENA_MCP_ACTIVATION_DISABLED lus à chaque appel (hot-reload .env
    cohérent doctrine Phase 20B).
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import SimpleNamespace
from typing import (
    Any,
    Dict,
    List,
    Optional,
    Protocol,
    Tuple,
)


# ══════════════════════════════════════════════════════════════════════════════
# Constantes module
# ══════════════════════════════════════════════════════════════════════════════

_SERVER_ID_MAX = 64

_CALLER_KIND_WHITELIST: frozenset[str] = frozenset({
    "react", "sub_agent", "admin_ui", "test", "autonomous_loop",
})

_RISK_SUMMARY_WHITELIST: frozenset[str] = frozenset({
    "install_required",
    "activation_required",
    "catalog_add_required",
    "local_creation_required",
    "none",
})

_EVIDENCE_WHITELIST: frozenset[str] = frozenset({
    "bridge_plan_id",
    "created_at",
    "dry_run",
    "phase24_decision",
    "phase24_action_kind",
    "mapped_decision_reason_code",
    "target_server_id",
    "catalog_status_observed",
    "dynamic_handlers_present_for_target",
    "live_mode_enabled",
    "install_kill_switch",
    "activation_kill_switch",
    "caller_kind",
    "sources_degraded",
    "which_orchestrator_would_be_called",
    "invoked_orchestrator",
    "execution_outcome_code",
    "risk_summary",
    "proposed_ticket_action_id",
    "dedup_match_pending",
})

_BLOCKER_CODES: frozenset[str] = frozenset({
    "phase24_blocked_or_no_path",
    "catalog_quarantined",
    "needs_install_first",
    "live_mode_disabled",
    "install_kill_switch_active",
    "activation_kill_switch_active",
    "approval_not_granted",
    "approval_result_invalid_shape",
    "approval_server_id_mismatch",
    "approval_action_mismatch",
    "action_id_invalid_format",
    "server_id_invalid_format",
    "install_failed",
    "activation_failed",
    "unexpected_catalog_state",
    "marker_not_found_or_already_decided",
    "no_approval_queue_dep",
    "no_install_orchestrator_dep",
    "no_activation_service_dep",
    "no_catalog_add_orchestrator_dep",
    "no_catalog_dep",
    "no_tool_registry_dep",
    "catalog_proposal_invalid",
    "catalog_add_failed",
})

# Clés requises Phase 18 dans approval_result.args pour install
_INSTALL_REQUIRED_ARG_KEYS: frozenset[str] = frozenset({
    "server_id", "transport", "package_name",
    "package_spec", "version", "trust_score",
})

# Regex Phase 14 réelle pour server_id
_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")
_WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset({
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5",
    "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5",
    "lpt6", "lpt7", "lpt8", "lpt9",
})

# UUID4 hex 32
_UUID4_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")

# Decision string APPROVED (Phase 10 ApprovalDecision.APPROVED.value)
_APPROVED_DECISION_STR = "approved"

# Catalog status values (Phase 14)
_CATALOG_STATUS_VALUES: frozenset[str] = frozenset({
    "declared", "installed", "active", "quarantined", "removed",
})


# ══════════════════════════════════════════════════════════════════════════════
# Helpers déterministes
# ══════════════════════════════════════════════════════════════════════════════


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_bridge_plan_id() -> str:
    return uuid.uuid4().hex


def _sanitize_caller_kind(raw: Any) -> str:
    if isinstance(raw, str) and raw in _CALLER_KIND_WHITELIST:
        return raw
    return "unknown"


def _is_valid_server_id(raw: Any) -> bool:
    """Phase 14 réelle : regex + refus de "..", "/", "\\\\",
    + refus Windows reserved (con, prn, aux, nul, com1-9, lpt1-9)
    avec stem split sur "." pour couvrir aussi "con.txt".
    """
    if not isinstance(raw, str):
        return False
    if not _SERVER_ID_RE.match(raw):
        return False
    if ".." in raw or "/" in raw or "\\" in raw:
        return False
    stem = raw.split(".", 1)[0]
    if stem in _WINDOWS_RESERVED_NAMES:
        return False
    return True


def _is_valid_action_id(raw: Any) -> bool:
    """UUID4 strict Phase 10 :
      - regex 32 hex lowercase
      - uuid.UUID(raw) parseable
      - parsed.version == 4
      - parsed.hex == raw (rejette uppercase, tirets, etc.)
    """
    if not isinstance(raw, str):
        return False
    if not _UUID4_HEX32_RE.match(raw):
        return False
    try:
        parsed = uuid.UUID(raw)
    except Exception:
        return False
    if parsed.version != 4:
        return False
    if parsed.hex != raw:
        return False
    return True


def _env_bool(name: str, default: str = "0") -> bool:
    v = os.environ.get(name, default).strip().lower()
    return v in ("1", "true", "yes", "on")


def _live_mode_enabled() -> bool:
    return _env_bool("LUMENA_MCP_LIVE", "0")


def _install_kill_switch_active() -> bool:
    return _env_bool("LUMENA_MCP_INSTALL_DISABLED", "0")


def _activation_kill_switch_active() -> bool:
    return _env_bool("LUMENA_MCP_ACTIVATION_DISABLED", "0")


def _safe_call(fn, *args, **kwargs) -> Tuple[bool, Any]:
    try:
        return True, fn(*args, **kwargs)
    except Exception:
        return False, None


def _read_catalog_status(catalog_read: Any, server_id: str) -> str:
    if catalog_read is None:
        return ""
    ok, entry = _safe_call(catalog_read.get_server, server_id)
    if not ok or entry is None:
        return "unknown"
    status = getattr(entry, "status", None)
    val = getattr(status, "value", None)
    if isinstance(val, str) and val in _CATALOG_STATUS_VALUES:
        return val
    if isinstance(status, str) and status in _CATALOG_STATUS_VALUES:
        return status
    return "unknown"


def _has_handlers_for_server(
    tool_registry_read: Any, server_id: str,
) -> bool:
    if tool_registry_read is None:
        return False
    ok, names = _safe_call(tool_registry_read.list_dynamic_handlers)
    if not ok or not isinstance(names, list):
        return False
    for name in names:
        if not isinstance(name, str):
            continue
        ok_prov, prov = _safe_call(
            tool_registry_read.get_dynamic_handler_provenance, name
        )
        if not ok_prov or not isinstance(prov, dict):
            continue
        sid = prov.get("server_id")
        if isinstance(sid, str) and sid == server_id:
            return True
    return False


def _find_pending_ticket(
    approval_queue_read: Any, tool_name: str,
) -> Optional[str]:
    """Retourne l'action_id (UUID4 hex 32) du premier ticket pending dont
    le tool_name matche exactement. None sinon.
    """
    if approval_queue_read is None:
        return None
    ok, pending = _safe_call(approval_queue_read.list_pending)
    if not ok or not isinstance(pending, list):
        return None
    for ticket in pending:
        tn = getattr(ticket, "tool_name", None)
        tid = getattr(ticket, "id", None)
        if (
            isinstance(tn, str) and tn == tool_name
            and isinstance(tid, str) and _UUID4_HEX32_RE.match(tid)
        ):
            return tid
    return None


def _read_phase24_decision(plan: Any) -> str:
    if plan is None:
        return ""
    dec = getattr(plan, "decision", None)
    val = getattr(dec, "value", None)
    if isinstance(val, str):
        return val
    if isinstance(dec, str):
        return dec
    return ""


def _read_phase24_action_kind(plan: Any) -> str:
    if plan is None:
        return ""
    action = getattr(plan, "action", None)
    if action is None:
        return ""
    kind = getattr(action, "kind", None)
    val = getattr(kind, "value", None)
    if isinstance(val, str):
        return val
    if isinstance(kind, str):
        return kind
    return ""


def _read_phase24_target_server_id(plan: Any) -> Optional[str]:
    action = getattr(plan, "action", None)
    if action is None:
        return None
    sid = getattr(action, "target_server_id", None)
    if isinstance(sid, str):
        return sid
    return None


def _read_phase24_action_attr(plan: Any, name: str) -> Any:
    action = getattr(plan, "action", None)
    if action is None:
        return None
    return getattr(action, name, None)


def _catalog_add_input_from_phase24(plan: Any) -> Any:
    server_id = _read_phase24_target_server_id(plan)
    display_name = _read_phase24_action_attr(plan, "catalog_display_name")
    package_spec = _read_phase24_action_attr(plan, "catalog_package_spec")
    version = _read_phase24_action_attr(plan, "catalog_version")
    trust_score = _read_phase24_action_attr(plan, "catalog_trust_score")
    if not _is_valid_server_id(server_id):
        return None
    if not isinstance(display_name, str) or not display_name.strip():
        return None
    if not isinstance(package_spec, str) or not package_spec.strip():
        return None
    if version is not None and not isinstance(version, str):
        return None
    if trust_score is not None:
        if isinstance(trust_score, bool) or not isinstance(trust_score, int):
            return None
        if not (0 <= trust_score <= 100):
            return None
    # Phase I-8 (Fix AC) : tags optionnels — invalides → None (jamais bloquant).
    raw_tags = _read_phase24_action_attr(plan, "catalog_capability_tags")
    capability_tags = None
    if isinstance(raw_tags, (list, tuple)) and raw_tags:
        cleaned = tuple(
            t for t in raw_tags if isinstance(t, str) and t
        )
        capability_tags = cleaned if cleaned else None
    return SimpleNamespace(
        server_id=server_id,
        display_name=display_name,
        package_spec=package_spec,
        version=version,
        trust_score=trust_score,
        capability_tags=capability_tags,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Protocols read + invoke (signatures Phase 10/18/19 réelles)
# ══════════════════════════════════════════════════════════════════════════════


class InstallOrchestratorLike(Protocol):
    """Phase 18 — propose_install + execute_approved_install."""
    def propose_install(
        self, server_id: Any, *, caller_kind: str = "silent",
    ) -> Any: ...

    def execute_approved_install(
        self, server_id: Any, approval_result: Any,
    ) -> Any: ...


class ActivationServiceLike(Protocol):
    """Phase 19 — propose_activation + activate."""
    def propose_activation(
        self, server_id: Any, *, caller_kind: str = "silent",
    ) -> Any: ...

    def activate(
        self, server_id: Any, approval_result: Optional[Any] = None,
    ) -> Any: ...


class CatalogAddOrchestratorLike(Protocol):
    """Catalog add ticket proposal + approved execution."""
    def propose_catalog_add(
        self,
        proposal: Any,
        *,
        caller_kind: str = "react",
        dry_run: bool = True,
    ) -> Any: ...

    def execute_approved_catalog_add(
        self,
        server_id: str,
        approval_result: Any,
        *,
        dry_run: bool = True,
    ) -> Any: ...


class ApprovalQueueReadLike(Protocol):
    """Phase 10 — list_pending + get UNIQUEMENT.

    Aucune méthode de création/mutation de ticket exposée côté Phase 25.
    """
    def list_pending(self) -> List[Any]: ...
    def get(self, action_id: str) -> Optional[Any]: ...


class CatalogReadLike(Protocol):
    def get_server(self, server_id: str) -> Optional[Any]: ...
    def list_servers(self, include_removed: bool = False) -> List[Any]: ...


class ToolRegistryReadLike(Protocol):
    def list_dynamic_handlers(self) -> List[str]: ...
    def is_dynamic_handler(self, name: str) -> bool: ...
    def get_dynamic_handler_provenance(
        self, name: str,
    ) -> Optional[Dict[str, Any]]: ...


# ══════════════════════════════════════════════════════════════════════════════
# Modèles immutables
# ══════════════════════════════════════════════════════════════════════════════


class Phase25BridgeDecision(str, Enum):
    NO_TICKET_NEEDED = "no_ticket_needed"
    TICKET_WOULD_BE_PROPOSED = "ticket_would_be_proposed"
    TICKET_PROPOSED = "ticket_proposed"
    TICKET_DESCRIPTIVE_ONLY = "ticket_descriptive_only"
    EXECUTION_WOULD_HAPPEN = "execution_would_happen"
    EXECUTED_SUCCESS_CATALOG_ADD = "executed_success_catalog_add"
    EXECUTED_SUCCESS_INSTALL = "executed_success_install"
    EXECUTED_SUCCESS_ACTIVATE = "executed_success_activate"
    EXECUTED_FAILURE = "executed_failure"
    ALREADY_APPLIED = "already_applied"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"


class Phase25BridgeActionKind(str, Enum):
    NONE = "none"
    PROPOSE_CATALOG_ADD = "propose_catalog_add"
    PROPOSE_INSTALL = "propose_install"
    PROPOSE_ACTIVATION = "propose_activation"
    EXECUTE_CATALOG_ADD = "execute_catalog_add"
    EXECUTE_INSTALL = "execute_install"
    EXECUTE_ACTIVATION = "execute_activation"


@dataclass(frozen=True)
class Phase25BridgeAction:
    kind: Phase25BridgeActionKind
    target_server_id: Optional[str]
    risk_summary: str
    proposed_ticket_action_id: Optional[str]
    invoked_orchestrator: str


@dataclass(frozen=True)
class Phase25BridgePlanBlocker:
    blocker_code: str
    target_server_id: Optional[str]
    details_count: int


@dataclass(frozen=True)
class MCPExecutionBridgeDeps:
    catalog_add_orchestrator: Optional[CatalogAddOrchestratorLike] = None
    install_orchestrator: Optional[InstallOrchestratorLike] = None
    activation_service: Optional[ActivationServiceLike] = None
    approval_queue_read: Optional[ApprovalQueueReadLike] = None
    catalog_read: Optional[CatalogReadLike] = None
    tool_registry_read: Optional[ToolRegistryReadLike] = None


@dataclass(frozen=True)
class Phase25BridgePlan:
    bridge_plan_id: str
    decision: Phase25BridgeDecision
    action: Phase25BridgeAction
    dry_run: bool
    blockers: Tuple[Phase25BridgePlanBlocker, ...]
    evidence: Dict[str, Any]
    created_at: str


def _none_action() -> Phase25BridgeAction:
    return Phase25BridgeAction(
        kind=Phase25BridgeActionKind.NONE,
        target_server_id=None,
        risk_summary="none",
        proposed_ticket_action_id=None,
        invoked_orchestrator="",
    )


# ══════════════════════════════════════════════════════════════════════════════
# MCPExecutionBridge — stateless
# ══════════════════════════════════════════════════════════════════════════════


class MCPExecutionBridge:
    """Phase 25 — pont d'exécution entre Phase 24 et orchestrators."""

    def __init__(
        self,
        deps: MCPExecutionBridgeDeps,
        *,
        audit_log_path: Optional[Path] = None,
    ) -> None:
        if not isinstance(deps, MCPExecutionBridgeDeps):
            raise TypeError(
                "deps must be a MCPExecutionBridgeDeps instance"
            )
        if audit_log_path is not None and not isinstance(
            audit_log_path, Path
        ):
            raise TypeError("audit_log_path must be a pathlib.Path or None")
        self._deps = deps
        self._audit_log_path = audit_log_path

    # ── Public APIs ────────────────────────────────────────────────────────

    def request_action_for_plan(
        self,
        phase24_plan: Any,
        *,
        caller_kind: str,
        dry_run: bool = True,
    ) -> Phase25BridgePlan:
        bridge_plan_id = _new_bridge_plan_id()
        created_at = _now_utc_iso()
        caller_kind_clean = _sanitize_caller_kind(caller_kind)
        sources_degraded: List[str] = []

        phase24_dec = _read_phase24_decision(phase24_plan)
        phase24_kind = _read_phase24_action_kind(phase24_plan)
        target_sid = _read_phase24_target_server_id(phase24_plan)

        live_enabled = _live_mode_enabled()
        install_ks = _install_kill_switch_active()
        activation_ks = _activation_kill_switch_active()

        def build(
            decision: Phase25BridgeDecision,
            action: Phase25BridgeAction,
            blockers: List[Phase25BridgePlanBlocker],
            extra_evidence: Optional[Dict[str, Any]] = None,
        ) -> Phase25BridgePlan:
            evidence = {
                "bridge_plan_id": bridge_plan_id,
                "created_at": created_at,
                "dry_run": dry_run,
                "phase24_decision": phase24_dec,
                "phase24_action_kind": phase24_kind,
                "mapped_decision_reason_code": decision.value,
                "target_server_id": action.target_server_id,
                "caller_kind": caller_kind_clean,
                "sources_degraded": sorted(set(sources_degraded)),
                "live_mode_enabled": live_enabled,
                "install_kill_switch": install_ks,
                "activation_kill_switch": activation_ks,
                "risk_summary": action.risk_summary,
                "proposed_ticket_action_id": action.proposed_ticket_action_id,
            }
            if extra_evidence:
                evidence.update(extra_evidence)
            return self._build_plan(
                bridge_plan_id=bridge_plan_id,
                created_at=created_at,
                decision=decision,
                action=action,
                dry_run=dry_run,
                blockers=blockers,
                evidence=evidence,
                caller_kind=caller_kind_clean,
            )

        # ── Mapping Phase 24 → Phase 25 ───────────────────────────────────

        if phase24_dec == "ready_to_use_existing_capability":
            return build(
                Phase25BridgeDecision.NO_TICKET_NEEDED,
                _none_action(),
                [],
            )

        if phase24_dec == "needs_install_approval":
            return self._handle_needs_install(
                target_sid=target_sid,
                caller_kind=caller_kind_clean,
                dry_run=dry_run,
                live_enabled=live_enabled,
                install_ks=install_ks,
                sources_degraded=sources_degraded,
                build=build,
            )

        if phase24_dec == "needs_activation_approval":
            return self._handle_needs_activation(
                target_sid=target_sid,
                caller_kind=caller_kind_clean,
                dry_run=dry_run,
                live_enabled=live_enabled,
                activation_ks=activation_ks,
                sources_degraded=sources_degraded,
                build=build,
            )

        if phase24_dec == "needs_catalog_approval":
            return self._handle_needs_catalog_add(
                phase24_plan=phase24_plan,
                target_sid=target_sid,
                caller_kind=caller_kind_clean,
                dry_run=dry_run,
                live_enabled=live_enabled,
                sources_degraded=sources_degraded,
                build=build,
            )

        if phase24_dec == "needs_local_creation":
            return build(
                Phase25BridgeDecision.TICKET_DESCRIPTIVE_ONLY,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=target_sid,
                    risk_summary="local_creation_required",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [],
            )

        if phase24_dec == "waiting_approval":
            return build(
                Phase25BridgeDecision.WAITING_APPROVAL,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=target_sid,
                    risk_summary="none",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [],
            )

        # blocked / no_safe_path / unknown
        return build(
            Phase25BridgeDecision.BLOCKED,
            _none_action(),
            [Phase25BridgePlanBlocker(
                blocker_code="phase24_blocked_or_no_path",
                target_server_id=target_sid,
                details_count=1,
            )],
        )

    def execute_after_approval(
        self,
        action_id: str,
        approval_result: Any,
        server_id: str,
        *,
        caller_kind: str,
        dry_run: bool = True,
    ) -> Phase25BridgePlan:
        bridge_plan_id = _new_bridge_plan_id()
        created_at = _now_utc_iso()
        caller_kind_clean = _sanitize_caller_kind(caller_kind)
        sources_degraded: List[str] = []

        live_enabled = _live_mode_enabled()
        install_ks = _install_kill_switch_active()
        activation_ks = _activation_kill_switch_active()

        def build_exec(
            decision: Phase25BridgeDecision,
            action: Phase25BridgeAction,
            blockers: List[Phase25BridgePlanBlocker],
            extra_evidence: Optional[Dict[str, Any]] = None,
        ) -> Phase25BridgePlan:
            evidence = {
                "bridge_plan_id": bridge_plan_id,
                "created_at": created_at,
                "dry_run": dry_run,
                "mapped_decision_reason_code": decision.value,
                "target_server_id": action.target_server_id,
                "caller_kind": caller_kind_clean,
                "sources_degraded": sorted(set(sources_degraded)),
                "live_mode_enabled": live_enabled,
                "install_kill_switch": install_ks,
                "activation_kill_switch": activation_ks,
                "risk_summary": action.risk_summary,
                "invoked_orchestrator": action.invoked_orchestrator,
            }
            if extra_evidence:
                evidence.update(extra_evidence)
            return self._build_plan(
                bridge_plan_id=bridge_plan_id,
                created_at=created_at,
                decision=decision,
                action=action,
                dry_run=dry_run,
                blockers=blockers,
                evidence=evidence,
                caller_kind=caller_kind_clean,
            )

        # ── 1. Validate action_id ─────────────────────────────────────────
        if not _is_valid_action_id(action_id):
            return build_exec(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="action_id_invalid_format",
                    target_server_id=None,
                    details_count=1,
                )],
            )

        # ── 2. Validate server_id ─────────────────────────────────────────
        if not _is_valid_server_id(server_id):
            return build_exec(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="server_id_invalid_format",
                    target_server_id=None,
                    details_count=1,
                )],
            )

        # ── 3. Validate approval_result ───────────────────────────────────
        ar_decision = getattr(approval_result, "decision", None)
        ar_decision_val = getattr(ar_decision, "value", None)
        decision_str = (
            ar_decision_val if isinstance(ar_decision_val, str)
            else (ar_decision if isinstance(ar_decision, str) else "")
        )
        if not decision_str:
            return build_exec(
                Phase25BridgeDecision.BLOCKED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=server_id,
                    risk_summary="none",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [Phase25BridgePlanBlocker(
                    blocker_code="approval_result_invalid_shape",
                    target_server_id=server_id, details_count=1,
                )],
            )
        if decision_str.lower() != _APPROVED_DECISION_STR:
            return build_exec(
                Phase25BridgeDecision.BLOCKED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=server_id,
                    risk_summary="none",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [Phase25BridgePlanBlocker(
                    blocker_code="approval_not_granted",
                    target_server_id=server_id, details_count=1,
                )],
            )

        ar_args = getattr(approval_result, "args", None)
        if not isinstance(ar_args, dict):
            return build_exec(
                Phase25BridgeDecision.BLOCKED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=server_id,
                    risk_summary="none",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [Phase25BridgePlanBlocker(
                    blocker_code="approval_result_invalid_shape",
                    target_server_id=server_id, details_count=1,
                )],
            )

        if ar_args.get("server_id") != server_id:
            return build_exec(
                Phase25BridgeDecision.BLOCKED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=server_id,
                    risk_summary="none",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [Phase25BridgePlanBlocker(
                    blocker_code="approval_server_id_mismatch",
                    target_server_id=server_id, details_count=1,
                )],
            )

        # ── 4. Catalog status + handlers check ────────────────────────────
        catalog_status = _read_catalog_status(
            self._deps.catalog_read, server_id
        )
        if self._deps.catalog_read is None:
            sources_degraded.append("catalog_read")
            return build_exec(
                Phase25BridgeDecision.BLOCKED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=server_id,
                    risk_summary="none",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [Phase25BridgePlanBlocker(
                    blocker_code="no_catalog_dep",
                    target_server_id=server_id, details_count=1,
                )],
            )

        if ar_args.get("action") == "catalog_add":
            if catalog_status in ("declared", "installed", "active"):
                return build_exec(
                    Phase25BridgeDecision.ALREADY_APPLIED,
                    Phase25BridgeAction(
                        kind=Phase25BridgeActionKind.NONE,
                        target_server_id=server_id,
                        risk_summary="catalog_add_required",
                        proposed_ticket_action_id=action_id,
                        invoked_orchestrator="",
                    ),
                    [],
                    extra_evidence={
                        "catalog_status_observed": catalog_status,
                        "execution_outcome_code": "already_applied",
                    },
                )
            if catalog_status in ("quarantined", "removed"):
                return build_exec(
                    Phase25BridgeDecision.BLOCKED,
                    Phase25BridgeAction(
                        kind=Phase25BridgeActionKind.NONE,
                        target_server_id=server_id,
                        risk_summary="catalog_add_required",
                        proposed_ticket_action_id=None,
                        invoked_orchestrator="",
                    ),
                    [Phase25BridgePlanBlocker(
                        blocker_code="unexpected_catalog_state",
                        target_server_id=server_id,
                        details_count=1,
                    )],
                    extra_evidence={"catalog_status_observed": catalog_status},
                )
            if dry_run:
                return build_exec(
                    Phase25BridgeDecision.EXECUTION_WOULD_HAPPEN,
                    Phase25BridgeAction(
                        kind=Phase25BridgeActionKind.EXECUTE_CATALOG_ADD,
                        target_server_id=server_id,
                        risk_summary="catalog_add_required",
                        proposed_ticket_action_id=action_id,
                        invoked_orchestrator="",
                    ),
                    [],
                    extra_evidence={
                        "catalog_status_observed": catalog_status,
                        "which_orchestrator_would_be_called": "catalog_add",
                        "execution_outcome_code": "not_executed",
                    },
                )
            if not live_enabled:
                return build_exec(
                    Phase25BridgeDecision.BLOCKED,
                    _none_action(),
                    [Phase25BridgePlanBlocker(
                        blocker_code="live_mode_disabled",
                        target_server_id=server_id,
                        details_count=1,
                    )],
                    extra_evidence={"catalog_status_observed": catalog_status},
                )
            if self._deps.catalog_add_orchestrator is None:
                return build_exec(
                    Phase25BridgeDecision.BLOCKED,
                    _none_action(),
                    [Phase25BridgePlanBlocker(
                        blocker_code="no_catalog_add_orchestrator_dep",
                        target_server_id=server_id,
                        details_count=1,
                    )],
                    extra_evidence={"catalog_status_observed": catalog_status},
                )
            ok, result = _safe_call(
                self._deps.catalog_add_orchestrator.execute_approved_catalog_add,
                server_id,
                approval_result,
                dry_run=False,
            )
            success = ok and bool(getattr(result, "success", False))
            if success:
                return build_exec(
                    Phase25BridgeDecision.EXECUTED_SUCCESS_CATALOG_ADD,
                    Phase25BridgeAction(
                        kind=Phase25BridgeActionKind.EXECUTE_CATALOG_ADD,
                        target_server_id=server_id,
                        risk_summary="catalog_add_required",
                        proposed_ticket_action_id=action_id,
                        invoked_orchestrator="catalog_add",
                    ),
                    [],
                    extra_evidence={
                        "catalog_status_observed": catalog_status,
                        "execution_outcome_code": "success_catalog_add",
                    },
                )
            return build_exec(
                Phase25BridgeDecision.EXECUTED_FAILURE,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.EXECUTE_CATALOG_ADD,
                    target_server_id=server_id,
                    risk_summary="catalog_add_required",
                    proposed_ticket_action_id=action_id,
                    invoked_orchestrator="catalog_add",
                ),
                [Phase25BridgePlanBlocker(
                    blocker_code="catalog_add_failed",
                    target_server_id=server_id,
                    details_count=1,
                )],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "execution_outcome_code": "failure_catalog_add",
                },
            )

        handlers_present = _has_handlers_for_server(
            self._deps.tool_registry_read, server_id
        )

        if catalog_status == "quarantined":
            return build_exec(
                Phase25BridgeDecision.BLOCKED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=server_id,
                    risk_summary="none",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [Phase25BridgePlanBlocker(
                    blocker_code="catalog_quarantined",
                    target_server_id=server_id, details_count=1,
                )],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "dynamic_handlers_present_for_target": handlers_present,
                },
            )

        if catalog_status == "active":
            return build_exec(
                Phase25BridgeDecision.ALREADY_APPLIED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=server_id,
                    risk_summary="none",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "dynamic_handlers_present_for_target": handlers_present,
                    "execution_outcome_code": "already_applied",
                },
            )

        if catalog_status == "installed" and handlers_present:
            return build_exec(
                Phase25BridgeDecision.ALREADY_APPLIED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=server_id,
                    risk_summary="none",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "dynamic_handlers_present_for_target": handlers_present,
                    "execution_outcome_code": "already_applied",
                },
            )

        # ── 5. Décider orchestrator cible ────────────────────────────────
        if catalog_status == "declared":
            target_orch = "install"
        elif catalog_status == "installed":
            target_orch = "activation"
        else:
            # "unknown" / "removed" / "" → état inattendu
            return build_exec(
                Phase25BridgeDecision.BLOCKED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=server_id,
                    risk_summary="none",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [Phase25BridgePlanBlocker(
                    blocker_code="unexpected_catalog_state",
                    target_server_id=server_id, details_count=1,
                )],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "dynamic_handlers_present_for_target": handlers_present,
                },
            )

        # ── 6. Cross-check args spécifiques au chemin ────────────────────
        if target_orch == "activation":
            if ar_args.get("action") != "activate":
                return build_exec(
                    Phase25BridgeDecision.BLOCKED,
                    Phase25BridgeAction(
                        kind=Phase25BridgeActionKind.NONE,
                        target_server_id=server_id,
                        risk_summary="none",
                        proposed_ticket_action_id=None,
                        invoked_orchestrator="",
                    ),
                    [Phase25BridgePlanBlocker(
                        blocker_code="approval_action_mismatch",
                        target_server_id=server_id, details_count=1,
                    )],
                    extra_evidence={
                        "catalog_status_observed": catalog_status,
                        "dynamic_handlers_present_for_target": handlers_present,
                    },
                )
        else:  # install
            # Vérifier présence des clés Phase 18 attendues (pas leur valeur)
            missing = [
                k for k in _INSTALL_REQUIRED_ARG_KEYS
                if k not in ar_args
            ]
            if missing:
                return build_exec(
                    Phase25BridgeDecision.BLOCKED,
                    Phase25BridgeAction(
                        kind=Phase25BridgeActionKind.NONE,
                        target_server_id=server_id,
                        risk_summary="none",
                        proposed_ticket_action_id=None,
                        invoked_orchestrator="",
                    ),
                    [Phase25BridgePlanBlocker(
                        blocker_code="approval_result_invalid_shape",
                        target_server_id=server_id,
                        details_count=len(missing),
                    )],
                    extra_evidence={
                        "catalog_status_observed": catalog_status,
                        "dynamic_handlers_present_for_target": handlers_present,
                    },
                )

        # ── 7. dry_run : describe only ────────────────────────────────────
        if dry_run:
            return build_exec(
                Phase25BridgeDecision.EXECUTION_WOULD_HAPPEN,
                Phase25BridgeAction(
                    kind=(
                        Phase25BridgeActionKind.EXECUTE_INSTALL
                        if target_orch == "install"
                        else Phase25BridgeActionKind.EXECUTE_ACTIVATION
                    ),
                    target_server_id=server_id,
                    risk_summary=(
                        "install_required" if target_orch == "install"
                        else "activation_required"
                    ),
                    proposed_ticket_action_id=action_id,
                    invoked_orchestrator="",
                ),
                [],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "dynamic_handlers_present_for_target": handlers_present,
                    "which_orchestrator_would_be_called": target_orch,
                    "execution_outcome_code": "not_executed",
                },
            )

        # ── 8. Live mode + kill switches ──────────────────────────────────
        if not live_enabled:
            return build_exec(
                Phase25BridgeDecision.BLOCKED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=server_id,
                    risk_summary="none",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [Phase25BridgePlanBlocker(
                    blocker_code="live_mode_disabled",
                    target_server_id=server_id, details_count=1,
                )],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "dynamic_handlers_present_for_target": handlers_present,
                },
            )

        if target_orch == "install":
            if install_ks:
                return build_exec(
                    Phase25BridgeDecision.BLOCKED,
                    _none_action(),
                    [Phase25BridgePlanBlocker(
                        blocker_code="install_kill_switch_active",
                        target_server_id=server_id, details_count=1,
                    )],
                    extra_evidence={
                        "catalog_status_observed": catalog_status,
                        "dynamic_handlers_present_for_target": handlers_present,
                    },
                )
            if self._deps.install_orchestrator is None:
                return build_exec(
                    Phase25BridgeDecision.BLOCKED,
                    _none_action(),
                    [Phase25BridgePlanBlocker(
                        blocker_code="no_install_orchestrator_dep",
                        target_server_id=server_id, details_count=1,
                    )],
                    extra_evidence={
                        "catalog_status_observed": catalog_status,
                    },
                )
            ok, result = _safe_call(
                self._deps.install_orchestrator.execute_approved_install,
                server_id, approval_result,
            )
            success = (
                ok and bool(getattr(result, "success", False))
            )
            if success:
                return build_exec(
                    Phase25BridgeDecision.EXECUTED_SUCCESS_INSTALL,
                    Phase25BridgeAction(
                        kind=Phase25BridgeActionKind.EXECUTE_INSTALL,
                        target_server_id=server_id,
                        risk_summary="install_required",
                        proposed_ticket_action_id=action_id,
                        invoked_orchestrator="install",
                    ),
                    [],
                    extra_evidence={
                        "catalog_status_observed": catalog_status,
                        "dynamic_handlers_present_for_target": handlers_present,
                        "execution_outcome_code": "success_install",
                    },
                )
            return build_exec(
                Phase25BridgeDecision.EXECUTED_FAILURE,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.EXECUTE_INSTALL,
                    target_server_id=server_id,
                    risk_summary="install_required",
                    proposed_ticket_action_id=action_id,
                    invoked_orchestrator="install",
                ),
                [Phase25BridgePlanBlocker(
                    blocker_code="install_failed",
                    target_server_id=server_id, details_count=1,
                )],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "dynamic_handlers_present_for_target": handlers_present,
                    "execution_outcome_code": "failure",
                },
            )

        # target_orch == "activation"
        if activation_ks:
            return build_exec(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="activation_kill_switch_active",
                    target_server_id=server_id, details_count=1,
                )],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "dynamic_handlers_present_for_target": handlers_present,
                },
            )
        if self._deps.activation_service is None:
            return build_exec(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="no_activation_service_dep",
                    target_server_id=server_id, details_count=1,
                )],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                },
            )
        ok, result = _safe_call(
            self._deps.activation_service.activate,
            server_id, approval_result,
        )
        success = (
            ok and bool(getattr(result, "success", False))
        )
        if success:
            return build_exec(
                Phase25BridgeDecision.EXECUTED_SUCCESS_ACTIVATE,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.EXECUTE_ACTIVATION,
                    target_server_id=server_id,
                    risk_summary="activation_required",
                    proposed_ticket_action_id=action_id,
                    invoked_orchestrator="activation",
                ),
                [],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "dynamic_handlers_present_for_target": handlers_present,
                    "execution_outcome_code": "success_activate",
                },
            )
        return build_exec(
            Phase25BridgeDecision.EXECUTED_FAILURE,
            Phase25BridgeAction(
                kind=Phase25BridgeActionKind.EXECUTE_ACTIVATION,
                target_server_id=server_id,
                risk_summary="activation_required",
                proposed_ticket_action_id=action_id,
                invoked_orchestrator="activation",
            ),
            [Phase25BridgePlanBlocker(
                blocker_code="activation_failed",
                target_server_id=server_id, details_count=1,
            )],
            extra_evidence={
                "catalog_status_observed": catalog_status,
                "dynamic_handlers_present_for_target": handlers_present,
                "execution_outcome_code": "failure",
            },
        )

    def describe_action_state(
        self, action_id: str, *, caller_kind: str,
    ) -> Phase25BridgePlan:
        bridge_plan_id = _new_bridge_plan_id()
        created_at = _now_utc_iso()
        caller_kind_clean = _sanitize_caller_kind(caller_kind)
        sources_degraded: List[str] = []

        if not _is_valid_action_id(action_id):
            return self._build_plan(
                bridge_plan_id=bridge_plan_id,
                created_at=created_at,
                decision=Phase25BridgeDecision.BLOCKED,
                action=_none_action(),
                dry_run=True,
                blockers=[Phase25BridgePlanBlocker(
                    blocker_code="action_id_invalid_format",
                    target_server_id=None, details_count=1,
                )],
                evidence={
                    "bridge_plan_id": bridge_plan_id,
                    "created_at": created_at,
                    "dry_run": True,
                    "caller_kind": caller_kind_clean,
                    "sources_degraded": [],
                    "mapped_decision_reason_code":
                        Phase25BridgeDecision.BLOCKED.value,
                },
                caller_kind=caller_kind_clean,
            )

        aq = self._deps.approval_queue_read
        if aq is None:
            return self._build_plan(
                bridge_plan_id=bridge_plan_id,
                created_at=created_at,
                decision=Phase25BridgeDecision.BLOCKED,
                action=_none_action(),
                dry_run=True,
                blockers=[Phase25BridgePlanBlocker(
                    blocker_code="no_approval_queue_dep",
                    target_server_id=None, details_count=1,
                )],
                evidence={
                    "bridge_plan_id": bridge_plan_id,
                    "created_at": created_at,
                    "dry_run": True,
                    "caller_kind": caller_kind_clean,
                    "sources_degraded": [],
                    "mapped_decision_reason_code":
                        Phase25BridgeDecision.BLOCKED.value,
                    "proposed_ticket_action_id": action_id,
                },
                caller_kind=caller_kind_clean,
            )

        ok_list, pending = _safe_call(aq.list_pending)
        if not ok_list or not isinstance(pending, list):
            sources_degraded.append("approval_queue_read")
            pending = []
        for ticket in pending:
            tid = getattr(ticket, "id", None)
            if tid == action_id:
                return self._build_plan(
                    bridge_plan_id=bridge_plan_id,
                    created_at=created_at,
                    decision=Phase25BridgeDecision.WAITING_APPROVAL,
                    action=Phase25BridgeAction(
                        kind=Phase25BridgeActionKind.NONE,
                        target_server_id=None,
                        risk_summary="none",
                        proposed_ticket_action_id=action_id,
                        invoked_orchestrator="",
                    ),
                    dry_run=True,
                    blockers=[],
                    evidence={
                        "bridge_plan_id": bridge_plan_id,
                        "created_at": created_at,
                        "dry_run": True,
                        "caller_kind": caller_kind_clean,
                        "sources_degraded": sorted(set(sources_degraded)),
                        "mapped_decision_reason_code":
                            Phase25BridgeDecision.WAITING_APPROVAL.value,
                        "proposed_ticket_action_id": action_id,
                    },
                    caller_kind=caller_kind_clean,
                )

        ok_get, ticket = _safe_call(aq.get, action_id)
        if not ok_get:
            sources_degraded.append("approval_queue_read")
        if ok_get and ticket is not None:
            return self._build_plan(
                bridge_plan_id=bridge_plan_id,
                created_at=created_at,
                decision=Phase25BridgeDecision.WAITING_APPROVAL,
                action=Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=None,
                    risk_summary="none",
                    proposed_ticket_action_id=action_id,
                    invoked_orchestrator="",
                ),
                dry_run=True,
                blockers=[],
                evidence={
                    "bridge_plan_id": bridge_plan_id,
                    "created_at": created_at,
                    "dry_run": True,
                    "caller_kind": caller_kind_clean,
                    "sources_degraded": sorted(set(sources_degraded)),
                    "mapped_decision_reason_code":
                        Phase25BridgeDecision.WAITING_APPROVAL.value,
                    "proposed_ticket_action_id": action_id,
                },
                caller_kind=caller_kind_clean,
            )

        return self._build_plan(
            bridge_plan_id=bridge_plan_id,
            created_at=created_at,
            decision=Phase25BridgeDecision.BLOCKED,
            action=_none_action(),
            dry_run=True,
            blockers=[Phase25BridgePlanBlocker(
                blocker_code="marker_not_found_or_already_decided",
                target_server_id=None, details_count=1,
            )],
            evidence={
                "bridge_plan_id": bridge_plan_id,
                "created_at": created_at,
                "dry_run": True,
                "caller_kind": caller_kind_clean,
                "sources_degraded": sorted(set(sources_degraded)),
                "mapped_decision_reason_code":
                    Phase25BridgeDecision.BLOCKED.value,
                "proposed_ticket_action_id": action_id,
            },
            caller_kind=caller_kind_clean,
        )

    # ── Handlers internes pour request_action_for_plan ─────────────────────

    def _handle_needs_catalog_add(
        self,
        *,
        phase24_plan: Any,
        target_sid: Optional[str],
        caller_kind: str,
        dry_run: bool,
        live_enabled: bool,
        sources_degraded: List[str],
        build,
    ) -> Phase25BridgePlan:
        if not _is_valid_server_id(target_sid):
            return build(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="server_id_invalid_format",
                    target_server_id=None,
                    details_count=1,
                )],
            )
        proposal_input = _catalog_add_input_from_phase24(phase24_plan)
        if proposal_input is None:
            return build(
                Phase25BridgeDecision.BLOCKED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=target_sid,
                    risk_summary="catalog_add_required",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [Phase25BridgePlanBlocker(
                    blocker_code="catalog_proposal_invalid",
                    target_server_id=target_sid,
                    details_count=1,
                )],
            )

        if self._deps.catalog_read is None:
            return build(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="no_catalog_dep",
                    target_server_id=target_sid,
                    details_count=1,
                )],
            )

        catalog_status = _read_catalog_status(self._deps.catalog_read, target_sid)
        if catalog_status in ("declared", "installed", "active"):
            return build(
                Phase25BridgeDecision.ALREADY_APPLIED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=target_sid,
                    risk_summary="catalog_add_required",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "execution_outcome_code": "already_applied",
                },
            )
        if catalog_status in ("quarantined", "removed"):
            return build(
                Phase25BridgeDecision.BLOCKED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=target_sid,
                    risk_summary="catalog_add_required",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [Phase25BridgePlanBlocker(
                    blocker_code="unexpected_catalog_state",
                    target_server_id=target_sid,
                    details_count=1,
                )],
                extra_evidence={"catalog_status_observed": catalog_status},
            )

        expected_tool_name = "mcp_catalog_add:" + target_sid
        existing_id = _find_pending_ticket(
            self._deps.approval_queue_read, expected_tool_name
        )
        if existing_id is not None:
            return build(
                Phase25BridgeDecision.WAITING_APPROVAL,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=target_sid,
                    risk_summary="catalog_add_required",
                    proposed_ticket_action_id=existing_id,
                    invoked_orchestrator="",
                ),
                [],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "dedup_match_pending": True,
                },
            )

        if dry_run:
            return build(
                Phase25BridgeDecision.TICKET_WOULD_BE_PROPOSED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.PROPOSE_CATALOG_ADD,
                    target_server_id=target_sid,
                    risk_summary="catalog_add_required",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "execution_outcome_code": "not_executed",
                },
            )

        if not live_enabled:
            return build(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="live_mode_disabled",
                    target_server_id=target_sid,
                    details_count=1,
                )],
                extra_evidence={"catalog_status_observed": catalog_status},
            )
        if self._deps.catalog_add_orchestrator is None:
            return build(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="no_catalog_add_orchestrator_dep",
                    target_server_id=target_sid,
                    details_count=1,
                )],
                extra_evidence={"catalog_status_observed": catalog_status},
            )
        ok, proposal = _safe_call(
            self._deps.catalog_add_orchestrator.propose_catalog_add,
            proposal_input,
            caller_kind=caller_kind,
            dry_run=False,
        )
        if not ok or proposal is None:
            sources_degraded.append("catalog_add_orchestrator")
            return build(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="catalog_add_failed",
                    target_server_id=target_sid,
                    details_count=1,
                )],
                extra_evidence={"catalog_status_observed": catalog_status},
            )
        ticket_id = getattr(proposal, "approval_ticket_id", None)
        if not _is_valid_action_id(ticket_id):
            ticket_id = None
        return build(
            Phase25BridgeDecision.TICKET_PROPOSED,
            Phase25BridgeAction(
                kind=Phase25BridgeActionKind.PROPOSE_CATALOG_ADD,
                target_server_id=target_sid,
                risk_summary="catalog_add_required",
                proposed_ticket_action_id=ticket_id,
                invoked_orchestrator="",
            ),
            [],
            extra_evidence={
                "catalog_status_observed": catalog_status,
                "execution_outcome_code": "not_executed",
            },
        )

    def _handle_needs_install(
        self,
        *,
        target_sid: Optional[str],
        caller_kind: str,
        dry_run: bool,
        live_enabled: bool,
        install_ks: bool,
        sources_degraded: List[str],
        build,
    ) -> Phase25BridgePlan:
        # Validate server_id
        if not _is_valid_server_id(target_sid):
            return build(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="server_id_invalid_format",
                    target_server_id=None, details_count=1,
                )],
            )

        # Catalog pre-check
        if self._deps.catalog_read is None:
            return build(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="no_catalog_dep",
                    target_server_id=target_sid, details_count=1,
                )],
            )
        catalog_status = _read_catalog_status(
            self._deps.catalog_read, target_sid
        )
        if catalog_status == "quarantined":
            return build(
                Phase25BridgeDecision.BLOCKED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=target_sid,
                    risk_summary="install_required",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [Phase25BridgePlanBlocker(
                    blocker_code="catalog_quarantined",
                    target_server_id=target_sid, details_count=1,
                )],
                extra_evidence={"catalog_status_observed": catalog_status},
            )
        if catalog_status in ("installed", "active"):
            return build(
                Phase25BridgeDecision.ALREADY_APPLIED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=target_sid,
                    risk_summary="install_required",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "execution_outcome_code": "already_applied",
                },
            )
        # Tous les autres états (removed/unknown/"") = inattendu pour install
        if catalog_status != "declared":
            return build(
                Phase25BridgeDecision.BLOCKED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=target_sid,
                    risk_summary="install_required",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [Phase25BridgePlanBlocker(
                    blocker_code="unexpected_catalog_state",
                    target_server_id=target_sid, details_count=1,
                )],
                extra_evidence={"catalog_status_observed": catalog_status},
            )

        # Dédup pending
        expected_tool_name = "mcp_install:" + target_sid
        existing_id = _find_pending_ticket(
            self._deps.approval_queue_read, expected_tool_name
        )
        if existing_id is not None:
            return build(
                Phase25BridgeDecision.WAITING_APPROVAL,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=target_sid,
                    risk_summary="install_required",
                    proposed_ticket_action_id=existing_id,
                    invoked_orchestrator="",
                ),
                [],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "dedup_match_pending": True,
                },
            )

        # Dry-run : describe
        if dry_run:
            return build(
                Phase25BridgeDecision.TICKET_WOULD_BE_PROPOSED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.PROPOSE_INSTALL,
                    target_server_id=target_sid,
                    risk_summary="install_required",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "execution_outcome_code": "not_executed",
                },
            )

        # Live propose : LIVE + kill switch
        if not live_enabled:
            return build(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="live_mode_disabled",
                    target_server_id=target_sid, details_count=1,
                )],
                extra_evidence={"catalog_status_observed": catalog_status},
            )
        if install_ks:
            return build(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="install_kill_switch_active",
                    target_server_id=target_sid, details_count=1,
                )],
                extra_evidence={"catalog_status_observed": catalog_status},
            )
        if self._deps.install_orchestrator is None:
            return build(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="no_install_orchestrator_dep",
                    target_server_id=target_sid, details_count=1,
                )],
                extra_evidence={"catalog_status_observed": catalog_status},
            )
        ok, proposal = _safe_call(
            self._deps.install_orchestrator.propose_install,
            target_sid, caller_kind=caller_kind,
        )
        if not ok or proposal is None:
            sources_degraded.append("install_orchestrator")
            return build(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="install_failed",
                    target_server_id=target_sid, details_count=1,
                )],
                extra_evidence={"catalog_status_observed": catalog_status},
            )
        ticket_id = getattr(proposal, "approval_ticket_id", None)
        if not _is_valid_action_id(ticket_id):
            ticket_id = None
        return build(
            Phase25BridgeDecision.TICKET_PROPOSED,
            Phase25BridgeAction(
                kind=Phase25BridgeActionKind.PROPOSE_INSTALL,
                target_server_id=target_sid,
                risk_summary="install_required",
                proposed_ticket_action_id=ticket_id,
                invoked_orchestrator="",
            ),
            [],
            extra_evidence={
                "catalog_status_observed": catalog_status,
                "execution_outcome_code": "not_executed",
            },
        )

    def _handle_needs_activation(
        self,
        *,
        target_sid: Optional[str],
        caller_kind: str,
        dry_run: bool,
        live_enabled: bool,
        activation_ks: bool,
        sources_degraded: List[str],
        build,
    ) -> Phase25BridgePlan:
        if not _is_valid_server_id(target_sid):
            return build(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="server_id_invalid_format",
                    target_server_id=None, details_count=1,
                )],
            )

        if self._deps.catalog_read is None:
            return build(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="no_catalog_dep",
                    target_server_id=target_sid, details_count=1,
                )],
            )
        catalog_status = _read_catalog_status(
            self._deps.catalog_read, target_sid
        )
        handlers_present = _has_handlers_for_server(
            self._deps.tool_registry_read, target_sid
        )

        if catalog_status == "quarantined":
            return build(
                Phase25BridgeDecision.BLOCKED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=target_sid,
                    risk_summary="activation_required",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [Phase25BridgePlanBlocker(
                    blocker_code="catalog_quarantined",
                    target_server_id=target_sid, details_count=1,
                )],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "dynamic_handlers_present_for_target": handlers_present,
                },
            )
        if catalog_status == "declared":
            return build(
                Phase25BridgeDecision.BLOCKED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=target_sid,
                    risk_summary="activation_required",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [Phase25BridgePlanBlocker(
                    blocker_code="needs_install_first",
                    target_server_id=target_sid, details_count=1,
                )],
                extra_evidence={"catalog_status_observed": catalog_status},
            )
        if catalog_status == "active":
            return build(
                Phase25BridgeDecision.ALREADY_APPLIED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=target_sid,
                    risk_summary="activation_required",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "dynamic_handlers_present_for_target": handlers_present,
                    "execution_outcome_code": "already_applied",
                },
            )
        if catalog_status == "installed" and handlers_present:
            return build(
                Phase25BridgeDecision.ALREADY_APPLIED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=target_sid,
                    risk_summary="activation_required",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "dynamic_handlers_present_for_target": handlers_present,
                    "execution_outcome_code": "already_applied",
                },
            )
        # Tous autres états (removed/unknown/"") = inattendu pour activation
        if catalog_status != "installed":
            return build(
                Phase25BridgeDecision.BLOCKED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=target_sid,
                    risk_summary="activation_required",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [Phase25BridgePlanBlocker(
                    blocker_code="unexpected_catalog_state",
                    target_server_id=target_sid, details_count=1,
                )],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "dynamic_handlers_present_for_target": handlers_present,
                },
            )

        # Dédup pending
        expected_tool_name = "mcp_activate:" + target_sid
        existing_id = _find_pending_ticket(
            self._deps.approval_queue_read, expected_tool_name
        )
        if existing_id is not None:
            return build(
                Phase25BridgeDecision.WAITING_APPROVAL,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.NONE,
                    target_server_id=target_sid,
                    risk_summary="activation_required",
                    proposed_ticket_action_id=existing_id,
                    invoked_orchestrator="",
                ),
                [],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "dynamic_handlers_present_for_target": handlers_present,
                    "dedup_match_pending": True,
                },
            )

        # Dry-run describe
        if dry_run:
            return build(
                Phase25BridgeDecision.TICKET_WOULD_BE_PROPOSED,
                Phase25BridgeAction(
                    kind=Phase25BridgeActionKind.PROPOSE_ACTIVATION,
                    target_server_id=target_sid,
                    risk_summary="activation_required",
                    proposed_ticket_action_id=None,
                    invoked_orchestrator="",
                ),
                [],
                extra_evidence={
                    "catalog_status_observed": catalog_status,
                    "dynamic_handlers_present_for_target": handlers_present,
                    "execution_outcome_code": "not_executed",
                },
            )

        # Live propose
        if not live_enabled:
            return build(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="live_mode_disabled",
                    target_server_id=target_sid, details_count=1,
                )],
                extra_evidence={"catalog_status_observed": catalog_status},
            )
        if activation_ks:
            return build(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="activation_kill_switch_active",
                    target_server_id=target_sid, details_count=1,
                )],
                extra_evidence={"catalog_status_observed": catalog_status},
            )
        if self._deps.activation_service is None:
            return build(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="no_activation_service_dep",
                    target_server_id=target_sid, details_count=1,
                )],
                extra_evidence={"catalog_status_observed": catalog_status},
            )
        ok, proposal = _safe_call(
            self._deps.activation_service.propose_activation,
            target_sid, caller_kind=caller_kind,
        )
        if not ok or proposal is None:
            sources_degraded.append("activation_service")
            return build(
                Phase25BridgeDecision.BLOCKED,
                _none_action(),
                [Phase25BridgePlanBlocker(
                    blocker_code="activation_failed",
                    target_server_id=target_sid, details_count=1,
                )],
                extra_evidence={"catalog_status_observed": catalog_status},
            )
        ticket_id = getattr(proposal, "approval_ticket_id", None)
        if not _is_valid_action_id(ticket_id):
            ticket_id = None
        return build(
            Phase25BridgeDecision.TICKET_PROPOSED,
            Phase25BridgeAction(
                kind=Phase25BridgeActionKind.PROPOSE_ACTIVATION,
                target_server_id=target_sid,
                risk_summary="activation_required",
                proposed_ticket_action_id=ticket_id,
                invoked_orchestrator="",
            ),
            [],
            extra_evidence={
                "catalog_status_observed": catalog_status,
                "dynamic_handlers_present_for_target": handlers_present,
                "execution_outcome_code": "not_executed",
            },
        )

    # ── Plan builder + sanitization finale ─────────────────────────────────

    def _build_plan(
        self,
        *,
        bridge_plan_id: str,
        created_at: str,
        decision: Phase25BridgeDecision,
        action: Phase25BridgeAction,
        dry_run: bool,
        blockers: List[Phase25BridgePlanBlocker],
        evidence: Dict[str, Any],
        caller_kind: str,
    ) -> Phase25BridgePlan:
        # Sanitize action whitelists
        if action.risk_summary not in _RISK_SUMMARY_WHITELIST:
            action = Phase25BridgeAction(
                kind=action.kind,
                target_server_id=action.target_server_id,
                risk_summary="none",
                proposed_ticket_action_id=action.proposed_ticket_action_id,
                invoked_orchestrator=action.invoked_orchestrator,
            )
        if action.invoked_orchestrator not in ("", "catalog_add", "install", "activation"):
            action = Phase25BridgeAction(
                kind=action.kind,
                target_server_id=action.target_server_id,
                risk_summary=action.risk_summary,
                proposed_ticket_action_id=action.proposed_ticket_action_id,
                invoked_orchestrator="",
            )

        # Sanitize blockers
        safe_blockers: List[Phase25BridgePlanBlocker] = []
        for b in blockers:
            if b.blocker_code in _BLOCKER_CODES:
                safe_blockers.append(b)

        # Sanitize evidence by whitelist
        clean_evidence = {
            k: v for k, v in evidence.items()
            if k in _EVIDENCE_WHITELIST and v is not None
        }
        # Always include dry_run as bool, mapped_decision_reason_code
        clean_evidence.setdefault("dry_run", dry_run)
        clean_evidence.setdefault(
            "mapped_decision_reason_code", decision.value
        )

        plan = Phase25BridgePlan(
            bridge_plan_id=bridge_plan_id,
            decision=decision,
            action=action,
            dry_run=dry_run,
            blockers=tuple(safe_blockers),
            evidence=clean_evidence,
            created_at=created_at,
        )
        self._append_audit_if_configured(plan, caller_kind)
        return plan

    # ── Audit local optionnel ──────────────────────────────────────────────

    def _append_audit_if_configured(
        self, plan: Phase25BridgePlan, caller_kind: str,
    ) -> None:
        if self._audit_log_path is None:
            return
        try:
            self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        event = {
            "ts": plan.created_at,
            "event": "bridge_plan_completed",
            "phase": "25",
            "bridge_plan_id": plan.bridge_plan_id,
            "decision": plan.decision.value,
            "action_kind": plan.action.kind.value,
            "caller_kind": caller_kind,
            "dry_run": bool(plan.dry_run),
            "blockers_count": len(plan.blockers),
            "sources_degraded_count": len(
                plan.evidence.get("sources_degraded", [])
            ),
            "invoked_orchestrator": plan.action.invoked_orchestrator,
            "execution_outcome_code": plan.evidence.get(
                "execution_outcome_code", ""
            ),
            "live_mode_enabled": bool(
                plan.evidence.get("live_mode_enabled", False)
            ),
        }
        try:
            line = json.dumps(event, ensure_ascii=False)
            with self._audit_log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            return
