"""
Tests Phase 25 — MCPExecutionBridge.

Couvre :
  - Structure (11 décisions, 5 action kinds, dataclasses frozen)
  - request_action_for_plan : mapping Phase 24 → Phase 25 (toutes branches)
  - execute_after_approval : validation ApprovalResult + cross-check args
    + invocation orchestrators
  - describe_action_state : lecture pure
  - Idempotency / dédup pending
  - Live mode + kill switches
  - Cross-check args spécifiques chemin (action="activate", clés Phase 18)
  - Anti-leak markers SECRET_*
  - Anti-mutation grep + imports interdits + policy reference interdite
  - Tokens autorisés : .propose_install, .propose_activation,
    .execute_approved_install, .activate
  - Sources optionnelles
  - UTF-8 anti-mojibake
  - Audit local optionnel
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

# Valid UUID4 hex test constants (générés par uuid.uuid4() — version + variant
# RFC 4122 réels, donc acceptés par _is_valid_action_id strict).
_TID_A = "e9953b6ad4ae430a8e1d4bf425f55e29"
_TID_B = "d6576736fd314dfda42d47b6e78f67d3"
_TID_C = "b9cbff08fc9a492680c74a8b80b113e7"
_TID_D = "5909bf2e633f4eacae06e42a97ac0fa0"
_TID_E = "bee7bfa189254b11bb808bd545704cf4"

from src.mcp.execution_bridge import (
    ActivationServiceLike,
    ApprovalQueueReadLike,
    CatalogAddOrchestratorLike,
    CatalogReadLike,
    InstallOrchestratorLike,
    MCPExecutionBridge,
    MCPExecutionBridgeDeps,
    Phase25BridgeAction,
    Phase25BridgeActionKind,
    Phase25BridgeDecision,
    Phase25BridgePlan,
    Phase25BridgePlanBlocker,
    ToolRegistryReadLike,
    _BLOCKER_CODES,
    _EVIDENCE_WHITELIST,
    _INSTALL_REQUIRED_ARG_KEYS,
    _RISK_SUMMARY_WHITELIST,
    _is_valid_action_id,
    _is_valid_server_id,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers fakes
# ──────────────────────────────────────────────────────────────────────────────


def _phase24_action(
    *, kind: str = "none", server_id: Optional[str] = None,
    catalog_display_name: Optional[str] = None,
    catalog_package_spec: Optional[str] = None,
    catalog_version: Optional[str] = None,
    catalog_trust_score: Optional[int] = None,
):
    return SimpleNamespace(
        kind=SimpleNamespace(value=kind),
        target_server_id=server_id,
        proposed_target_server_id=server_id,
        catalog_display_name=catalog_display_name,
        catalog_package_spec=catalog_package_spec,
        catalog_version=catalog_version,
        catalog_trust_score=catalog_trust_score,
    )


def _phase24_plan(*, decision: str, action_kind: str = "none",
                  server_id: Optional[str] = None,
                  catalog_display_name: Optional[str] = None,
                  catalog_package_spec: Optional[str] = None,
                  catalog_version: Optional[str] = None,
                  catalog_trust_score: Optional[int] = None):
    return SimpleNamespace(
        decision=SimpleNamespace(value=decision),
        action=_phase24_action(
            kind=action_kind,
            server_id=server_id,
            catalog_display_name=catalog_display_name,
            catalog_package_spec=catalog_package_spec,
            catalog_version=catalog_version,
            catalog_trust_score=catalog_trust_score,
        ),
    )


class FakeCatalog:
    def __init__(self, servers: Dict[str, str]) -> None:
        # servers : {server_id: status_str}
        self._servers = servers

    def get_server(self, server_id: str) -> Optional[Any]:
        st = self._servers.get(server_id)
        if st is None:
            return None
        return SimpleNamespace(status=SimpleNamespace(value=st))

    def list_servers(self, include_removed: bool = False):
        out = []
        for sid, st in self._servers.items():
            if not include_removed and st == "removed":
                continue
            out.append(SimpleNamespace(
                server_id=sid, status=SimpleNamespace(value=st),
            ))
        return out


class FakeToolRegistry:
    def __init__(self, handlers: Dict[str, str]) -> None:
        # handlers : {handler_name: server_id}
        self._handlers = handlers

    def list_dynamic_handlers(self) -> List[str]:
        return sorted(self._handlers.keys())

    def is_dynamic_handler(self, name: str) -> bool:
        return name in self._handlers

    def get_dynamic_handler_provenance(self, name: str):
        sid = self._handlers.get(name)
        if sid is None:
            return None
        return {"server_id": sid}


class FakeApprovalQueue:
    def __init__(self, pending: Optional[List[Dict[str, str]]] = None) -> None:
        # pending : list of {id, tool_name}
        self._pending = [
            SimpleNamespace(id=p["id"], tool_name=p["tool_name"])
            for p in (pending or [])
        ]

    def list_pending(self):
        return list(self._pending)

    def get(self, action_id: str):
        for t in self._pending:
            if t.id == action_id:
                return t
        return None


class FakeInstallOrchestrator:
    def __init__(
        self,
        proposal_ticket_id: Optional[str] = None,
        execute_success: bool = True,
        raises_on_execute: bool = False,
        raises_on_propose: bool = False,
    ) -> None:
        self._ptid = proposal_ticket_id
        self._success = execute_success
        self._raises_exec = raises_on_execute
        self._raises_prop = raises_on_propose

    def propose_install(self, server_id, *, caller_kind="silent"):
        if self._raises_prop:
            raise RuntimeError("boom")
        return SimpleNamespace(
            server_id=server_id,
            approval_ticket_id=self._ptid or _TID_A,
        )

    def execute_approved_install(self, server_id, approval_result):
        if self._raises_exec:
            raise RuntimeError("boom")
        return SimpleNamespace(
            server_id=server_id,
            success=self._success,
            reason="ok" if self._success else "fail",
        )


class FakeActivationService:
    def __init__(
        self,
        proposal_ticket_id: Optional[str] = None,
        activate_success: bool = True,
        raises_on_activate: bool = False,
        raises_on_propose: bool = False,
    ) -> None:
        self._ptid = proposal_ticket_id
        self._success = activate_success
        self._raises_act = raises_on_activate
        self._raises_prop = raises_on_propose

    def propose_activation(self, server_id, *, caller_kind="silent"):
        if self._raises_prop:
            raise RuntimeError("boom")
        return SimpleNamespace(
            server_id=server_id,
            approval_ticket_id=self._ptid or _TID_B,
        )

    def activate(self, server_id, approval_result=None):
        if self._raises_act:
            raise RuntimeError("boom")
        return SimpleNamespace(
            server_id=server_id,
            success=self._success,
            reason="ok" if self._success else "fail",
        )


class FakeCatalogAddOrchestrator:
    def __init__(
        self,
        proposal_ticket_id: Optional[str] = None,
        execute_success: bool = True,
        raises_on_execute: bool = False,
        raises_on_propose: bool = False,
    ) -> None:
        self._ptid = proposal_ticket_id
        self._success = execute_success
        self._raises_exec = raises_on_execute
        self._raises_prop = raises_on_propose
        self.proposals = []
        self.executions = []

    def propose_catalog_add(self, proposal, *, caller_kind="react", dry_run=True):
        if self._raises_prop:
            raise RuntimeError("boom")
        self.proposals.append((proposal, caller_kind, dry_run))
        return SimpleNamespace(
            server_id=proposal.server_id,
            approval_ticket_id=self._ptid or _TID_C,
        )

    def execute_approved_catalog_add(self, server_id, approval_result, *, dry_run=True):
        if self._raises_exec:
            raise RuntimeError("boom")
        self.executions.append((server_id, approval_result, dry_run))
        return SimpleNamespace(
            server_id=server_id,
            success=self._success,
            reason="ok" if self._success else "fail",
        )


def _approval_result(
    *, decision: str = "approved",
    server_id: Optional[str] = "alice",
    action: Optional[str] = None,
    install_args: bool = False,
    extra_args: Optional[Dict[str, Any]] = None,
):
    args: Dict[str, Any] = {}
    if server_id is not None:
        args["server_id"] = server_id
    if action is not None:
        args["action"] = action
    if action == "catalog_add":
        args.update({
            "display_name": "Alice MCP",
            "package_spec": "npm:alice-mcp",
            "version": "1.0.0",
            "trust_score": 80,
            "owner_profile": "lumena",
        })
    if install_args:
        args.update({
            "transport": "npm",
            "package_name": "pkg",
            "package_spec": "npm:pkg",
            "version": "1.0.0",
            "trust_score": 80,
        })
    if extra_args:
        args.update(extra_args)
    return SimpleNamespace(
        decision=SimpleNamespace(value=decision),
        args=args,
        reason=None,
    )


def _empty_deps() -> MCPExecutionBridgeDeps:
    return MCPExecutionBridgeDeps()


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Structure
# ══════════════════════════════════════════════════════════════════════════════


def test_decision_has_catalog_add_values():
    expected = {
        "NO_TICKET_NEEDED", "TICKET_WOULD_BE_PROPOSED", "TICKET_PROPOSED",
        "TICKET_DESCRIPTIVE_ONLY", "EXECUTION_WOULD_HAPPEN",
        "EXECUTED_SUCCESS_CATALOG_ADD",
        "EXECUTED_SUCCESS_INSTALL", "EXECUTED_SUCCESS_ACTIVATE",
        "EXECUTED_FAILURE", "ALREADY_APPLIED", "WAITING_APPROVAL", "BLOCKED",
    }
    assert {d.name for d in Phase25BridgeDecision} == expected


def test_action_kind_has_catalog_add_values():
    expected = {
        "NONE", "PROPOSE_CATALOG_ADD", "PROPOSE_INSTALL",
        "PROPOSE_ACTIVATION", "EXECUTE_CATALOG_ADD", "EXECUTE_INSTALL",
        "EXECUTE_ACTIVATION",
    }
    assert {k.name for k in Phase25BridgeActionKind} == expected


def test_action_frozen():
    a = Phase25BridgeAction(
        kind=Phase25BridgeActionKind.NONE,
        target_server_id=None, risk_summary="none",
        proposed_ticket_action_id=None, invoked_orchestrator="",
    )
    with pytest.raises(Exception):
        a.kind = Phase25BridgeActionKind.PROPOSE_INSTALL  # type: ignore[misc]


def test_bridge_rejects_non_deps():
    with pytest.raises(TypeError):
        MCPExecutionBridge(deps="x")  # type: ignore[arg-type]


def test_bridge_rejects_non_path_audit():
    with pytest.raises(TypeError):
        MCPExecutionBridge(_empty_deps(), audit_log_path="/x")  # type: ignore[arg-type]


def test_bridge_plan_id_uuid4():
    plan = MCPExecutionBridge(_empty_deps()).request_action_for_plan(
        _phase24_plan(decision="ready_to_use_existing_capability"),
        caller_kind="test",
    )
    assert re.match(r"^[0-9a-f]{32}$", plan.bridge_plan_id)


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — server_id regex Phase 14 réelle
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("sid,expected", [
    ("alice", True),
    ("alice_srv", True),
    ("alice-srv", True),
    ("alice.v2", True),
    ("a", True),
    ("a" * 64, True),
    # Refusés regex
    ("Alice", False),               # uppercase initial
    ("_alice", False),              # underscore initial
    ("-alice", False),              # tiret initial
    (".alice", False),              # dot initial
    ("alice/srv", False),           # slash
    ("alice srv", False),           # espace
    ("a" * 65, False),              # too long
    ("", False),
    (None, False),
    # Refusés Phase 14 complète
    ("alice..bob", False),          # ".."
    ("alice\\srv", False),          # backslash
    ("con", False),                 # Windows reserved
    ("prn", False),
    ("aux", False),
    ("nul", False),
    ("con.txt", False),             # Windows reserved avec extension
    ("com1", False),
    ("lpt9", False),
])
def test_server_id_phase14_validation(sid, expected):
    assert _is_valid_server_id(sid) is expected


# ──────────────────────────────────────────────────────────────────────────────
# UUID4 strict validation
# ──────────────────────────────────────────────────────────────────────────────


import uuid as _uuid_mod


def test_action_id_real_uuid4_accepted():
    assert _is_valid_action_id(_uuid_mod.uuid4().hex) is True


def test_action_id_all_zeros_refused():
    # 32 hex mais pas UUID4 valide (version 0)
    assert _is_valid_action_id("0" * 32) is False


def test_action_id_uppercase_refused():
    raw = _uuid_mod.uuid4().hex.upper()
    assert _is_valid_action_id(raw) is False


def test_action_id_uuid1_refused():
    raw = _uuid_mod.uuid1().hex
    assert _is_valid_action_id(raw) is False


def test_action_id_non_uuid_hex_refused():
    # 32 caractères hex mais composition UUID4 invalide (version digit absent)
    bad = "a" * 32
    assert _is_valid_action_id(bad) is False


def test_action_id_with_dashes_refused():
    raw = str(_uuid_mod.uuid4())  # avec tirets
    assert _is_valid_action_id(raw) is False


def test_action_id_non_string_refused():
    assert _is_valid_action_id(None) is False
    assert _is_valid_action_id(123) is False
    assert _is_valid_action_id(b"a" * 32) is False


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — request_action_for_plan : mapping Phase 24 → Phase 25
# ══════════════════════════════════════════════════════════════════════════════


def test_ready_to_use_yields_no_ticket_needed():
    plan = MCPExecutionBridge(_empty_deps()).request_action_for_plan(
        _phase24_plan(decision="ready_to_use_existing_capability"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.NO_TICKET_NEEDED
    assert plan.action.kind == Phase25BridgeActionKind.NONE


def test_needs_install_no_catalog_dep_blocked():
    plan = MCPExecutionBridge(_empty_deps()).request_action_for_plan(
        _phase24_plan(decision="needs_install_approval", server_id="alice"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(b.blocker_code == "no_catalog_dep" for b in plan.blockers)


def test_needs_install_invalid_server_id():
    deps = MCPExecutionBridgeDeps(catalog_read=FakeCatalog({}))
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_install_approval", server_id="BAD"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "server_id_invalid_format" for b in plan.blockers
    )


def test_needs_install_quarantined_blocked():
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "quarantined"}),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_install_approval", server_id="alice"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(b.blocker_code == "catalog_quarantined" for b in plan.blockers)


def test_needs_install_already_installed_already_applied():
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "installed"}),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_install_approval", server_id="alice"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.ALREADY_APPLIED


def test_needs_install_already_active_already_applied():
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "active"}),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_install_approval", server_id="alice"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.ALREADY_APPLIED


def test_needs_install_dedup_pending():
    pending = [{"id": _TID_A, "tool_name": "mcp_install:alice"}]
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "declared"}),
        approval_queue_read=FakeApprovalQueue(pending=pending),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_install_approval", server_id="alice"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.WAITING_APPROVAL
    assert plan.action.proposed_ticket_action_id == _TID_A
    assert plan.evidence.get("dedup_match_pending") is True


def test_needs_install_dry_run_describes():
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "declared"}),
        approval_queue_read=FakeApprovalQueue(pending=[]),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_install_approval", server_id="alice"),
        caller_kind="test", dry_run=True,
    )
    assert plan.decision == Phase25BridgeDecision.TICKET_WOULD_BE_PROPOSED
    assert plan.action.kind == Phase25BridgeActionKind.PROPOSE_INSTALL


def test_needs_install_live_no_live_mode_blocked(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "0")
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "declared"}),
        approval_queue_read=FakeApprovalQueue(pending=[]),
        install_orchestrator=FakeInstallOrchestrator(),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_install_approval", server_id="alice"),
        caller_kind="test", dry_run=False,
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(b.blocker_code == "live_mode_disabled" for b in plan.blockers)


def test_needs_install_live_kill_switch_blocked(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_INSTALL_DISABLED", "1")
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "declared"}),
        approval_queue_read=FakeApprovalQueue(pending=[]),
        install_orchestrator=FakeInstallOrchestrator(),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_install_approval", server_id="alice"),
        caller_kind="test", dry_run=False,
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "install_kill_switch_active" for b in plan.blockers
    )


def test_needs_install_live_proposes(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_INSTALL_DISABLED", "0")
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "declared"}),
        approval_queue_read=FakeApprovalQueue(pending=[]),
        install_orchestrator=FakeInstallOrchestrator(
            proposal_ticket_id=_TID_C,
        ),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_install_approval", server_id="alice"),
        caller_kind="test", dry_run=False,
    )
    assert plan.decision == Phase25BridgeDecision.TICKET_PROPOSED
    assert plan.action.proposed_ticket_action_id == _TID_C


def test_needs_install_propose_raises_blocker(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "declared"}),
        approval_queue_read=FakeApprovalQueue(pending=[]),
        install_orchestrator=FakeInstallOrchestrator(raises_on_propose=True),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_install_approval", server_id="alice"),
        caller_kind="test", dry_run=False,
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(b.blocker_code == "install_failed" for b in plan.blockers)


# Activation path


def test_needs_activation_declared_blocked_needs_install_first():
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "declared"}),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_activation_approval", server_id="alice"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(b.blocker_code == "needs_install_first" for b in plan.blockers)


def test_needs_activation_active_already_applied():
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "active"}),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_activation_approval", server_id="alice"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.ALREADY_APPLIED


def test_needs_activation_installed_handlers_present_already_applied():
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "installed"}),
        tool_registry_read=FakeToolRegistry({"alice_tool": "alice"}),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_activation_approval", server_id="alice"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.ALREADY_APPLIED


def test_needs_activation_dedup_pending():
    pending = [{"id": _TID_D, "tool_name": "mcp_activate:alice"}]
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "installed"}),
        tool_registry_read=FakeToolRegistry({}),
        approval_queue_read=FakeApprovalQueue(pending=pending),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_activation_approval", server_id="alice"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.WAITING_APPROVAL
    assert plan.action.proposed_ticket_action_id == _TID_D


def test_needs_activation_dry_run_describes():
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "installed"}),
        tool_registry_read=FakeToolRegistry({}),
        approval_queue_read=FakeApprovalQueue(pending=[]),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_activation_approval", server_id="alice"),
        caller_kind="test", dry_run=True,
    )
    assert plan.decision == Phase25BridgeDecision.TICKET_WOULD_BE_PROPOSED
    assert plan.action.kind == Phase25BridgeActionKind.PROPOSE_ACTIVATION


def test_needs_activation_live_proposes(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_ACTIVATION_DISABLED", "0")
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "installed"}),
        tool_registry_read=FakeToolRegistry({}),
        approval_queue_read=FakeApprovalQueue(pending=[]),
        activation_service=FakeActivationService(
            proposal_ticket_id=_TID_E,
        ),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_activation_approval", server_id="alice"),
        caller_kind="test", dry_run=False,
    )
    assert plan.decision == Phase25BridgeDecision.TICKET_PROPOSED
    assert plan.action.proposed_ticket_action_id == _TID_E


def test_needs_activation_activation_kill_switch_blocked(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_ACTIVATION_DISABLED", "1")
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "installed"}),
        tool_registry_read=FakeToolRegistry({}),
        approval_queue_read=FakeApprovalQueue(pending=[]),
        activation_service=FakeActivationService(),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_activation_approval", server_id="alice"),
        caller_kind="test", dry_run=False,
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "activation_kill_switch_active" for b in plan.blockers
    )


# Catalog / local_creation / waiting_approval / blocked


def test_needs_catalog_approval_invalid_proposal_blocked():
    deps = MCPExecutionBridgeDeps(catalog_read=FakeCatalog({}))
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_catalog_approval", server_id="alice"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(b.blocker_code == "catalog_proposal_invalid" for b in plan.blockers)


def test_needs_catalog_approval_dry_run_would_propose():
    deps = MCPExecutionBridgeDeps(catalog_read=FakeCatalog({}))
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(
            decision="needs_catalog_approval",
            server_id="alice",
            catalog_display_name="Alice MCP",
            catalog_package_spec="npm:alice-mcp",
            catalog_version="1.0.0",
            catalog_trust_score=80,
        ),
        caller_kind="test",
        dry_run=True,
    )
    assert plan.decision == Phase25BridgeDecision.TICKET_WOULD_BE_PROPOSED
    assert plan.action.kind == Phase25BridgeActionKind.PROPOSE_CATALOG_ADD
    assert plan.action.risk_summary == "catalog_add_required"


def test_needs_catalog_approval_live_proposes_catalog_ticket(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    cao = FakeCatalogAddOrchestrator(proposal_ticket_id=_TID_C)
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({}),
        approval_queue_read=FakeApprovalQueue(pending=[]),
        catalog_add_orchestrator=cao,
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(
            decision="needs_catalog_approval",
            server_id="alice",
            catalog_display_name="Alice MCP",
            catalog_package_spec="npm:alice-mcp",
            catalog_version="1.0.0",
            catalog_trust_score=80,
        ),
        caller_kind="test",
        dry_run=False,
    )
    assert plan.decision == Phase25BridgeDecision.TICKET_PROPOSED
    assert plan.action.kind == Phase25BridgeActionKind.PROPOSE_CATALOG_ADD
    assert plan.action.proposed_ticket_action_id == _TID_C
    assert cao.proposals[0][0].server_id == "alice"
    assert cao.proposals[0][0].package_spec == "npm:alice-mcp"


def test_execute_live_catalog_add_success(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    cao = FakeCatalogAddOrchestrator(execute_success=True)
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({}),
        catalog_add_orchestrator=cao,
    )
    ar = _approval_result(action="catalog_add")
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_C, ar, "alice", caller_kind="test", dry_run=False,
    )
    assert plan.decision == Phase25BridgeDecision.EXECUTED_SUCCESS_CATALOG_ADD
    assert plan.action.kind == Phase25BridgeActionKind.EXECUTE_CATALOG_ADD
    assert plan.action.invoked_orchestrator == "catalog_add"
    assert cao.executions[0][0] == "alice"


def test_needs_local_creation_descriptive():
    plan = MCPExecutionBridge(_empty_deps()).request_action_for_plan(
        _phase24_plan(decision="needs_local_creation"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.TICKET_DESCRIPTIVE_ONLY
    assert plan.action.risk_summary == "local_creation_required"


def test_waiting_approval_passthrough():
    plan = MCPExecutionBridge(_empty_deps()).request_action_for_plan(
        _phase24_plan(decision="waiting_approval"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.WAITING_APPROVAL


def test_blocked_phase24_passthrough():
    plan = MCPExecutionBridge(_empty_deps()).request_action_for_plan(
        _phase24_plan(decision="blocked"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "phase24_blocked_or_no_path" for b in plan.blockers
    )


def test_no_safe_path_phase24_blocked():
    plan = MCPExecutionBridge(_empty_deps()).request_action_for_plan(
        _phase24_plan(decision="no_safe_path"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED


# ──────────────────────────────────────────────────────────────────────────────
# Unexpected catalog state (install)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["removed", "unknown_state"])
def test_needs_install_unexpected_state_blocked(status):
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": status}),
        approval_queue_read=FakeApprovalQueue(pending=[]),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_install_approval", server_id="alice"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "unexpected_catalog_state" for b in plan.blockers
    )


def test_needs_install_unknown_catalog_blocked():
    # catalog ne contient pas le server → unknown
    deps = MCPExecutionBridgeDeps(catalog_read=FakeCatalog({}))
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_install_approval", server_id="alice"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "unexpected_catalog_state" for b in plan.blockers
    )


def test_needs_install_unexpected_state_no_propose_install_called(monkeypatch):
    """Test live mode : si catalog state inattendu, propose_install
    n'est JAMAIS appelé.
    """
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    io = MagicMock(spec=InstallOrchestratorLike)
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "removed"}),
        approval_queue_read=FakeApprovalQueue(pending=[]),
        install_orchestrator=io,
    )
    MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_install_approval", server_id="alice"),
        caller_kind="test", dry_run=False,
    )
    io.propose_install.assert_not_called()


# ──────────────────────────────────────────────────────────────────────────────
# Unexpected catalog state (activation)
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("status", ["removed", "unknown_state"])
def test_needs_activation_unexpected_state_blocked(status):
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": status}),
        tool_registry_read=FakeToolRegistry({}),
        approval_queue_read=FakeApprovalQueue(pending=[]),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_activation_approval", server_id="alice"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "unexpected_catalog_state" for b in plan.blockers
    )


def test_needs_activation_unknown_catalog_blocked():
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({}),
        tool_registry_read=FakeToolRegistry({}),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_activation_approval", server_id="alice"),
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "unexpected_catalog_state" for b in plan.blockers
    )


def test_needs_activation_unexpected_state_no_propose_activation_called(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    asv = MagicMock(spec=ActivationServiceLike)
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "removed"}),
        tool_registry_read=FakeToolRegistry({}),
        approval_queue_read=FakeApprovalQueue(pending=[]),
        activation_service=asv,
    )
    MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_activation_approval", server_id="alice"),
        caller_kind="test", dry_run=False,
    )
    asv.propose_activation.assert_not_called()


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — execute_after_approval
# ══════════════════════════════════════════════════════════════════════════════


def test_execute_invalid_action_id():
    deps = MCPExecutionBridgeDeps(catalog_read=FakeCatalog({}))
    plan = MCPExecutionBridge(deps).execute_after_approval(
        "not-uuid", _approval_result(), "alice",
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "action_id_invalid_format" for b in plan.blockers
    )


def test_execute_invalid_server_id():
    deps = MCPExecutionBridgeDeps(catalog_read=FakeCatalog({}))
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, _approval_result(), "BAD",
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "server_id_invalid_format" for b in plan.blockers
    )


def test_execute_approval_not_granted():
    deps = MCPExecutionBridgeDeps(catalog_read=FakeCatalog({"alice": "declared"}))
    ar = _approval_result(decision="rejected")
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, ar, "alice", caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(b.blocker_code == "approval_not_granted" for b in plan.blockers)


def test_execute_args_not_dict():
    ar = SimpleNamespace(
        decision=SimpleNamespace(value="approved"),
        args="not_a_dict",
        reason=None,
    )
    deps = MCPExecutionBridgeDeps(catalog_read=FakeCatalog({"alice": "declared"}))
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, ar, "alice", caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "approval_result_invalid_shape"
        for b in plan.blockers
    )


def test_execute_server_id_mismatch():
    ar = _approval_result(server_id="bob")
    deps = MCPExecutionBridgeDeps(catalog_read=FakeCatalog({"alice": "declared"}))
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, ar, "alice", caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "approval_server_id_mismatch" for b in plan.blockers
    )


def test_execute_no_catalog_dep():
    plan = MCPExecutionBridge(_empty_deps()).execute_after_approval(
        _TID_A, _approval_result(install_args=True), "alice",
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(b.blocker_code == "no_catalog_dep" for b in plan.blockers)


def test_execute_quarantined_blocked():
    deps = MCPExecutionBridgeDeps(catalog_read=FakeCatalog({"alice": "quarantined"}))
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, _approval_result(install_args=True), "alice",
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(b.blocker_code == "catalog_quarantined" for b in plan.blockers)


def test_execute_active_already_applied():
    deps = MCPExecutionBridgeDeps(catalog_read=FakeCatalog({"alice": "active"}))
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, _approval_result(install_args=True), "alice",
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.ALREADY_APPLIED


def test_execute_installed_handlers_present_already_applied():
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "installed"}),
        tool_registry_read=FakeToolRegistry({"alice_tool": "alice"}),
    )
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, _approval_result(action="activate"), "alice",
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.ALREADY_APPLIED


def test_execute_unknown_catalog_state_blocked():
    deps = MCPExecutionBridgeDeps(catalog_read=FakeCatalog({}))
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, _approval_result(install_args=True), "alice",
        caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "unexpected_catalog_state" for b in plan.blockers
    )


# Cross-check args spécifiques (v2.1)


def test_execute_activation_action_mismatch_catalog_update():
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "installed"}),
        tool_registry_read=FakeToolRegistry({}),
    )
    ar = _approval_result(action="catalog_update")
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, ar, "alice", caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "approval_action_mismatch" for b in plan.blockers
    )


def test_execute_activation_no_action_key_blocked():
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "installed"}),
        tool_registry_read=FakeToolRegistry({}),
    )
    ar = _approval_result()  # no action key
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, ar, "alice", caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "approval_action_mismatch" for b in plan.blockers
    )


def test_execute_activation_correct_action_ok_dry_run():
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "installed"}),
        tool_registry_read=FakeToolRegistry({}),
    )
    ar = _approval_result(action="activate")
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, ar, "alice", caller_kind="test", dry_run=True,
    )
    assert plan.decision == Phase25BridgeDecision.EXECUTION_WOULD_HAPPEN
    assert plan.evidence.get("which_orchestrator_would_be_called") == "activation"


def test_execute_install_missing_required_keys():
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "declared"}),
    )
    # args avec seulement server_id, missing transport/package_name/etc.
    ar = _approval_result(install_args=False)
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, ar, "alice", caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "approval_result_invalid_shape" for b in plan.blockers
    )


def test_execute_install_keys_present_ok_dry_run():
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "declared"}),
    )
    ar = _approval_result(install_args=True)
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, ar, "alice", caller_kind="test", dry_run=True,
    )
    assert plan.decision == Phase25BridgeDecision.EXECUTION_WOULD_HAPPEN
    assert plan.evidence.get("which_orchestrator_would_be_called") == "install"


# Live execution


def test_execute_install_live_success(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "declared"}),
        install_orchestrator=FakeInstallOrchestrator(execute_success=True),
    )
    ar = _approval_result(install_args=True)
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, ar, "alice", caller_kind="test", dry_run=False,
    )
    assert plan.decision == Phase25BridgeDecision.EXECUTED_SUCCESS_INSTALL
    assert plan.action.invoked_orchestrator == "install"


def test_execute_install_live_failure(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "declared"}),
        install_orchestrator=FakeInstallOrchestrator(execute_success=False),
    )
    ar = _approval_result(install_args=True)
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, ar, "alice", caller_kind="test", dry_run=False,
    )
    assert plan.decision == Phase25BridgeDecision.EXECUTED_FAILURE
    assert any(b.blocker_code == "install_failed" for b in plan.blockers)


def test_execute_install_live_exception(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "declared"}),
        install_orchestrator=FakeInstallOrchestrator(raises_on_execute=True),
    )
    ar = _approval_result(install_args=True)
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, ar, "alice", caller_kind="test", dry_run=False,
    )
    assert plan.decision == Phase25BridgeDecision.EXECUTED_FAILURE


def test_execute_activation_live_success(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "installed"}),
        tool_registry_read=FakeToolRegistry({}),
        activation_service=FakeActivationService(activate_success=True),
    )
    ar = _approval_result(action="activate")
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, ar, "alice", caller_kind="test", dry_run=False,
    )
    assert plan.decision == Phase25BridgeDecision.EXECUTED_SUCCESS_ACTIVATE
    assert plan.action.invoked_orchestrator == "activation"


def test_execute_activation_live_failure(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "installed"}),
        tool_registry_read=FakeToolRegistry({}),
        activation_service=FakeActivationService(raises_on_activate=True),
    )
    ar = _approval_result(action="activate")
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, ar, "alice", caller_kind="test", dry_run=False,
    )
    assert plan.decision == Phase25BridgeDecision.EXECUTED_FAILURE


def test_execute_live_no_live_mode_blocked(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "0")
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "declared"}),
        install_orchestrator=FakeInstallOrchestrator(),
    )
    ar = _approval_result(install_args=True)
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, ar, "alice", caller_kind="test", dry_run=False,
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(b.blocker_code == "live_mode_disabled" for b in plan.blockers)


def test_execute_live_install_orchestrator_dep_none(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "declared"}),
    )
    ar = _approval_result(install_args=True)
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, ar, "alice", caller_kind="test", dry_run=False,
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "no_install_orchestrator_dep" for b in plan.blockers
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — describe_action_state
# ══════════════════════════════════════════════════════════════════════════════


def test_describe_invalid_action_id():
    plan = MCPExecutionBridge(_empty_deps()).describe_action_state(
        "not-uuid", caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "action_id_invalid_format" for b in plan.blockers
    )


def test_describe_no_approval_queue_dep():
    plan = MCPExecutionBridge(_empty_deps()).describe_action_state(
        _TID_A, caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "no_approval_queue_dep" for b in plan.blockers
    )


def test_describe_pending_yields_waiting():
    pending = [{"id": _TID_A, "tool_name": "mcp_install:alice"}]
    deps = MCPExecutionBridgeDeps(
        approval_queue_read=FakeApprovalQueue(pending=pending),
    )
    plan = MCPExecutionBridge(deps).describe_action_state(
        _TID_A, caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.WAITING_APPROVAL


def test_describe_absent_yields_blocked():
    deps = MCPExecutionBridgeDeps(
        approval_queue_read=FakeApprovalQueue(pending=[]),
    )
    plan = MCPExecutionBridge(deps).describe_action_state(
        _TID_A, caller_kind="test",
    )
    assert plan.decision == Phase25BridgeDecision.BLOCKED
    assert any(
        b.blocker_code == "marker_not_found_or_already_decided"
        for b in plan.blockers
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — Anti-leak
# ══════════════════════════════════════════════════════════════════════════════


def _serialize(plan: Phase25BridgePlan) -> str:
    return json.dumps({
        "bridge_plan_id": plan.bridge_plan_id,
        "decision": plan.decision.value,
        "action": plan.action.__dict__,
        "blockers": [b.__dict__ for b in plan.blockers],
        "evidence": plan.evidence,
    }, ensure_ascii=False, default=str)


def test_approval_args_package_spec_not_leaked(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "0")
    deps = MCPExecutionBridgeDeps(catalog_read=FakeCatalog({"alice": "declared"}))
    ar = _approval_result(install_args=True, extra_args={
        "package_spec": "SECRET_APPROVAL_ARGS_PACKAGE_SPEC_LEAK",
    })
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, ar, "alice", caller_kind="test", dry_run=True,
    )
    blob = _serialize(plan)
    assert "SECRET_APPROVAL_ARGS_PACKAGE_SPEC_LEAK" not in blob


def test_approval_args_version_not_leaked(monkeypatch):
    deps = MCPExecutionBridgeDeps(catalog_read=FakeCatalog({"alice": "declared"}))
    ar = _approval_result(install_args=True, extra_args={
        "version": "SECRET_APPROVAL_ARGS_VERSION_LEAK",
    })
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, ar, "alice", caller_kind="test", dry_run=True,
    )
    blob = _serialize(plan)
    assert "SECRET_APPROVAL_ARGS_VERSION_LEAK" not in blob


def test_install_result_reason_not_leaked(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")

    class LeakyOrchestrator:
        def propose_install(self, server_id, *, caller_kind="silent"):
            return SimpleNamespace(
                server_id=server_id, approval_ticket_id=_TID_A,
            )

        def execute_approved_install(self, server_id, approval_result):
            return SimpleNamespace(
                server_id=server_id, success=False,
                reason="SECRET_INSTALL_RESULT_REASON_LEAK",
            )

    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "declared"}),
        install_orchestrator=LeakyOrchestrator(),
    )
    ar = _approval_result(install_args=True)
    plan = MCPExecutionBridge(deps).execute_after_approval(
        _TID_A, ar, "alice", caller_kind="test", dry_run=False,
    )
    blob = _serialize(plan)
    assert "SECRET_INSTALL_RESULT_REASON_LEAK" not in blob


def test_install_proposal_package_spec_not_leaked(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")

    class LeakyOrchestrator:
        def propose_install(self, server_id, *, caller_kind="silent"):
            return SimpleNamespace(
                server_id=server_id,
                approval_ticket_id=_TID_A,
                package_spec="SECRET_INSTALL_PROPOSAL_PACKAGE_SPEC_LEAK",
            )

        def execute_approved_install(self, server_id, approval_result):
            return SimpleNamespace(server_id=server_id, success=True)

    deps = MCPExecutionBridgeDeps(
        catalog_read=FakeCatalog({"alice": "declared"}),
        approval_queue_read=FakeApprovalQueue(pending=[]),
        install_orchestrator=LeakyOrchestrator(),
    )
    plan = MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_install_approval", server_id="alice"),
        caller_kind="test", dry_run=False,
    )
    blob = _serialize(plan)
    assert "SECRET_INSTALL_PROPOSAL_PACKAGE_SPEC_LEAK" not in blob


def test_evidence_only_whitelist():
    plan = MCPExecutionBridge(_empty_deps()).request_action_for_plan(
        _phase24_plan(decision="ready_to_use_existing_capability"),
        caller_kind="test",
    )
    for k in plan.evidence.keys():
        assert k in _EVIDENCE_WHITELIST, f"key {k} hors whitelist"


def test_blocker_codes_only_whitelist():
    plan = MCPExecutionBridge(_empty_deps()).request_action_for_plan(
        _phase24_plan(decision="blocked"),
        caller_kind="test",
    )
    for b in plan.blockers:
        assert b.blocker_code in _BLOCKER_CODES


def test_risk_summary_only_whitelist():
    plan = MCPExecutionBridge(_empty_deps()).request_action_for_plan(
        _phase24_plan(decision="needs_local_creation"),
        caller_kind="test",
    )
    assert plan.action.risk_summary in _RISK_SUMMARY_WHITELIST


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — Anti-mutation grep statique
# ══════════════════════════════════════════════════════════════════════════════


_MODULE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src" / "mcp" / "execution_bridge.py"
)


_FORBIDDEN_MUTATION_TOKENS = (
    ".approve(", ".reject(",
    ".add_pending(", ".delete_pending(",
    ".deactivate(",
    ".add_server(", ".quarantine(", ".restore(", ".remove_server(",
    ".update_trust_score(",
    ".add_pattern(", ".remove_pattern(",
    ".register_dynamic_handler(",
    ".update_last_active(",
    "call_tool(",
    "start_runner(", "stop_runner(",
    "subprocess.", "Popen(",
    "os.system(", "os.exec",
)


@pytest.mark.parametrize("token", _FORBIDDEN_MUTATION_TOKENS)
def test_no_mutation_token(token):
    text = _MODULE_PATH.read_text(encoding="utf-8")
    assert token not in text, f"{token} interdit en Phase 25"


def test_no_direct_propose_approval_queue():
    """Pas de .propose( direct sur ApprovalQueue.

    Seul .propose_install( et .propose_activation( autorisés.
    """
    text = _MODULE_PATH.read_text(encoding="utf-8")
    assert ".propose(" not in text


def test_authorized_invocations_present():
    """Tokens autorisés présents au moins une fois (passés comme références
    à _safe_call : pas de parenthèse d'appel direct dans le source, mais
    le symbole doit apparaître).
    """
    text = _MODULE_PATH.read_text(encoding="utf-8")
    assert ".propose_catalog_add" in text
    assert ".execute_approved_catalog_add" in text
    assert ".propose_install" in text
    assert ".propose_activation" in text
    assert ".execute_approved_install" in text
    assert ".activate" in text


_FORBIDDEN_IMPORTS = (
    "from src.mcp.install_orchestrator",
    "from src.mcp.activation_service",
    "from src.mcp.approval_queue",
    "from src.mcp.autonomous_orchestrator",
    "from src.mcp.capability_resolver",
    "from src.mcp.proposal_planner",
    "from src.mcp.auto_approve",
    "from src.mcp.client_factory",
    "from src.mcp.sandbox_runner",
    "from src.mcp.policy",
    "import src.mcp.install_orchestrator",
    "import src.mcp.activation_service",
    "import src.mcp.approval_queue",
    "import src.mcp.autonomous_orchestrator",
    "import requests",
    "import httpx",
    "import urllib3",
    "import aiohttp",
    "from urllib.request",
)


@pytest.mark.parametrize("imp", _FORBIDDEN_IMPORTS)
def test_no_forbidden_imports(imp):
    text = _MODULE_PATH.read_text(encoding="utf-8")
    assert imp not in text, f"import interdit : {imp}"


def test_no_policy_name_reference():
    text = _MODULE_PATH.read_text(encoding="utf-8")
    for tok in (
        "EXTERNAL_WRITE_RECOVERABLE", "EXTERNAL_WRITE_IRREVERSIBLE",
        "LOCAL_WRITE", "SECRETS_AUTH", "READ_ONLY", "EXTERNAL_READ",
        "MCPPolicy.",
    ):
        assert tok not in text, f"{tok} ne doit pas apparaître dans Phase 25"


def test_no_singleton_cache():
    text = _MODULE_PATH.read_text(encoding="utf-8")
    for tok in (
        "_INSTANCE", "_SINGLETON", "lru_cache",
        "functools.cache", "@cache", "global _",
    ):
        assert tok not in text


def test_no_http_route():
    text = _MODULE_PATH.read_text(encoding="utf-8")
    for tok in (
        "@router", "APIRouter", "FastAPI", "@app.",
        "fastapi", "from fastapi",
    ):
        assert tok not in text


def test_no_crypto_direct():
    text = _MODULE_PATH.read_text(encoding="utf-8")
    for tok in (
        "Fernet(", ".decrypt(", "SecretsService", "secrets_service",
    ):
        assert tok not in text


def test_spec_mocks_no_mutation_called(monkeypatch):
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    io = MagicMock(spec=InstallOrchestratorLike)
    io.propose_install.return_value = SimpleNamespace(
        server_id="alice", approval_ticket_id=_TID_A,
    )
    io.execute_approved_install.return_value = SimpleNamespace(
        server_id="alice", success=True,
    )
    cao = MagicMock(spec=CatalogAddOrchestratorLike)
    cao.propose_catalog_add.return_value = SimpleNamespace(
        server_id="alice", approval_ticket_id=_TID_C,
    )
    cao.execute_approved_catalog_add.return_value = SimpleNamespace(
        server_id="alice", success=True,
    )
    asv = MagicMock(spec=ActivationServiceLike)
    asv.propose_activation.return_value = SimpleNamespace(
        server_id="alice", approval_ticket_id=_TID_B,
    )
    asv.activate.return_value = SimpleNamespace(
        server_id="alice", success=True,
    )
    aq = MagicMock(spec=ApprovalQueueReadLike)
    aq.list_pending.return_value = []
    aq.get.return_value = None
    cat = MagicMock(spec=CatalogReadLike)
    cat.get_server.return_value = SimpleNamespace(
        status=SimpleNamespace(value="declared"),
    )
    cat.list_servers.return_value = []
    tr = MagicMock(spec=ToolRegistryReadLike)
    tr.list_dynamic_handlers.return_value = []
    deps = MCPExecutionBridgeDeps(
        install_orchestrator=io, activation_service=asv,
        catalog_add_orchestrator=cao,
        approval_queue_read=aq, catalog_read=cat, tool_registry_read=tr,
    )
    MCPExecutionBridge(deps).request_action_for_plan(
        _phase24_plan(decision="needs_install_approval", server_id="alice"),
        caller_kind="test", dry_run=False,
    )
    forbidden_subs = (
        "approve", "reject", "add_pending", "delete_pending",
        "deactivate", "add_server", "quarantine", "restore",
        "remove_server", "update_trust", "add_pattern", "remove_pattern",
        "register_dynamic_handler", "call_tool",
    )
    all_calls = []
    for m in (io, asv, cao, aq, cat, tr):
        for c in m.mock_calls:
            all_calls.append(str(c))
    for c in all_calls:
        for fs in forbidden_subs:
            assert fs not in c, f"mutation suspecte : {c}"


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — UTF-8 anti-mojibake + Audit local
# ══════════════════════════════════════════════════════════════════════════════


def test_audit_not_written_when_none(tmp_path):
    MCPExecutionBridge(_empty_deps(), audit_log_path=None).request_action_for_plan(
        _phase24_plan(decision="ready_to_use_existing_capability"),
        caller_kind="test",
    )
    assert list(tmp_path.iterdir()) == []


def test_audit_written_when_path(tmp_path):
    audit = tmp_path / "audit.jsonl"
    MCPExecutionBridge(_empty_deps(), audit_log_path=audit).request_action_for_plan(
        _phase24_plan(decision="ready_to_use_existing_capability"),
        caller_kind="test",
    )
    raw = audit.read_text(encoding="utf-8").strip()
    event = json.loads(raw.splitlines()[0])
    assert event["phase"] == "25"


def test_audit_no_mojibake(tmp_path):
    audit = tmp_path / "audit.jsonl"
    MCPExecutionBridge(_empty_deps(), audit_log_path=audit).request_action_for_plan(
        _phase24_plan(decision="ready_to_use_existing_capability"),
        caller_kind="test",
    )
    raw = audit.read_text(encoding="utf-8")
    for moji in ("Ã©", "Ã¨", "Ã ", "Ã§", "â€™"):
        assert moji not in raw


def test_audit_parent_dir_created(tmp_path):
    audit = tmp_path / "sub" / "nested" / "audit.jsonl"
    MCPExecutionBridge(_empty_deps(), audit_log_path=audit).request_action_for_plan(
        _phase24_plan(decision="ready_to_use_existing_capability"),
        caller_kind="test",
    )
    assert audit.exists()


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — caller_kind whitelist
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("ck,expected", [
    ("react", "react"),
    ("sub_agent", "sub_agent"),
    ("admin_ui", "admin_ui"),
    ("test", "test"),
    ("autonomous_loop", "autonomous_loop"),
    ("bogus", "unknown"),
    ("", "unknown"),
    (None, "unknown"),
])
def test_caller_kind_whitelist(ck, expected):
    plan = MCPExecutionBridge(_empty_deps()).request_action_for_plan(
        _phase24_plan(decision="ready_to_use_existing_capability"),
        caller_kind=ck,
    )
    assert plan.evidence.get("caller_kind") == expected
