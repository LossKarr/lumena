"""
orchestrator.py — MCP Decision Orchestrator (Phase 13 v2).

Façade de décision pure qui combine :
  - PolicyResolver (résolution policy depuis server_id+tool_name)
  - AutoApproveEngine (Phase 11)
  - ApprovalQueue (Phase 10 — méthode propose())
  - RuntimeWatcher (Phase 12)

Retourne une MCPCallDecision unique. N'EXÉCUTE AUCUN OUTIL MCP.
Le caller (futur câblage runtime) décide quoi faire avec la décision.

DOCTRINE Phase 13 :
  - Aucun câblage runtime : pas de MCPClient.call_tool, pas de
    tool_registry.execute. Façade synchronue de décision.
  - Aucune touche aux modules existants (tool_registry, react, sub_agent,
    MCPSandboxRunner, MCPClient, approval_queue, policy, auto_approve,
    runtime_watcher).
  - Side effects encadrés :
      * enqueue d'un ticket ApprovalQueue.propose() = autorisé (raison d'être)
      * incrément quota auto-approve = via engine sous-jacent
      * audit append-only = autorisé
  - Audit forensique sans PII : OK pour server_id / tool_name / profile /
    caller_kind / policy.value / health.value / codes courts.
    INTERDIT : args values, risk_summary dynamique autre que code court,
    stringification des dépendances.

Table policy Phase 13 (stricte, exhaustive) :
  READ_ONLY                   → APPROVED_POLICY
  EXTERNAL_READ               → APPROVED_POLICY
  LOCAL_WRITE                 → PENDING_APPROVAL
  EXTERNAL_WRITE_RECOVERABLE  → PENDING_APPROVAL
  EXTERNAL_WRITE_IRREVERSIBLE → BLOCKED_POLICY
  SECRETS_AUTH                → BLOCKED_POLICY
  (policy non mappée          → REFUSED_UNKNOWN "policy_unmapped")

Flow décision (8 étapes, ordre déterministe) :
  1. Validate context (profile, server_id, tool_name, caller_kind, args,
     tool_server binding mcp__server__*)
  2. Resolve policy via PolicyResolver
  3. Policy table block check (irreversible / secrets_auth)
  4. Runtime health gate (si registered + CRASH_LOOP/UNHEALTHY)
  5. Auto-approve evaluate (INTEGRITY_INVALID / MATCHED / fallthrough)
  6. Policy table approval for non-blocked (READ_ONLY / EXTERNAL_READ)
  7. Propose() ticket pour write recoverable / local write
  8. Propose() raise → REFUSED_UNKNOWN

Layout disque :
  DATA_DIR/mcp_orchestrator/audit.jsonl
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import (
    Any,
    Dict,
    FrozenSet,
    Optional,
    Protocol,
    runtime_checkable,
)

from loguru import logger

from src.mcp.approval_queue import ApprovalQueue
from src.mcp.auto_approve import AutoApproveDecision, AutoApproveEngine
from src.mcp.policy import MCPPolicy
from src.mcp.runtime_watcher import RuntimeHealth, RuntimeWatcher
from src.utils.paths import DATA_DIR


# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_DIRNAME = "mcp_orchestrator"
_AUDIT_FILENAME = "audit.jsonl"

_PROFILE_RE = re.compile(r"^[a-z0-9_-]+$")
_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")
_TOOL_NAME_RE = re.compile(r"^mcp__[A-Za-z0-9_.\-]+__[A-Za-z0-9_.\-]+$")

_WINDOWS_RESERVED_NAMES: FrozenSet[str] = frozenset({
    "con", "prn", "aux", "nul",
    "com1", "com2", "com3", "com4", "com5",
    "com6", "com7", "com8", "com9",
    "lpt1", "lpt2", "lpt3", "lpt4", "lpt5",
    "lpt6", "lpt7", "lpt8", "lpt9",
})

_VALID_CALLER_KINDS: FrozenSet[str] = frozenset(
    {"react", "codeagent", "autonomy", "scheduler", "daemon", "silent"}
)

# Table policy Phase 13 — stricte et exhaustive.
# Si une nouvelle MCPPolicy apparaît et n'est pas dans cette table,
# decide() retourne REFUSED_UNKNOWN avec reason="policy_unmapped".
_POLICY_DECISION_PHASE13: Dict[MCPPolicy, str] = {
    MCPPolicy.READ_ONLY:                   "approved_policy",
    MCPPolicy.EXTERNAL_READ:               "approved_policy",
    MCPPolicy.LOCAL_WRITE:                 "pending_approval",
    MCPPolicy.EXTERNAL_WRITE_RECOVERABLE:  "pending_approval",
    MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE: "blocked_policy",
    MCPPolicy.SECRETS_AUTH:                "blocked_policy",
}


# ──────────────────────────────────────────────────────────────────────────────
# Exceptions / Enums
# ──────────────────────────────────────────────────────────────────────────────


class MCPOrchestratorError(Exception):
    """Erreur générique de l'orchestrator."""


