"""
Phase 24 — Autonomous MCP Loop Planner (pur planificateur).

Composant pur testable, lecture seule stricte.

Doctrine Phase 24 v1 :
  - Phase 24 v1 n'appelle jamais les orchestrators, même en tests.
    Phase 25 fera l'Execution Bridge.
  - Aucune invocation install, aucune invocation activation,
    aucun call_tool, aucun enregistrement dynamique de handler,
    aucun lancement de processus externe, aucune mutation runtime,
    aucun branchement ReAct.
  - ApprovalQueue strictement lecture seule : list_pending + get
    uniquement. Aucun appel à approve/reject/propose/add_pending.
  - Aucun import dur vers capability_resolver.py, proposal_planner.py,
    install_orchestrator.py, activation_service.py, client_factory.py,
    sandbox_runner.py, auto_approve.py dans le code prod. Protocols
    injectés uniquement. Import direct autorisé seulement dans le
    fichier de test pour fixtures réelles.
  - Aucun import au module-level de clients HTTP tiers.
  - Aucun singleton runtime, aucun cache.
  - Aucune route HTTP, aucun web touché.
  - Aucun faux action_id généré (Phase 10 ApprovalQueue reste l'unique
    source de vérité pour la génération des action_id).
  - Aucun flag auto-approve direct exposé (auto_approve_engine retiré
    des deps v1 — les orchestrators 20B existants restent l'unique
    source de décision auto-approve).
  - Extraction stricte des champs SAFE de Phase 22 selected_candidate :
    kind, tool_name, server_id, match_score uniquement.
  - target_tool_name sanitizé (regex + longueur). Si invalide → None +
    blocker safe_field_invalid.
  - proposed_risk_summary ∈ whitelist enum-like courte.
  - Sortie sanitizée par whitelist stricte.
  - Audit local optionnel.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
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

_INTENT_MAX_CHARS = 256
_TASK_CONTEXT_HASH_LEN = 12
_TOOL_NAME_MAX = 128
_SERVER_ID_MAX = 64
_PROFILE_MAX = 64
_CALLER_KIND_MAX = 32

_CALLER_KIND_WHITELIST: frozenset[str] = frozenset({
    "react", "sub_agent", "admin_ui", "test", "autonomous_loop",
})

_PROPOSED_RISK_SUMMARY_WHITELIST: frozenset[str] = frozenset({
    "install_required",
    "activation_required",
    "catalog_add_required",
    "local_creation_required",
    "none",
})

_PROPOSED_ACTION_KIND_WHITELIST: frozenset[str] = frozenset({
    "install", "activation", "catalog_add_declared", "local_create", "",
})

_EVIDENCE_WHITELIST: frozenset[str] = frozenset({
    "plan_id",
    "created_at",
    "intent_id_phase22",
    "proposal_id_phase23",
    "phase22_decision",
    "phase23_decision",
    "mapped_decision_reason_code",
    "actionable_intent",
    "requires_admin_nod",
    "target_server_id",
    "target_tool_name",
    "target_match_score",
    "task_context_hash",
    "caller_kind",
    "sources_degraded",
    "resume_marker_inspected",
    "ticket_state",
    "catalog_package_spec_present",
    "catalog_trust_score_set",
})

_BLOCKER_CODES: frozenset[str] = frozenset({
    "phase22_blocked",
    "phase22_needs_approval",
    "marker_invalid_format",
    "marker_not_found_or_already_decided",
    "no_phase22_resolver",
    "no_phase23_planner",
    "no_approval_queue_read",
    "infinite_loop_prevention",
    "safe_field_invalid",
})

# Validation tool_name (lowercase + chiffres + underscore + tiret).
_TOOL_NAME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_\-\.]{0,127}$")

# Server_id format Phase 14 hint : lettres/chiffres/_/- seulement.
_SERVER_ID_RE = re.compile(r"^[a-zA-Z0-9_\-]{1,64}$")

# Marker UUID4 hex 32.
_UUID4_HEX32_RE = re.compile(r"^[0-9a-f]{32}$")

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# ══════════════════════════════════════════════════════════════════════════════
# Helpers déterministes
# ══════════════════════════════════════════════════════════════════════════════


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_plan_id() -> str:
    return uuid.uuid4().hex


def _sanitize_intent(raw: Any) -> str:
    if not isinstance(raw, str):
        return ""
    normalized = unicodedata.normalize("NFC", raw)
    cleaned = _CONTROL_RE.sub("", normalized).strip()
    if len(cleaned) > _INTENT_MAX_CHARS:
        cleaned = cleaned[:_INTENT_MAX_CHARS]
    return cleaned


def _sanitize_caller_kind(raw: Any) -> str:
    if not isinstance(raw, str):
        return "unknown"
    if raw in _CALLER_KIND_WHITELIST:
        return raw
    return "unknown"


def _sanitize_profile(raw: Any) -> Optional[str]:
    if not isinstance(raw, str) or not raw:
        return None
    cleaned = _CONTROL_RE.sub("", raw).strip()
    if not cleaned:
        return None
    if len(cleaned) > _PROFILE_MAX:
        cleaned = cleaned[:_PROFILE_MAX]
    return cleaned


def _hash_task_context(task_context: Any) -> str:
    if task_context is None:
        return ""
    try:
        serialized = json.dumps(
            task_context, sort_keys=True, ensure_ascii=False, default=str
        )
    except Exception:
        serialized = ""
    if not serialized:
        return ""
    h = hashlib.sha256(serialized.encode("utf-8", errors="replace")).hexdigest()
    return h[:_TASK_CONTEXT_HASH_LEN]


def _validate_target_tool_name(raw: Any) -> Optional[str]:
    """Phase 24 v1 : strict regex + longueur, sinon None.

    Le caller verra alors un blocker safe_field_invalid si on ne peut pas
    propager un target_tool_name fiable.
    """
    if not isinstance(raw, str):
        return None
    if len(raw) > _TOOL_NAME_MAX:
        return None
    if _CONTROL_RE.search(raw):
        return None
    if not _TOOL_NAME_RE.match(raw):
        return None
    return raw


def _validate_target_server_id(raw: Any) -> Optional[str]:
    if not isinstance(raw, str):
        return None
    if len(raw) > _SERVER_ID_MAX:
        return None
    if not _SERVER_ID_RE.match(raw):
        return None
    return raw


def _validate_match_score(raw: Any) -> Optional[float]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        try:
            v = float(raw)
        except Exception:
            return None
        if 0.0 <= v <= 1.0:
            return v
    return None


def _is_valid_marker_uuid4_hex32(raw: Any) -> bool:
    return isinstance(raw, str) and bool(_UUID4_HEX32_RE.match(raw))


def _safe_call(fn, *args, **kwargs) -> Tuple[bool, Any]:
    try:
        return True, fn(*args, **kwargs)
    except Exception:
        return False, None


def _sanitize_phase22_evidence(plan: Any) -> Optional[Dict[str, Any]]:
    """Phase 22 expose déjà une evidence whitelistée. Phase 24 ne lit
    QUE le dict evidence, jamais candidates raw.
    """
    if plan is None:
        return None
    ev = getattr(plan, "evidence", None)
    if not isinstance(ev, dict):
        return None
    return dict(ev)


def _sanitize_phase23_evidence(plan: Any) -> Optional[Dict[str, Any]]:
    if plan is None:
        return None
    ev = getattr(plan, "evidence", None)
    if not isinstance(ev, dict):
        return None
    return dict(ev)


# ══════════════════════════════════════════════════════════════════════════════
# Protocols read-only — aucun import dur Phase 22/23 en prod
# ══════════════════════════════════════════════════════════════════════════════


class CapabilityResolverLike(Protocol):
    def resolve(
        self, intent: str, *, caller_kind: str,
        profile: Optional[str] = None,
    ) -> Any: ...


class ProposalPlannerLike(Protocol):
    def plan_proposal(
        self, intent: str, *, caller_kind: str,
        profile: Optional[str] = None,
        phase22_plan: Optional[Any] = None,
    ) -> Any: ...


class ApprovalQueueReadLike(Protocol):
    """API réelle Phase 10 — read-only strict.

    Seules list_pending() et get(action_id) exposées. Aucun appel
    approve/reject/propose/add_pending.
    """
    def list_pending(self) -> List[Any]: ...
    def get(self, action_id: str) -> Optional[Any]: ...


# ══════════════════════════════════════════════════════════════════════════════
# Modèles immutables
# ══════════════════════════════════════════════════════════════════════════════


class AutonomousMCPLoopDecision(str, Enum):
    READY_TO_USE_EXISTING_CAPABILITY = "ready_to_use_existing_capability"
    NEEDS_INSTALL_APPROVAL = "needs_install_approval"
    NEEDS_ACTIVATION_APPROVAL = "needs_activation_approval"
    NEEDS_CATALOG_APPROVAL = "needs_catalog_approval"
    NEEDS_LOCAL_CREATION = "needs_local_creation"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    NO_SAFE_PATH = "no_safe_path"


class AutonomousActionKind(str, Enum):
    USE_TOOL = "use_tool"
    INSTALL = "install"
    ACTIVATE = "activate"
    CATALOG_ADD_DECLARED = "catalog_add_declared"
    LOCAL_CREATE = "local_create"
    NONE = "none"


@dataclass(frozen=True)
class AutonomousAction:
    kind: AutonomousActionKind
    target_server_id: Optional[str]
    target_tool_name: Optional[str]
    match_score: Optional[float]
    proposed_action_kind: str          # ∈ _PROPOSED_ACTION_KIND_WHITELIST
    proposed_target_server_id: Optional[str]
    proposed_risk_summary: str         # ∈ _PROPOSED_RISK_SUMMARY_WHITELIST
    requires_admin_nod: bool
    catalog_display_name: Optional[str] = None
    catalog_package_spec: Optional[str] = None
    catalog_version: Optional[str] = None
    catalog_trust_score: Optional[int] = None
    # Phase I-8 (Fix AC) : tokens discriminants de l'intent d'origine,
    # propagés jusqu'à l'entrée catalog pour le re-matching futur.
    catalog_capability_tags: Optional[Tuple[str, ...]] = None


@dataclass(frozen=True)
class AutonomousMCPLoopPlanBlocker:
    blocker_code: str                  # ∈ _BLOCKER_CODES
    target_server_id: Optional[str]
    details_count: int


@dataclass(frozen=True)
class AutonomousMCPLoopDeps:
    capability_resolver: Optional[CapabilityResolverLike] = None
    proposal_planner: Optional[ProposalPlannerLike] = None
    approval_queue_read: Optional[ApprovalQueueReadLike] = None


@dataclass(frozen=True)
class AutonomousMCPLoopPlan:
    plan_id: str
    intent_query_sanitized: str
    decision: AutonomousMCPLoopDecision
    action: AutonomousAction
    capability_plan_snapshot: Optional[Dict[str, Any]]
    proposal_plan_snapshot: Optional[Dict[str, Any]]
    blockers: Tuple[AutonomousMCPLoopPlanBlocker, ...]
    evidence: Dict[str, Any]
    created_at: str
    resume_marker_inspected: Optional[str]


def _none_action() -> AutonomousAction:
    return AutonomousAction(
        kind=AutonomousActionKind.NONE,
        target_server_id=None,
        target_tool_name=None,
        match_score=None,
        proposed_action_kind="",
        proposed_target_server_id=None,
        proposed_risk_summary="none",
        requires_admin_nod=False,
        catalog_display_name=None,
        catalog_package_spec=None,
        catalog_version=None,
        catalog_trust_score=None,
    )


# ══════════════════════════════════════════════════════════════════════════════
# AutonomousMCPLoopPlanner — stateless, lecture seule
# ══════════════════════════════════════════════════════════════════════════════


class AutonomousMCPLoopPlanner:
    """Phase 24 v1 — pur planificateur. Aucune invocation.

    Aucun cache. Aucune mutation. Aucune méthode d'exécution.
    """

    def __init__(
        self,
        deps: AutonomousMCPLoopDeps,
        *,
        audit_log_path: Optional[Path] = None,
    ) -> None:
        if not isinstance(deps, AutonomousMCPLoopDeps):
            raise TypeError(
                "deps must be a AutonomousMCPLoopDeps instance"
            )
        if audit_log_path is not None and not isinstance(
            audit_log_path, Path
        ):
            raise TypeError("audit_log_path must be a pathlib.Path or None")
        self._deps = deps
        self._audit_log_path = audit_log_path

    # ── Public APIs ────────────────────────────────────────────────────────

    def plan_for_intent(
        self,
        intent: str,
        *,
        caller_kind: str,
        profile: Optional[str] = None,
        task_context: Optional[Dict[str, Any]] = None,
    ) -> AutonomousMCPLoopPlan:
        intent_sanitized = _sanitize_intent(intent)
        caller_kind_clean = _sanitize_caller_kind(caller_kind)
        profile_clean = _sanitize_profile(profile)
        task_context_hash = _hash_task_context(task_context)
        plan_id = _new_plan_id()
        created_at = _now_utc_iso()
        sources_degraded: List[str] = []

        # ── 1. Phase 22 resolver requis ───────────────────────────────────
        resolver = self._deps.capability_resolver
        if resolver is None:
            return self._build_plan(
                plan_id=plan_id,
                created_at=created_at,
                intent_sanitized=intent_sanitized,
                decision=AutonomousMCPLoopDecision.NO_SAFE_PATH,
                action=_none_action(),
                phase22_snapshot=None,
                phase23_snapshot=None,
                blockers=[AutonomousMCPLoopPlanBlocker(
                    blocker_code="no_phase22_resolver",
                    target_server_id=None,
                    details_count=1,
                )],
                caller_kind=caller_kind_clean,
                task_context_hash=task_context_hash,
                sources_degraded=sources_degraded,
                resume_marker_inspected=None,
            )

        ok, p22_plan = _safe_call(
            resolver.resolve, intent_sanitized,
            caller_kind=caller_kind_clean, profile=profile_clean,
        )
        if not ok or p22_plan is None:
            sources_degraded.append("capability_resolver")
            return self._build_plan(
                plan_id=plan_id,
                created_at=created_at,
                intent_sanitized=intent_sanitized,
                decision=AutonomousMCPLoopDecision.NO_SAFE_PATH,
                action=_none_action(),
                phase22_snapshot=None,
                phase23_snapshot=None,
                blockers=[AutonomousMCPLoopPlanBlocker(
                    blocker_code="no_phase22_resolver",
                    target_server_id=None,
                    details_count=1,
                )],
                caller_kind=caller_kind_clean,
                task_context_hash=task_context_hash,
                sources_degraded=sources_degraded,
                resume_marker_inspected=None,
            )

        # ── 2. Mapper Phase 22 decision → Phase 24 (avec éventuel recall) ─
        decision, action, p23_plan, extra_blockers = self._map_phase22(
            p22_plan,
            intent_sanitized=intent_sanitized,
            caller_kind=caller_kind_clean,
            profile=profile_clean,
            allow_recall=True,
            sources_degraded=sources_degraded,
        )

        return self._build_plan(
            plan_id=plan_id,
            created_at=created_at,
            intent_sanitized=intent_sanitized,
            decision=decision,
            action=action,
            phase22_snapshot=_sanitize_phase22_evidence(p22_plan),
            phase23_snapshot=_sanitize_phase23_evidence(p23_plan),
            blockers=extra_blockers,
            caller_kind=caller_kind_clean,
            task_context_hash=task_context_hash,
            sources_degraded=sources_degraded,
            resume_marker_inspected=None,
            p22_decision=self._read_phase_decision(p22_plan),
            p23_decision=self._read_phase_decision(p23_plan),
            p22_intent_id=self._read_intent_id_evidence(p22_plan),
            p23_proposal_id=self._read_proposal_id_evidence(p23_plan),
        )

    def describe_pending_resume(
        self,
        resume_marker: str,
        *,
        caller_kind: str,
    ) -> AutonomousMCPLoopPlan:
        plan_id = _new_plan_id()
        created_at = _now_utc_iso()
        caller_kind_clean = _sanitize_caller_kind(caller_kind)
        sources_degraded: List[str] = []

        # 1. Validation marker
        if not _is_valid_marker_uuid4_hex32(resume_marker):
            return self._build_plan(
                plan_id=plan_id,
                created_at=created_at,
                intent_sanitized="",
                decision=AutonomousMCPLoopDecision.BLOCKED,
                action=_none_action(),
                phase22_snapshot=None,
                phase23_snapshot=None,
                blockers=[AutonomousMCPLoopPlanBlocker(
                    blocker_code="marker_invalid_format",
                    target_server_id=None,
                    details_count=1,
                )],
                caller_kind=caller_kind_clean,
                task_context_hash="",
                sources_degraded=sources_degraded,
                resume_marker_inspected=None,
            )

        # 2. Dep approval_queue_read requis
        aq = self._deps.approval_queue_read
        if aq is None:
            return self._build_plan(
                plan_id=plan_id,
                created_at=created_at,
                intent_sanitized="",
                decision=AutonomousMCPLoopDecision.BLOCKED,
                action=_none_action(),
                phase22_snapshot=None,
                phase23_snapshot=None,
                blockers=[AutonomousMCPLoopPlanBlocker(
                    blocker_code="no_approval_queue_read",
                    target_server_id=None,
                    details_count=1,
                )],
                caller_kind=caller_kind_clean,
                task_context_hash="",
                sources_degraded=sources_degraded,
                resume_marker_inspected=resume_marker,
            )

        # 3. Inspect list_pending pour le marker
        ok_list, pending_list = _safe_call(aq.list_pending)
        if not ok_list or not isinstance(pending_list, list):
            sources_degraded.append("approval_queue_read")
            pending_list = []
        marker_in_pending = False
        for ticket in pending_list:
            tid = getattr(ticket, "id", None)
            if tid == resume_marker:
                marker_in_pending = True
                break

        if marker_in_pending:
            return self._build_plan(
                plan_id=plan_id,
                created_at=created_at,
                intent_sanitized="",
                decision=AutonomousMCPLoopDecision.WAITING_APPROVAL,
                action=_none_action(),
                phase22_snapshot=None,
                phase23_snapshot=None,
                blockers=[],
                caller_kind=caller_kind_clean,
                task_context_hash="",
                sources_degraded=sources_degraded,
                resume_marker_inspected=resume_marker,
                ticket_state="pending",
            )

        # 4. .get retombe sur None pour tout ticket non-pending → ambigu
        ok_get, ticket = _safe_call(aq.get, resume_marker)
        if not ok_get:
            sources_degraded.append("approval_queue_read")
        if ok_get and ticket is not None:
            # Phase 10 .get retourne uniquement pending — donc on devrait
            # déjà l'avoir vu plus haut. Si on arrive ici, c'est un état
            # inattendu mais on traite comme pending (sécurité).
            return self._build_plan(
                plan_id=plan_id,
                created_at=created_at,
                intent_sanitized="",
                decision=AutonomousMCPLoopDecision.WAITING_APPROVAL,
                action=_none_action(),
                phase22_snapshot=None,
                phase23_snapshot=None,
                blockers=[],
                caller_kind=caller_kind_clean,
                task_context_hash="",
                sources_degraded=sources_degraded,
                resume_marker_inspected=resume_marker,
                ticket_state="pending",
            )

        # 5. Marker absent → ambigu honnête (rejected/approved/jamais existé)
        return self._build_plan(
            plan_id=plan_id,
            created_at=created_at,
            intent_sanitized="",
            decision=AutonomousMCPLoopDecision.BLOCKED,
            action=_none_action(),
            phase22_snapshot=None,
            phase23_snapshot=None,
            blockers=[AutonomousMCPLoopPlanBlocker(
                blocker_code="marker_not_found_or_already_decided",
                target_server_id=None,
                details_count=1,
            )],
            caller_kind=caller_kind_clean,
            task_context_hash="",
            sources_degraded=sources_degraded,
            resume_marker_inspected=resume_marker,
        )

    # ── Mapping Phase 22 → Phase 24 ────────────────────────────────────────

    def _map_phase22(
        self,
        p22_plan: Any,
        *,
        intent_sanitized: str,
        caller_kind: str,
        profile: Optional[str],
        allow_recall: bool,
        sources_degraded: List[str],
    ) -> Tuple[
        AutonomousMCPLoopDecision,
        AutonomousAction,
        Optional[Any],
        List[AutonomousMCPLoopPlanBlocker],
    ]:
        decision_raw = self._read_phase_decision(p22_plan)
        selected = getattr(p22_plan, "selected_candidate", None)

        # Extract safe fields from selected_candidate
        sel_kind = self._read_str_attr(selected, "kind")
        sel_tool = _validate_target_tool_name(
            self._read_str_attr(selected, "tool_name")
        )
        sel_sid = _validate_target_server_id(
            self._read_str_attr(selected, "server_id")
        )
        sel_score = _validate_match_score(
            getattr(selected, "match_score", None)
        )

        extra_blockers: List[AutonomousMCPLoopPlanBlocker] = []

        # Detect unsafe tool_name when selected is present
        safe_field_invalid = False
        if selected is not None:
            raw_tool = getattr(selected, "tool_name", None)
            if isinstance(raw_tool, str) and sel_tool is None:
                safe_field_invalid = True

        if decision_raw in ("use_native_tool", "use_active_mcp_tool"):
            decision_24 = (
                AutonomousMCPLoopDecision.READY_TO_USE_EXISTING_CAPABILITY
            )
            if safe_field_invalid:
                extra_blockers.append(AutonomousMCPLoopPlanBlocker(
                    blocker_code="safe_field_invalid",
                    target_server_id=sel_sid,
                    details_count=1,
                ))
            action = AutonomousAction(
                kind=AutonomousActionKind.USE_TOOL,
                target_server_id=sel_sid,
                target_tool_name=sel_tool,
                match_score=sel_score,
                proposed_action_kind="",
                proposed_target_server_id=None,
                proposed_risk_summary="none",
                requires_admin_nod=False,
            )
            return decision_24, action, None, extra_blockers

        if decision_raw == "activate_installed_mcp":
            if safe_field_invalid:
                extra_blockers.append(AutonomousMCPLoopPlanBlocker(
                    blocker_code="safe_field_invalid",
                    target_server_id=sel_sid,
                    details_count=1,
                ))
            action = AutonomousAction(
                kind=AutonomousActionKind.ACTIVATE,
                target_server_id=sel_sid,
                target_tool_name=sel_tool,
                match_score=sel_score,
                proposed_action_kind="activation",
                proposed_target_server_id=sel_sid,
                proposed_risk_summary="activation_required",
                requires_admin_nod=False,
            )
            return (
                AutonomousMCPLoopDecision.NEEDS_ACTIVATION_APPROVAL,
                action, None, extra_blockers,
            )

        if decision_raw == "install_declared_mcp":
            if safe_field_invalid:
                extra_blockers.append(AutonomousMCPLoopPlanBlocker(
                    blocker_code="safe_field_invalid",
                    target_server_id=sel_sid,
                    details_count=1,
                ))
            action = AutonomousAction(
                kind=AutonomousActionKind.INSTALL,
                target_server_id=sel_sid,
                target_tool_name=sel_tool,
                match_score=sel_score,
                proposed_action_kind="install",
                proposed_target_server_id=sel_sid,
                proposed_risk_summary="install_required",
                requires_admin_nod=False,
            )
            return (
                AutonomousMCPLoopDecision.NEEDS_INSTALL_APPROVAL,
                action, None, extra_blockers,
            )

        if decision_raw == "search_mcp":
            return self._handle_search_mcp(
                p22_plan,
                intent_sanitized=intent_sanitized,
                caller_kind=caller_kind,
                profile=profile,
                allow_recall=allow_recall,
                sources_degraded=sources_degraded,
            )

        if decision_raw in (
            "blocked_policy", "blocked_trust", "blocked_runtime",
        ):
            return (
                AutonomousMCPLoopDecision.BLOCKED,
                _none_action(),
                None,
                [AutonomousMCPLoopPlanBlocker(
                    blocker_code="phase22_blocked",
                    target_server_id=sel_sid,
                    details_count=1,
                )],
            )

        if decision_raw == "needs_approval":
            return (
                AutonomousMCPLoopDecision.WAITING_APPROVAL,
                _none_action(),
                None,
                [AutonomousMCPLoopPlanBlocker(
                    blocker_code="phase22_needs_approval",
                    target_server_id=sel_sid,
                    details_count=1,
                )],
            )

        if decision_raw == "no_capability_found":
            # Si actionable + planner dispo → essayer Phase 23 search path
            actionable = self._read_actionable_intent(p22_plan)
            if actionable and self._deps.proposal_planner is not None:
                return self._handle_search_mcp(
                    p22_plan,
                    intent_sanitized=intent_sanitized,
                    caller_kind=caller_kind,
                    profile=profile,
                    allow_recall=allow_recall,
                    sources_degraded=sources_degraded,
                )
            return (
                AutonomousMCPLoopDecision.NO_SAFE_PATH,
                _none_action(), None, [],
            )

        # Decisions inattendues / inconnues → NO_SAFE_PATH descriptif
        return (
            AutonomousMCPLoopDecision.NO_SAFE_PATH,
            _none_action(), None, [],
        )

    def _handle_search_mcp(
        self,
        p22_plan: Any,
        *,
        intent_sanitized: str,
        caller_kind: str,
        profile: Optional[str],
        allow_recall: bool,
        sources_degraded: List[str],
    ) -> Tuple[
        AutonomousMCPLoopDecision,
        AutonomousAction,
        Optional[Any],
        List[AutonomousMCPLoopPlanBlocker],
    ]:
        planner = self._deps.proposal_planner
        if planner is None:
            return (
                AutonomousMCPLoopDecision.NO_SAFE_PATH,
                _none_action(), None,
                [AutonomousMCPLoopPlanBlocker(
                    blocker_code="no_phase23_planner",
                    target_server_id=None, details_count=1,
                )],
            )
        ok, p23_plan = _safe_call(
            planner.plan_proposal, intent_sanitized,
            caller_kind=caller_kind, profile=profile,
            phase22_plan=p22_plan,
        )
        if not ok or p23_plan is None:
            sources_degraded.append("proposal_planner")
            return (
                AutonomousMCPLoopDecision.NO_SAFE_PATH,
                _none_action(), None,
                [AutonomousMCPLoopPlanBlocker(
                    blocker_code="no_phase23_planner",
                    target_server_id=None, details_count=1,
                )],
            )

        p23_decision = self._read_phase_decision(p23_plan)

        # Extract safe target_server_id from Phase 23 catalog_proposal if any
        cat_prop = getattr(p23_plan, "catalog_proposal", None)
        cat_sid = _validate_target_server_id(
            self._read_str_attr(cat_prop, "proposed_server_id")
        )
        cat_requires_approval = bool(
            getattr(cat_prop, "requires_approval", False)
        )
        cat_display_name = self._read_str_attr(cat_prop, "proposed_display_name")
        cat_package_spec = self._read_str_attr(cat_prop, "proposed_package_spec")
        cat_version = self._read_str_attr(cat_prop, "proposed_version")
        cat_trust_score = getattr(cat_prop, "proposed_trust_score_set", None)
        if isinstance(cat_trust_score, bool) or not isinstance(cat_trust_score, int):
            cat_trust_score = None
        elif not (0 <= cat_trust_score <= 100):
            cat_trust_score = None

        # Phase I-8 (Fix AC) : tags discriminants depuis l'intent d'origine
        # + le nom du package. Propagés via le ticket jusqu'à l'entrée
        # catalog pour que le resolver re-matche les intents futurs.
        cat_capability_tags: Optional[Tuple[str, ...]] = None
        try:
            # Shim feuille (règle d'architecture Phase 24 : pas d'import
            # de la machinerie resolver — cf. _FORBIDDEN_IMPORTS).
            from src.mcp.capability_tags import (  # noqa: WPS433
                derive_capability_tags,
            )
            tag_source = intent_sanitized
            if isinstance(cat_display_name, str) and cat_display_name:
                tag_source += " " + cat_display_name
            derived = derive_capability_tags(tag_source)
            cat_capability_tags = derived if derived else None
        except Exception:  # noqa: BLE001
            cat_capability_tags = None

        if p23_decision == "use_existing_candidate":
            # Recall Phase 22 une seule fois (anti-loop)
            if not allow_recall:
                return (
                    AutonomousMCPLoopDecision.NO_SAFE_PATH,
                    _none_action(), p23_plan,
                    [AutonomousMCPLoopPlanBlocker(
                        blocker_code="infinite_loop_prevention",
                        target_server_id=None, details_count=1,
                    )],
                )
            resolver = self._deps.capability_resolver
            if resolver is None:
                return (
                    AutonomousMCPLoopDecision.NO_SAFE_PATH,
                    _none_action(), p23_plan, [],
                )
            ok2, p22_plan_2 = _safe_call(
                resolver.resolve, intent_sanitized,
                caller_kind=caller_kind, profile=profile,
            )
            if not ok2 or p22_plan_2 is None:
                sources_degraded.append("capability_resolver")
                return (
                    AutonomousMCPLoopDecision.NO_SAFE_PATH,
                    _none_action(), p23_plan, [],
                )
            # Recall : si Phase 22 redonne SEARCH_MCP → loop prevention
            decision_raw_2 = self._read_phase_decision(p22_plan_2)
            if decision_raw_2 == "search_mcp":
                return (
                    AutonomousMCPLoopDecision.NO_SAFE_PATH,
                    _none_action(), p23_plan,
                    [AutonomousMCPLoopPlanBlocker(
                        blocker_code="infinite_loop_prevention",
                        target_server_id=None, details_count=1,
                    )],
                )
            return self._map_phase22(
                p22_plan_2,
                intent_sanitized=intent_sanitized,
                caller_kind=caller_kind,
                profile=profile,
                allow_recall=False,
                sources_degraded=sources_degraded,
            )

        if p23_decision == "propose_catalog_declared":
            action = AutonomousAction(
                kind=AutonomousActionKind.CATALOG_ADD_DECLARED,
                target_server_id=cat_sid,
                target_tool_name=None,
                match_score=None,
                proposed_action_kind="catalog_add_declared",
                proposed_target_server_id=cat_sid,
                proposed_risk_summary="catalog_add_required",
                requires_admin_nod=cat_requires_approval,
                catalog_display_name=cat_display_name,
                catalog_package_spec=cat_package_spec,
                catalog_version=cat_version,
                catalog_trust_score=cat_trust_score,
                catalog_capability_tags=cat_capability_tags,
            )
            return (
                AutonomousMCPLoopDecision.NEEDS_CATALOG_APPROVAL,
                action, p23_plan, [],
            )

        if p23_decision == "propose_local_create":
            action = AutonomousAction(
                kind=AutonomousActionKind.LOCAL_CREATE,
                target_server_id=None,
                target_tool_name=None,
                match_score=None,
                proposed_action_kind="local_create",
                proposed_target_server_id=None,
                proposed_risk_summary="local_creation_required",
                requires_admin_nod=False,
            )
            return (
                AutonomousMCPLoopDecision.NEEDS_LOCAL_CREATION,
                action, p23_plan, [],
            )

        if p23_decision == "needs_approval":
            # Source réseau Phase 23 → catalog approval avec admin nod
            action = AutonomousAction(
                kind=AutonomousActionKind.CATALOG_ADD_DECLARED,
                target_server_id=cat_sid,
                target_tool_name=None,
                match_score=None,
                proposed_action_kind="catalog_add_declared",
                proposed_target_server_id=cat_sid,
                proposed_risk_summary="catalog_add_required",
                requires_admin_nod=True,
                catalog_display_name=cat_display_name,
                catalog_package_spec=cat_package_spec,
                catalog_version=cat_version,
                catalog_trust_score=cat_trust_score,
                catalog_capability_tags=cat_capability_tags,
            )
            return (
                AutonomousMCPLoopDecision.NEEDS_CATALOG_APPROVAL,
                action, p23_plan, [],
            )

        # no_safe_candidate ou inconnu
        return (
            AutonomousMCPLoopDecision.NO_SAFE_PATH,
            _none_action(), p23_plan, [],
        )

    # ── Read helpers (lecture pure, jamais d'attribut raw exposé) ─────────

    def _read_phase_decision(self, plan: Any) -> str:
        if plan is None:
            return ""
        dec = getattr(plan, "decision", None)
        if dec is None:
            return ""
        val = getattr(dec, "value", None)
        if isinstance(val, str):
            return val
        if isinstance(dec, str):
            return dec
        return ""

    def _read_str_attr(self, obj: Any, name: str) -> Optional[str]:
        if obj is None:
            return None
        v = getattr(obj, name, None)
        if isinstance(v, str):
            return v
        return None

    def _read_actionable_intent(self, p22_plan: Any) -> bool:
        ev = getattr(p22_plan, "evidence", None)
        if not isinstance(ev, dict):
            return False
        return bool(ev.get("actionable_intent", False))

    def _read_intent_id_evidence(self, p22_plan: Any) -> Optional[str]:
        ev = getattr(p22_plan, "evidence", None)
        if not isinstance(ev, dict):
            return None
        v = ev.get("intent_id")
        return v if isinstance(v, str) else None

    def _read_proposal_id_evidence(self, p23_plan: Any) -> Optional[str]:
        if p23_plan is None:
            return None
        ev = getattr(p23_plan, "evidence", None)
        if not isinstance(ev, dict):
            return None
        v = ev.get("proposal_id")
        return v if isinstance(v, str) else None

    # ── Plan builder + sanitization finale ─────────────────────────────────

    def _build_plan(
        self,
        *,
        plan_id: str,
        created_at: str,
        intent_sanitized: str,
        decision: AutonomousMCPLoopDecision,
        action: AutonomousAction,
        phase22_snapshot: Optional[Dict[str, Any]],
        phase23_snapshot: Optional[Dict[str, Any]],
        blockers: List[AutonomousMCPLoopPlanBlocker],
        caller_kind: str,
        task_context_hash: str,
        sources_degraded: List[str],
        resume_marker_inspected: Optional[str],
        ticket_state: Optional[str] = None,
        p22_decision: str = "",
        p23_decision: str = "",
        p22_intent_id: Optional[str] = None,
        p23_proposal_id: Optional[str] = None,
    ) -> AutonomousMCPLoopPlan:
        # Validation whitelists action
        if action.proposed_risk_summary not in _PROPOSED_RISK_SUMMARY_WHITELIST:
            # Fallback safe : "none"
            action = AutonomousAction(
                kind=action.kind,
                target_server_id=action.target_server_id,
                target_tool_name=action.target_tool_name,
                match_score=action.match_score,
                proposed_action_kind=action.proposed_action_kind,
                proposed_target_server_id=action.proposed_target_server_id,
                proposed_risk_summary="none",
                requires_admin_nod=action.requires_admin_nod,
                catalog_display_name=action.catalog_display_name,
                catalog_package_spec=action.catalog_package_spec,
                catalog_version=action.catalog_version,
                catalog_trust_score=action.catalog_trust_score,
            )
        if action.proposed_action_kind not in _PROPOSED_ACTION_KIND_WHITELIST:
            action = AutonomousAction(
                kind=action.kind,
                target_server_id=action.target_server_id,
                target_tool_name=action.target_tool_name,
                match_score=action.match_score,
                proposed_action_kind="",
                proposed_target_server_id=action.proposed_target_server_id,
                proposed_risk_summary=action.proposed_risk_summary,
                requires_admin_nod=action.requires_admin_nod,
                catalog_display_name=action.catalog_display_name,
                catalog_package_spec=action.catalog_package_spec,
                catalog_version=action.catalog_version,
                catalog_trust_score=action.catalog_trust_score,
            )

        # Validation whitelists blockers
        safe_blockers: List[AutonomousMCPLoopPlanBlocker] = []
        for b in blockers:
            if b.blocker_code in _BLOCKER_CODES:
                safe_blockers.append(b)

        evidence: Dict[str, Any] = {
            "plan_id": plan_id,
            "created_at": created_at,
            "intent_id_phase22": p22_intent_id,
            "proposal_id_phase23": p23_proposal_id,
            "phase22_decision": p22_decision,
            "phase23_decision": p23_decision,
            "mapped_decision_reason_code": decision.value,
            "actionable_intent": bool(
                phase22_snapshot.get("actionable_intent", False)
                if isinstance(phase22_snapshot, dict) else False
            ),
            "requires_admin_nod": bool(action.requires_admin_nod),
            "target_server_id": action.target_server_id,
            "target_tool_name": action.target_tool_name,
            "target_match_score": action.match_score,
            "task_context_hash": task_context_hash,
            "caller_kind": caller_kind,
            "sources_degraded": sorted(set(sources_degraded)),
            "resume_marker_inspected": resume_marker_inspected,
            "ticket_state": ticket_state,
            "catalog_package_spec_present": bool(action.catalog_package_spec),
            "catalog_trust_score_set": action.catalog_trust_score is not None,
        }
        evidence = {
            k: v for k, v in evidence.items()
            if k in _EVIDENCE_WHITELIST and v is not None
        }

        plan = AutonomousMCPLoopPlan(
            plan_id=plan_id,
            intent_query_sanitized=intent_sanitized,
            decision=decision,
            action=action,
            capability_plan_snapshot=phase22_snapshot,
            proposal_plan_snapshot=phase23_snapshot,
            blockers=tuple(safe_blockers),
            evidence=evidence,
            created_at=created_at,
            resume_marker_inspected=resume_marker_inspected,
        )
        self._append_audit_if_configured(plan, caller_kind)
        return plan

    # ── Audit local optionnel ──────────────────────────────────────────────

    def _append_audit_if_configured(
        self, plan: AutonomousMCPLoopPlan, caller_kind: str,
    ) -> None:
        if self._audit_log_path is None:
            return
        try:
            self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            return
        event_name = (
            "autonomous_resume_descriptor_completed"
            if plan.resume_marker_inspected is not None
            else "autonomous_plan_completed"
        )
        event = {
            "ts": plan.created_at,
            "event": event_name,
            "phase": "24",
            "plan_id": plan.plan_id,
            "decision": plan.decision.value,
            "action_kind": plan.action.kind.value,
            "caller_kind": caller_kind,
            "blockers_count": len(plan.blockers),
            "sources_degraded_count": len(
                plan.evidence.get("sources_degraded", [])
            ),
            "requires_admin_nod": bool(plan.action.requires_admin_nod),
            "actionable_intent": bool(
                plan.evidence.get("actionable_intent", False)
            ),
            "resume_marker_inspected": plan.resume_marker_inspected,
        }
        try:
            line = json.dumps(event, ensure_ascii=False)
            with self._audit_log_path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            return