class _ContextRefused(Exception):
    """Levée en interne par les validateurs de contexte.
    Le code court est utilisé comme reason (sans PII)."""

    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class MCPDecision(Enum):
    APPROVED_AUTO     = "approved_auto"
    APPROVED_POLICY   = "approved_policy"
    PENDING_APPROVAL  = "pending_approval"
    BLOCKED_POLICY    = "blocked_policy"
    BLOCKED_RUNTIME   = "blocked_runtime"
    BLOCKED_INTEGRITY = "blocked_integrity"
    REFUSED_CONTEXT   = "refused_context"
    REFUSED_UNKNOWN   = "refused_unknown"


# ──────────────────────────────────────────────────────────────────────────────
# Dataclasses
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MCPCallContext:
    server_id: str
    tool_name: str
    args: Dict[str, Any]
    profile: str
    caller_kind: str


@dataclass(frozen=True)
class MCPCallDecision:
    decision: MCPDecision
    server_id: str
    tool_name: str
    reason: str
    policy: Optional[MCPPolicy] = None
    auto_approve_decision: Optional[AutoApproveDecision] = None
    auto_approve_pattern_id: Optional[str] = None
    approval_ticket_id: Optional[str] = None
    runtime_health: Optional[RuntimeHealth] = None


# ──────────────────────────────────────────────────────────────────────────────
# PolicyResolver Protocol
# ──────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class PolicyResolver(Protocol):
    """Interface attendue par l'orchestrator pour résoudre une policy.

    Implémentation concrète hors scope Phase 13 (sera Phase 14+ et
    pourra wrapper tool_registry sans le modifier).
    """

    def resolve(
        self, server_id: str, tool_name: str
    ) -> Optional[MCPPolicy]: ...


# ──────────────────────────────────────────────────────────────────────────────
# Validators internes (raise _ContextRefused avec code court)
# ──────────────────────────────────────────────────────────────────────────────


def _validate_profile(profile: Any) -> None:
    if not isinstance(profile, str) or not _PROFILE_RE.match(profile):
        raise _ContextRefused("context_invalid:profile")


def _validate_server_id(server_id: Any) -> None:
    if not isinstance(server_id, str) or not _SERVER_ID_RE.match(server_id):
        raise _ContextRefused("context_invalid:server_id")
    if ".." in server_id or "/" in server_id or "\\" in server_id:
        raise _ContextRefused("context_invalid:server_id")
    stem = server_id.split(".", 1)[0]
    if stem in _WINDOWS_RESERVED_NAMES:
        raise _ContextRefused("context_invalid:server_id")


def _validate_tool_name(tool_name: Any) -> None:
    if not isinstance(tool_name, str) or not _TOOL_NAME_RE.match(tool_name):
        raise _ContextRefused("context_invalid:tool_name")


def _validate_caller_kind(caller_kind: Any) -> None:
    if not isinstance(caller_kind, str) or caller_kind not in _VALID_CALLER_KINDS:
        raise _ContextRefused("context_invalid:caller_kind")


def _validate_args(args: Any) -> None:
    if not isinstance(args, dict):
        raise _ContextRefused("context_invalid:args")


def _validate_tool_server_binding(server_id: str, tool_name: str) -> None:
    """Anti-confused-deputy : tool_name doit cibler server_id."""
    expected_prefix = f"mcp__{server_id}__"
    if not tool_name.startswith(expected_prefix):
        raise _ContextRefused("context_invalid:tool_server_mismatch")


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ──────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ──────────────────────────────────────────────────────────────────────────────


class MCPOrchestrator:
    """Facade pure de décision Phase 13."""

    def __init__(
        self,
        policy_resolver: PolicyResolver,
        auto_approve_engine: AutoApproveEngine,
        approval_queue: ApprovalQueue,
        runtime_watcher: RuntimeWatcher,
        audit_log_path: Optional[Path] = None,
        block_unhealthy_runtime: bool = True,
    ):
        if policy_resolver is None or not callable(
            getattr(policy_resolver, "resolve", None)
        ):
            raise MCPOrchestratorError(
                "policy_resolver must implement resolve(server_id, tool_name)"
            )
        if auto_approve_engine is None:
            raise MCPOrchestratorError("auto_approve_engine must not be None")
        if approval_queue is None:
            raise MCPOrchestratorError("approval_queue must not be None")
        if runtime_watcher is None:
            raise MCPOrchestratorError("runtime_watcher must not be None")

        self._policy_resolver = policy_resolver
        self._auto_approve = auto_approve_engine
        self._approval_queue = approval_queue
        self._watcher = runtime_watcher
        self._audit_log_path = audit_log_path or (
            DATA_DIR / _DEFAULT_DIRNAME / _AUDIT_FILENAME
        )
        self._block_unhealthy_runtime = bool(block_unhealthy_runtime)

        self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def audit_log_path(self) -> Path:
        return self._audit_log_path

    @property
    def block_unhealthy_runtime(self) -> bool:
        return self._block_unhealthy_runtime

    # ── Audit (sans PII) ──────────────────────────────────────────────────

    def _append_audit(self, event: str, **fields: Any) -> None:
        """Append-only au audit.jsonl.

        Whitelist stricte des champs identifiants autorisés :
          server_id, tool_name, profile, caller_kind, policy, health,
          decision, reason, pattern_id, ticket_id, auto_decision
        AUCUNE valeur d'args, AUCUN stringification des dépendances.
        """
        record = {"ts": _now_iso(), "event": event, **fields}
        try:
            self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._audit_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning(f"[mcp.orchestrator] audit write failed: {e}")

    # ── decide() ──────────────────────────────────────────────────────────

    def decide(self, ctx: MCPCallContext) -> MCPCallDecision:
        """Décision Phase 13 — n'exécute AUCUN outil MCP."""
        # ──── Étape 1 : Validate context ────
        try:
            _validate_profile(ctx.profile)
            _validate_server_id(ctx.server_id)
            _validate_tool_name(ctx.tool_name)
            _validate_caller_kind(ctx.caller_kind)
            _validate_args(ctx.args)
            _validate_tool_server_binding(ctx.server_id, ctx.tool_name)
        except _ContextRefused as e:
            # NB : on évite de logger server_id/tool_name si la validation
            # de l'un de ces champs a échoué (pour ne pas leaker une valeur
            # qui n'a pas passé la regex). On ne log que le code court.
            self._append_audit(
                "context_refused",
                reason=e.code,
                caller_kind=ctx.caller_kind
                    if e.code != "context_invalid:caller_kind" else None,
                profile=ctx.profile
                    if e.code != "context_invalid:profile" else None,
            )
            return MCPCallDecision(
                decision=MCPDecision.REFUSED_CONTEXT,
                server_id=ctx.server_id,
                tool_name=ctx.tool_name,
                reason=e.code,
            )

        # ──── Étape 2 : Resolve policy ────
        try:
            policy = self._policy_resolver.resolve(ctx.server_id, ctx.tool_name)
        except Exception:  # noqa: BLE001
            policy = None
        if not isinstance(policy, MCPPolicy):
            self._append_audit(
                "policy_unresolved",
                server_id=ctx.server_id,
                tool_name=ctx.tool_name,
                profile=ctx.profile,
                caller_kind=ctx.caller_kind,
                reason="policy_unresolved",
            )
            return MCPCallDecision(
                decision=MCPDecision.REFUSED_UNKNOWN,
                server_id=ctx.server_id,
                tool_name=ctx.tool_name,
                reason="policy_unresolved",
            )

        # ──── Étape 3 : Policy table check ────
        table_value = _POLICY_DECISION_PHASE13.get(policy)
        if table_value is None:
            # Policy non mappée dans la table Phase 13
            self._append_audit(
                "policy_unmapped",
                server_id=ctx.server_id,
                tool_name=ctx.tool_name,
                profile=ctx.profile,
                caller_kind=ctx.caller_kind,
                policy=policy.value,
                reason="policy_unmapped",
            )
            return MCPCallDecision(
                decision=MCPDecision.REFUSED_UNKNOWN,
                server_id=ctx.server_id,
                tool_name=ctx.tool_name,
                reason="policy_unmapped",
                policy=policy,
            )

        if table_value == "blocked_policy":
            reason = f"policy_blocked:{policy.value}"
            self._append_audit(
                "policy_blocked",
                server_id=ctx.server_id,
                tool_name=ctx.tool_name,
                profile=ctx.profile,
                caller_kind=ctx.caller_kind,
                policy=policy.value,
                reason=reason,
            )
            return MCPCallDecision(
                decision=MCPDecision.BLOCKED_POLICY,
                server_id=ctx.server_id,
                tool_name=ctx.tool_name,
                reason=reason,
                policy=policy,
            )

        # ──── Étape 4 : Runtime health gate ────
        runtime_health: Optional[RuntimeHealth] = None
        if self._block_unhealthy_runtime:
            try:
                registered = self._watcher.is_registered(ctx.server_id)
            except Exception:  # noqa: BLE001
                registered = False
            if registered:
                try:
                    report = self._watcher.get_report(ctx.server_id)
                    runtime_health = report.health
                except Exception:  # noqa: BLE001
                    runtime_health = None
                if runtime_health in (
                    RuntimeHealth.CRASH_LOOP,
                    RuntimeHealth.UNHEALTHY,
                ):
                    reason = f"runtime_health:{runtime_health.value}"
                    self._append_audit(
                        "runtime_blocked",
                        server_id=ctx.server_id,
                        tool_name=ctx.tool_name,
                        profile=ctx.profile,
                        caller_kind=ctx.caller_kind,
                        policy=policy.value,
                        health=runtime_health.value,
                        reason=reason,
                    )
                    return MCPCallDecision(
                        decision=MCPDecision.BLOCKED_RUNTIME,
                        server_id=ctx.server_id,
                        tool_name=ctx.tool_name,
                        reason=reason,
                        policy=policy,
                        runtime_health=runtime_health,
                    )

        # ──── Étape 5 : Auto-approve evaluation ────
        try:
            ev = self._auto_approve.evaluate(
                profile=ctx.profile,
                tool_name=ctx.tool_name,
                args=ctx.args,
                policy=policy,
                caller_kind=ctx.caller_kind,
            )
        except Exception:  # noqa: BLE001
            # Si l'engine raise (ex: args invalide), on traite comme
            # REFUSED_UNKNOWN avec code court — pas de stringification d'exc.
            self._append_audit(
                "auto_approve_error",
                server_id=ctx.server_id,
                tool_name=ctx.tool_name,
                profile=ctx.profile,
                caller_kind=ctx.caller_kind,
                policy=policy.value,
                reason="auto_approve_error",
            )
            return MCPCallDecision(
                decision=MCPDecision.REFUSED_UNKNOWN,
                server_id=ctx.server_id,
                tool_name=ctx.tool_name,
                reason="auto_approve_error",
                policy=policy,
                runtime_health=runtime_health,
            )

        if ev.decision == AutoApproveDecision.INTEGRITY_INVALID:
            reason = "auto_integrity_invalid"
            self._append_audit(
                "integrity_blocked",
                server_id=ctx.server_id,
                tool_name=ctx.tool_name,
                profile=ctx.profile,
                caller_kind=ctx.caller_kind,
                policy=policy.value,
                auto_decision=ev.decision.value,
                reason=reason,
            )
            return MCPCallDecision(
                decision=MCPDecision.BLOCKED_INTEGRITY,
                server_id=ctx.server_id,
                tool_name=ctx.tool_name,
                reason=reason,
                policy=policy,
                auto_approve_decision=ev.decision,
                runtime_health=runtime_health,
            )

        if ev.decision == AutoApproveDecision.MATCHED:
            reason = "auto_matched"
            self._append_audit(
                "auto_matched",
                server_id=ctx.server_id,
                tool_name=ctx.tool_name,
                profile=ctx.profile,
                caller_kind=ctx.caller_kind,
                policy=policy.value,
                auto_decision=ev.decision.value,
                pattern_id=ev.matched_pattern_id,
                reason=reason,
            )
            return MCPCallDecision(
                decision=MCPDecision.APPROVED_AUTO,
                server_id=ctx.server_id,
                tool_name=ctx.tool_name,
                reason=reason,
                policy=policy,
                auto_approve_decision=ev.decision,
                auto_approve_pattern_id=ev.matched_pattern_id,
                runtime_health=runtime_health,
            )

        # ──── Étape 6 : Policy table approved_policy ────
        if table_value == "approved_policy":
            reason = f"policy_allowed:{policy.value}"
            self._append_audit(
                "policy_allowed",
                server_id=ctx.server_id,
                tool_name=ctx.tool_name,
                profile=ctx.profile,
                caller_kind=ctx.caller_kind,
                policy=policy.value,
                auto_decision=ev.decision.value,
                reason=reason,
            )
            return MCPCallDecision(
                decision=MCPDecision.APPROVED_POLICY,
                server_id=ctx.server_id,
                tool_name=ctx.tool_name,
                reason=reason,
                policy=policy,
                auto_approve_decision=ev.decision,
                runtime_health=runtime_health,
            )

        # ──── Étape 7 : Pending approval via propose() ────
        # table_value == "pending_approval" garanti par la complétude de la table.
        try:
            ticket_id = self._approval_queue.propose(
                tool_name=ctx.tool_name,
                args=ctx.args,
                policy=policy,
                caller_kind=ctx.caller_kind,
                risk_summary=f"mcp_pending_approval:{policy.value}",
            )
        except Exception:  # noqa: BLE001
            # ──── Étape 8 : propose() raise → REFUSED_UNKNOWN ────
            self._append_audit(
                "propose_failed",
                server_id=ctx.server_id,
                tool_name=ctx.tool_name,
                profile=ctx.profile,
                caller_kind=ctx.caller_kind,
                policy=policy.value,
                reason="propose_failed",
            )
            return MCPCallDecision(
                decision=MCPDecision.REFUSED_UNKNOWN,
                server_id=ctx.server_id,
                tool_name=ctx.tool_name,
                reason="propose_failed",
                policy=policy,
                auto_approve_decision=ev.decision,
                runtime_health=runtime_health,
            )

        if not isinstance(ticket_id, str) or not ticket_id:
            self._append_audit(
                "propose_failed",
                server_id=ctx.server_id,
                tool_name=ctx.tool_name,
                profile=ctx.profile,
                caller_kind=ctx.caller_kind,
                policy=policy.value,
                reason="propose_failed",
            )
            return MCPCallDecision(
                decision=MCPDecision.REFUSED_UNKNOWN,
                server_id=ctx.server_id,
                tool_name=ctx.tool_name,
                reason="propose_failed",
                policy=policy,
                auto_approve_decision=ev.decision,
                runtime_health=runtime_health,
            )

        reason = "enqueued_propose"
        self._append_audit(
            "pending_enqueued",
            server_id=ctx.server_id,
            tool_name=ctx.tool_name,
            profile=ctx.profile,
            caller_kind=ctx.caller_kind,
            policy=policy.value,
            auto_decision=ev.decision.value,
            ticket_id=ticket_id,
            reason=reason,
        )
        return MCPCallDecision(
            decision=MCPDecision.PENDING_APPROVAL,
            server_id=ctx.server_id,
            tool_name=ctx.tool_name,
            reason=reason,
            policy=policy,
            auto_approve_decision=ev.decision,
            approval_ticket_id=ticket_id,
            runtime_health=runtime_health,
        )
