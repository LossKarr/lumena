"""
Tests Phase 24 — AutonomousMCPLoopPlanner.

Couvre :
  - Structure (8 décisions, 6 action kinds, dataclasses frozen,
    plan_id UUID4)
  - Mapping Phase 22 → Phase 24 (8 mappings)
  - Mapping Phase 22 SEARCH → Phase 23 → Phase 24 (5 mappings)
  - Anti-loop infinite (recall Phase 22 max 1 niveau)
  - describe_pending_resume (read-only Phase 10, marker validation,
    ambigu honnête)
  - Safe extraction selected_candidate
  - target_tool_name sanitization (regex + longueur)
  - proposed_risk_summary whitelist
  - Anti-leak markers SECRET_*
  - Anti-mutation grep statique
  - Anti-imports interdits
  - Anti-singleton/cache/HTTP/crypto
  - Sources optionnelles
  - UTF-8 anti-mojibake
  - Audit local optionnel
  - Cohérence Phase 22/23
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from src.mcp.autonomous_orchestrator import (
    ApprovalQueueReadLike,
    AutonomousAction,
    AutonomousActionKind,
    AutonomousMCPLoopDecision,
    AutonomousMCPLoopDeps,
    AutonomousMCPLoopPlan,
    AutonomousMCPLoopPlanBlocker,
    AutonomousMCPLoopPlanner,
    CapabilityResolverLike,
    ProposalPlannerLike,
    _BLOCKER_CODES,
    _EVIDENCE_WHITELIST,
    _PROPOSED_ACTION_KIND_WHITELIST,
    _PROPOSED_RISK_SUMMARY_WHITELIST,
    _validate_target_tool_name,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers fakes (Phase 22/23 plans)
# ──────────────────────────────────────────────────────────────────────────────


def _p22_plan(
    *,
    decision: str,
    selected_kind: Optional[str] = None,
    selected_tool: Optional[str] = None,
    selected_sid: Optional[str] = None,
    selected_score: Optional[float] = None,
    actionable: bool = False,
    intent_id: str = "abcdef01234567890abcdef012345678",
):
    selected = None
    if selected_kind is not None:
        selected = SimpleNamespace(
            kind=selected_kind,
            tool_name=selected_tool,
            server_id=selected_sid,
            match_score=selected_score,
        )
    return SimpleNamespace(
        decision=SimpleNamespace(value=decision),
        selected_candidate=selected,
        evidence={
            "intent_id": intent_id,
            "actionable_intent": actionable,
        },
    )


def _p23_plan(
    *,
    decision: str,
    cat_sid: Optional[str] = None,
    requires_approval: bool = False,
    display_name: str = "Proposed MCP",
    package_spec: str = "npm:proposed-mcp",
    version: Optional[str] = "1.0.0",
    trust_score: Optional[int] = 80,
    proposal_id: str = "1234567890abcdef1234567890abcdef",
):
    cat = None
    if cat_sid is not None:
        cat = SimpleNamespace(
            proposed_server_id=cat_sid,
            proposed_display_name=display_name,
            proposed_package_spec=package_spec,
            proposed_version=version,
            proposed_trust_score_set=trust_score,
            requires_approval=requires_approval,
        )
    return SimpleNamespace(
        decision=SimpleNamespace(value=decision),
        catalog_proposal=cat,
        evidence={"proposal_id": proposal_id},
    )


class FakeCapabilityResolver:
    def __init__(self, plans: List[Any]) -> None:
        self._plans = plans
        self._i = 0

    def resolve(self, intent, *, caller_kind, profile=None):
        p = self._plans[self._i]
        self._i = min(self._i + 1, len(self._plans) - 1)
        return p


class FakeProposalPlanner:
    def __init__(self, plan: Any) -> None:
        self._plan = plan

    def plan_proposal(self, intent, *, caller_kind, profile=None,
                      phase22_plan=None):
        return self._plan


class FakeApprovalQueue:
    def __init__(self, pending_ids: Optional[List[str]] = None) -> None:
        self._pending = [
            SimpleNamespace(id=tid) for tid in (pending_ids or [])
        ]

    def list_pending(self):
        return list(self._pending)

    def get(self, action_id):
        for t in self._pending:
            if t.id == action_id:
                return t
        return None


def _empty_deps() -> AutonomousMCPLoopDeps:
    return AutonomousMCPLoopDeps()


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Structure
# ══════════════════════════════════════════════════════════════════════════════


def test_decision_has_exactly_eight_values():
    expected = {
        "READY_TO_USE_EXISTING_CAPABILITY",
        "NEEDS_INSTALL_APPROVAL",
        "NEEDS_ACTIVATION_APPROVAL",
        "NEEDS_CATALOG_APPROVAL",
        "NEEDS_LOCAL_CREATION",
        "WAITING_APPROVAL",
        "BLOCKED",
        "NO_SAFE_PATH",
    }
    assert {d.name for d in AutonomousMCPLoopDecision} == expected


def test_action_kind_has_exactly_six_values():
    expected = {
        "USE_TOOL", "INSTALL", "ACTIVATE",
        "CATALOG_ADD_DECLARED", "LOCAL_CREATE", "NONE",
    }
    assert {k.name for k in AutonomousActionKind} == expected


def test_action_frozen():
    a = AutonomousAction(
        kind=AutonomousActionKind.NONE,
        target_server_id=None, target_tool_name=None, match_score=None,
        proposed_action_kind="", proposed_target_server_id=None,
        proposed_risk_summary="none", requires_admin_nod=False,
    )
    with pytest.raises(Exception):
        a.kind = AutonomousActionKind.USE_TOOL  # type: ignore[misc]


def test_planner_rejects_non_deps():
    with pytest.raises(TypeError):
        AutonomousMCPLoopPlanner(deps="x")  # type: ignore[arg-type]


def test_planner_rejects_non_path_audit():
    with pytest.raises(TypeError):
        AutonomousMCPLoopPlanner(_empty_deps(), audit_log_path="/x")  # type: ignore[arg-type]


def test_plan_id_uuid4_hex32():
    plan = AutonomousMCPLoopPlanner(_empty_deps()).plan_for_intent(
        "hello", caller_kind="test",
    )
    assert re.match(r"^[0-9a-f]{32}$", plan.plan_id)


def test_intent_truncated_256():
    huge = "send email " + ("x" * 500)
    plan = AutonomousMCPLoopPlanner(_empty_deps()).plan_for_intent(
        huge, caller_kind="test",
    )
    assert len(plan.intent_query_sanitized) <= 256


def test_intent_preserves_accents():
    cr = FakeCapabilityResolver([
        _p22_plan(decision="no_capability_found"),
    ])
    deps = AutonomousMCPLoopDeps(capability_resolver=cr)
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "envoyer un email à éàçôê", caller_kind="test",
    )
    assert "éàçôê" in plan.intent_query_sanitized


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Mapping Phase 22 → Phase 24
# ══════════════════════════════════════════════════════════════════════════════


def test_use_native_tool_maps_to_ready():
    cr = FakeCapabilityResolver([_p22_plan(
        decision="use_native_tool",
        selected_kind="native", selected_tool="read_file",
        selected_sid=None, selected_score=0.8,
    )])
    deps = AutonomousMCPLoopDeps(capability_resolver=cr)
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "read file", caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.READY_TO_USE_EXISTING_CAPABILITY
    assert plan.action.kind == AutonomousActionKind.USE_TOOL
    assert plan.action.target_tool_name == "read_file"
    assert plan.action.match_score == 0.8


def test_use_active_mcp_tool_maps_to_ready():
    cr = FakeCapabilityResolver([_p22_plan(
        decision="use_active_mcp_tool",
        selected_kind="mcp_active",
        selected_tool="mcp_search",
        selected_sid="brave_srv",
        selected_score=0.6,
    )])
    deps = AutonomousMCPLoopDeps(capability_resolver=cr)
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "search", caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.READY_TO_USE_EXISTING_CAPABILITY
    assert plan.action.target_server_id == "brave_srv"


def test_activate_installed_mcp_maps():
    cr = FakeCapabilityResolver([_p22_plan(
        decision="activate_installed_mcp",
        selected_kind="mcp_installed",
        selected_tool="github_create_issue",
        selected_sid="gh_srv",
        selected_score=0.7,
    )])
    deps = AutonomousMCPLoopDeps(capability_resolver=cr)
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "github issue", caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.NEEDS_ACTIVATION_APPROVAL
    assert plan.action.kind == AutonomousActionKind.ACTIVATE
    assert plan.action.proposed_action_kind == "activation"
    assert plan.action.proposed_risk_summary == "activation_required"
    assert plan.action.proposed_target_server_id == "gh_srv"


def test_install_declared_mcp_maps():
    cr = FakeCapabilityResolver([_p22_plan(
        decision="install_declared_mcp",
        selected_kind="mcp_declared",
        selected_tool=None,
        selected_sid="notion_srv",
        selected_score=0.55,
    )])
    deps = AutonomousMCPLoopDeps(capability_resolver=cr)
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "notion page", caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.NEEDS_INSTALL_APPROVAL
    assert plan.action.kind == AutonomousActionKind.INSTALL
    assert plan.action.proposed_action_kind == "install"
    assert plan.action.proposed_risk_summary == "install_required"


@pytest.mark.parametrize("blocked_decision", [
    "blocked_policy", "blocked_trust", "blocked_runtime",
])
def test_blocked_phase22_maps_to_blocked(blocked_decision):
    cr = FakeCapabilityResolver([_p22_plan(decision=blocked_decision)])
    deps = AutonomousMCPLoopDeps(capability_resolver=cr)
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "x", caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.BLOCKED
    assert any(
        b.blocker_code == "phase22_blocked" for b in plan.blockers
    )


def test_phase22_needs_approval_maps_to_waiting():
    cr = FakeCapabilityResolver([_p22_plan(decision="needs_approval")])
    deps = AutonomousMCPLoopDeps(capability_resolver=cr)
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "x", caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.WAITING_APPROVAL
    assert any(
        b.blocker_code == "phase22_needs_approval" for b in plan.blockers
    )


def test_no_capability_not_actionable_no_safe_path():
    cr = FakeCapabilityResolver([_p22_plan(
        decision="no_capability_found", actionable=False,
    )])
    deps = AutonomousMCPLoopDeps(capability_resolver=cr)
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "x", caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.NO_SAFE_PATH


def test_no_capability_actionable_no_planner_no_safe_path():
    cr = FakeCapabilityResolver([_p22_plan(
        decision="no_capability_found", actionable=True,
    )])
    deps = AutonomousMCPLoopDeps(capability_resolver=cr)
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "x", caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.NO_SAFE_PATH


def test_no_capability_actionable_planner_triggers_phase23():
    cr = FakeCapabilityResolver([_p22_plan(
        decision="no_capability_found", actionable=True,
    )])
    pp = FakeProposalPlanner(_p23_plan(decision="no_safe_candidate"))
    deps = AutonomousMCPLoopDeps(
        capability_resolver=cr, proposal_planner=pp,
    )
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "x", caller_kind="test",
    )
    # NO_SAFE_CANDIDATE Phase 23 → NO_SAFE_PATH
    assert plan.decision == AutonomousMCPLoopDecision.NO_SAFE_PATH


def test_resolver_dep_none_yields_no_safe_path():
    plan = AutonomousMCPLoopPlanner(_empty_deps()).plan_for_intent(
        "x", caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.NO_SAFE_PATH
    assert any(
        b.blocker_code == "no_phase22_resolver" for b in plan.blockers
    )


def test_resolver_raises_yields_blocker():
    class Raising:
        def resolve(self, *a, **k):
            raise RuntimeError("boom")
    deps = AutonomousMCPLoopDeps(capability_resolver=Raising())
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "x", caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.NO_SAFE_PATH
    assert "capability_resolver" in plan.evidence["sources_degraded"]


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Mapping SEARCH_MCP → Phase 23
# ══════════════════════════════════════════════════════════════════════════════


def test_search_mcp_propose_catalog_declared():
    cr = FakeCapabilityResolver([_p22_plan(
        decision="search_mcp", actionable=True,
    )])
    pp = FakeProposalPlanner(_p23_plan(
        decision="propose_catalog_declared",
        cat_sid="proposed_abc12345",
        requires_approval=False,
    ))
    deps = AutonomousMCPLoopDeps(
        capability_resolver=cr, proposal_planner=pp,
    )
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "search", caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.NEEDS_CATALOG_APPROVAL
    assert plan.action.kind == AutonomousActionKind.CATALOG_ADD_DECLARED
    assert plan.action.proposed_action_kind == "catalog_add_declared"
    assert plan.action.proposed_risk_summary == "catalog_add_required"
    assert plan.action.requires_admin_nod is False
    assert plan.action.proposed_target_server_id == "proposed_abc12345"
    assert plan.action.catalog_display_name == "Proposed MCP"
    assert plan.action.catalog_package_spec == "npm:proposed-mcp"
    assert plan.action.catalog_version == "1.0.0"
    assert plan.action.catalog_trust_score == 80
    assert plan.evidence["catalog_package_spec_present"] is True
    assert plan.evidence["catalog_trust_score_set"] is True


def test_search_mcp_propose_local_create():
    cr = FakeCapabilityResolver([_p22_plan(
        decision="search_mcp", actionable=True,
    )])
    pp = FakeProposalPlanner(_p23_plan(decision="propose_local_create"))
    deps = AutonomousMCPLoopDeps(
        capability_resolver=cr, proposal_planner=pp,
    )
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "send email", caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.NEEDS_LOCAL_CREATION
    assert plan.action.kind == AutonomousActionKind.LOCAL_CREATE
    assert plan.action.proposed_action_kind == "local_create"
    assert plan.action.proposed_risk_summary == "local_creation_required"


def test_search_mcp_no_safe_candidate():
    cr = FakeCapabilityResolver([_p22_plan(
        decision="search_mcp", actionable=True,
    )])
    pp = FakeProposalPlanner(_p23_plan(decision="no_safe_candidate"))
    deps = AutonomousMCPLoopDeps(
        capability_resolver=cr, proposal_planner=pp,
    )
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "x", caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.NO_SAFE_PATH


def test_search_mcp_phase23_needs_approval_network():
    cr = FakeCapabilityResolver([_p22_plan(
        decision="search_mcp", actionable=True,
    )])
    pp = FakeProposalPlanner(_p23_plan(
        decision="needs_approval",
        cat_sid="proposed_xyz12345",
        requires_approval=True,
    ))
    deps = AutonomousMCPLoopDeps(
        capability_resolver=cr, proposal_planner=pp,
    )
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "x", caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.NEEDS_CATALOG_APPROVAL
    assert plan.action.requires_admin_nod is True
    assert plan.action.catalog_display_name == "Proposed MCP"
    assert plan.action.catalog_package_spec == "npm:proposed-mcp"


def test_search_mcp_no_planner_blocker():
    cr = FakeCapabilityResolver([_p22_plan(
        decision="search_mcp", actionable=True,
    )])
    deps = AutonomousMCPLoopDeps(capability_resolver=cr)
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "x", caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.NO_SAFE_PATH
    assert any(
        b.blocker_code == "no_phase23_planner" for b in plan.blockers
    )


def test_search_mcp_use_existing_recall_to_phase22():
    # Phase 22 (1) → SEARCH_MCP ; Phase 23 → USE_EXISTING_CANDIDATE ;
    # Recall Phase 22 (2) → USE_ACTIVE_MCP_TOOL
    cr = FakeCapabilityResolver([
        _p22_plan(decision="search_mcp", actionable=True),
        _p22_plan(
            decision="use_active_mcp_tool",
            selected_kind="mcp_active",
            selected_tool="mcp_search",
            selected_sid="srv1", selected_score=0.7,
        ),
    ])
    pp = FakeProposalPlanner(_p23_plan(decision="use_existing_candidate"))
    deps = AutonomousMCPLoopDeps(
        capability_resolver=cr, proposal_planner=pp,
    )
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "x", caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.READY_TO_USE_EXISTING_CAPABILITY


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Anti-loop infinite
# ══════════════════════════════════════════════════════════════════════════════


def test_infinite_loop_prevention():
    # Phase 22 (1) → SEARCH ; Phase 23 → USE_EXISTING ; Phase 22 (2) → SEARCH
    cr = FakeCapabilityResolver([
        _p22_plan(decision="search_mcp", actionable=True),
        _p22_plan(decision="search_mcp", actionable=True),
    ])
    pp = FakeProposalPlanner(_p23_plan(decision="use_existing_candidate"))
    deps = AutonomousMCPLoopDeps(
        capability_resolver=cr, proposal_planner=pp,
    )
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "x", caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.NO_SAFE_PATH
    assert any(
        b.blocker_code == "infinite_loop_prevention" for b in plan.blockers
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — describe_pending_resume
# ══════════════════════════════════════════════════════════════════════════════


def test_resume_marker_invalid_format():
    plan = AutonomousMCPLoopPlanner(_empty_deps()).describe_pending_resume(
        "not-a-uuid", caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.BLOCKED
    assert any(
        b.blocker_code == "marker_invalid_format" for b in plan.blockers
    )


def test_resume_no_approval_queue_read():
    valid = "abcdef0123456789abcdef0123456789"
    plan = AutonomousMCPLoopPlanner(_empty_deps()).describe_pending_resume(
        valid, caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.BLOCKED
    assert any(
        b.blocker_code == "no_approval_queue_read" for b in plan.blockers
    )


def test_resume_marker_present_in_pending_yields_waiting():
    valid = "abcdef0123456789abcdef0123456789"
    aq = FakeApprovalQueue(pending_ids=[valid])
    deps = AutonomousMCPLoopDeps(approval_queue_read=aq)
    plan = AutonomousMCPLoopPlanner(deps).describe_pending_resume(
        valid, caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.WAITING_APPROVAL
    assert plan.evidence.get("ticket_state") == "pending"
    assert plan.evidence.get("resume_marker_inspected") == valid


def test_resume_marker_absent_yields_blocked_not_found_or_decided():
    valid = "abcdef0123456789abcdef0123456789"
    aq = FakeApprovalQueue(pending_ids=[])
    deps = AutonomousMCPLoopDeps(approval_queue_read=aq)
    plan = AutonomousMCPLoopPlanner(deps).describe_pending_resume(
        valid, caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.BLOCKED
    assert any(
        b.blocker_code == "marker_not_found_or_already_decided"
        for b in plan.blockers
    )


def test_resume_does_not_call_mutation_methods():
    valid = "abcdef0123456789abcdef0123456789"
    aq = MagicMock(spec=ApprovalQueueReadLike)
    aq.list_pending.return_value = []
    aq.get.return_value = None
    deps = AutonomousMCPLoopDeps(approval_queue_read=aq)
    AutonomousMCPLoopPlanner(deps).describe_pending_resume(
        valid, caller_kind="test",
    )
    # Aucune méthode hors list_pending/get appelée
    all_calls = [str(c) for c in aq.mock_calls]
    for forbidden in ("approve", "reject", "propose", "add_pending"):
        for c in all_calls:
            assert forbidden not in c


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — Safe extraction selected_candidate
# ══════════════════════════════════════════════════════════════════════════════


def test_selected_candidate_extracts_only_safe_fields():
    # selected_candidate avec un attribut "trust_score_factors" interdit
    selected = SimpleNamespace(
        kind="mcp_active",
        tool_name="mcp_search",
        server_id="srv1",
        match_score=0.7,
        trust_score_factors="SECRET_TRUST_FACTORS_LEAK",
        policy_state="allowed",
    )
    p22 = SimpleNamespace(
        decision=SimpleNamespace(value="use_active_mcp_tool"),
        selected_candidate=selected,
        evidence={"intent_id": "a" * 32, "actionable_intent": False},
    )
    cr = FakeCapabilityResolver([p22])
    deps = AutonomousMCPLoopDeps(capability_resolver=cr)
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "x", caller_kind="test",
    )
    blob = _serialize_plan(plan)
    assert "SECRET_TRUST_FACTORS_LEAK" not in blob


def test_match_score_out_of_bounds_dropped():
    p22 = _p22_plan(
        decision="use_native_tool", selected_kind="native",
        selected_tool="read_file", selected_score=1.5,
    )
    cr = FakeCapabilityResolver([p22])
    plan = AutonomousMCPLoopPlanner(
        AutonomousMCPLoopDeps(capability_resolver=cr)
    ).plan_for_intent("x", caller_kind="test")
    assert plan.action.match_score is None


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — target_tool_name sanitization
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("raw,expected", [
    ("read_file", "read_file"),
    ("mcp_search_brave", "mcp_search_brave"),
    ("tool-name", "tool-name"),
    ("Tool.Name", "Tool.Name"),
    ("a" * 128, "a" * 128),
    # Refusés
    ("", None),
    ("a" * 129, None),
    ("tool name with spaces", None),
    ("tool/with/slash", None),
    ("tool\x00null", None),
    (None, None),
    (123, None),
    ("0starts_with_digit", None),
])
def test_validate_target_tool_name(raw, expected):
    assert _validate_target_tool_name(raw) == expected


def test_invalid_tool_name_yields_safe_field_invalid_blocker():
    selected = SimpleNamespace(
        kind="mcp_active",
        tool_name="tool name with spaces",  # invalide
        server_id="srv1",
        match_score=0.7,
    )
    p22 = SimpleNamespace(
        decision=SimpleNamespace(value="use_active_mcp_tool"),
        selected_candidate=selected,
        evidence={"intent_id": "a" * 32, "actionable_intent": False},
    )
    cr = FakeCapabilityResolver([p22])
    deps = AutonomousMCPLoopDeps(capability_resolver=cr)
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "x", caller_kind="test",
    )
    assert plan.action.target_tool_name is None
    assert any(
        b.blocker_code == "safe_field_invalid" for b in plan.blockers
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — proposed_risk_summary whitelist
# ══════════════════════════════════════════════════════════════════════════════


def test_proposed_risk_summary_whitelist_exhaustive():
    assert _PROPOSED_RISK_SUMMARY_WHITELIST == frozenset({
        "install_required",
        "activation_required",
        "catalog_add_required",
        "local_creation_required",
        "none",
    })


@pytest.mark.parametrize("p22_decision,expected", [
    ("use_native_tool", "none"),
    ("use_active_mcp_tool", "none"),
    ("activate_installed_mcp", "activation_required"),
    ("install_declared_mcp", "install_required"),
    ("blocked_policy", "none"),
    ("needs_approval", "none"),
    ("no_capability_found", "none"),
])
def test_proposed_risk_summary_is_whitelisted(p22_decision, expected):
    cr = FakeCapabilityResolver([_p22_plan(
        decision=p22_decision,
        selected_kind="mcp_installed" if p22_decision == "activate_installed_mcp" else (
            "mcp_declared" if p22_decision == "install_declared_mcp" else "native"
        ),
        selected_tool="some_tool",
        selected_sid="srv1",
        selected_score=0.5,
    )])
    deps = AutonomousMCPLoopDeps(capability_resolver=cr)
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "x", caller_kind="test",
    )
    assert plan.action.proposed_risk_summary in _PROPOSED_RISK_SUMMARY_WHITELIST
    assert plan.action.proposed_risk_summary == expected


def test_proposed_action_kind_whitelist():
    assert _PROPOSED_ACTION_KIND_WHITELIST == frozenset({
        "install", "activation", "catalog_add_declared", "local_create", "",
    })


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — Anti-leak markers
# ══════════════════════════════════════════════════════════════════════════════


_SECRET_MARKERS = (
    "SECRET_INTENT_LEAK",
    "SECRET_TASK_CONTEXT_LEAK",
    "SECRET_PHASE22_CANDIDATE_RAW_LEAK",
    "SECRET_PHASE23_RESULT_RAW_LEAK",
    "SECRET_TICKET_ARGS_LEAK",
    "SECRET_PACKAGE_SPEC_LEAK",
    "SECRET_TRUST_FACTORS_LEAK",
    "SECRET_NOTES_LEAK",
    "SECRET_JUSTIFICATION_LEAK_éàç",
)


def _serialize_plan(plan: AutonomousMCPLoopPlan) -> str:
    return json.dumps({
        "plan_id": plan.plan_id,
        "intent": plan.intent_query_sanitized,
        "decision": plan.decision.value,
        "action": plan.action.__dict__,
        "p22": plan.capability_plan_snapshot,
        "p23": plan.proposal_plan_snapshot,
        "blockers": [b.__dict__ for b in plan.blockers],
        "evidence": plan.evidence,
    }, ensure_ascii=False, default=str)


def test_intent_marker_not_leaked():
    huge = "SECRET_INTENT_LEAK_" + "x" * 500
    plan = AutonomousMCPLoopPlanner(_empty_deps()).plan_for_intent(
        huge, caller_kind="test",
    )
    sanitized = plan.intent_query_sanitized
    blob = _serialize_plan(plan).replace(sanitized, "<SANITIZED>")
    assert "SECRET_INTENT_LEAK" not in blob


def test_task_context_only_hashed():
    cr = FakeCapabilityResolver([_p22_plan(decision="no_capability_found")])
    deps = AutonomousMCPLoopDeps(capability_resolver=cr)
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "x", caller_kind="test",
        task_context={"secret": "SECRET_TASK_CONTEXT_LEAK"},
    )
    blob = _serialize_plan(plan)
    assert "SECRET_TASK_CONTEXT_LEAK" not in blob
    assert plan.evidence.get("task_context_hash", "") != ""


def test_evidence_only_whitelist():
    plan = AutonomousMCPLoopPlanner(_empty_deps()).plan_for_intent(
        "x", caller_kind="test",
    )
    for k in plan.evidence.keys():
        assert k in _EVIDENCE_WHITELIST


def test_blocker_codes_only_whitelist():
    cr = FakeCapabilityResolver([_p22_plan(decision="blocked_policy")])
    deps = AutonomousMCPLoopDeps(capability_resolver=cr)
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "x", caller_kind="test",
    )
    for b in plan.blockers:
        assert b.blocker_code in _BLOCKER_CODES


def test_phase22_candidate_raw_not_leaked():
    # Phase 22 selected_candidate contient SECRET dans un attribut non-safe
    selected = SimpleNamespace(
        kind="mcp_active",
        tool_name="mcp_search",
        server_id="srv1",
        match_score=0.7,
        raw_payload="SECRET_PHASE22_CANDIDATE_RAW_LEAK",
    )
    p22 = SimpleNamespace(
        decision=SimpleNamespace(value="use_active_mcp_tool"),
        selected_candidate=selected,
        evidence={"intent_id": "a" * 32, "actionable_intent": False},
    )
    cr = FakeCapabilityResolver([p22])
    plan = AutonomousMCPLoopPlanner(
        AutonomousMCPLoopDeps(capability_resolver=cr)
    ).plan_for_intent("x", caller_kind="test")
    blob = _serialize_plan(plan)
    assert "SECRET_PHASE22_CANDIDATE_RAW_LEAK" not in blob


def test_phase23_search_results_raw_not_leaked():
    # Phase 23 search_results raw contient SECRET — Phase 24 ne propage
    # que evidence whitelistée Phase 23.
    p23 = SimpleNamespace(
        decision=SimpleNamespace(value="propose_catalog_declared"),
        catalog_proposal=SimpleNamespace(
            proposed_server_id="proposed_abc",
            requires_approval=False,
        ),
        evidence={"proposal_id": "b" * 32},  # safe whitelist
        search_results=("SECRET_PHASE23_RESULT_RAW_LEAK",),  # ne doit pas sortir
    )
    p22 = _p22_plan(decision="search_mcp", actionable=True)
    cr = FakeCapabilityResolver([p22])
    pp = FakeProposalPlanner(p23)
    plan = AutonomousMCPLoopPlanner(
        AutonomousMCPLoopDeps(capability_resolver=cr, proposal_planner=pp)
    ).plan_for_intent("x", caller_kind="test")
    blob = _serialize_plan(plan)
    assert "SECRET_PHASE23_RESULT_RAW_LEAK" not in blob


# ══════════════════════════════════════════════════════════════════════════════
# Section 10 — Anti-mutation / anti-subprocess (grep statique)
# ══════════════════════════════════════════════════════════════════════════════


_MODULE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src" / "mcp" / "autonomous_orchestrator.py"
)


_FORBIDDEN_MUTATION_TOKENS = (
    ".install(", ".activate(", ".deactivate(",
    ".approve(", ".reject(", ".propose(",
    ".add_pending(",
    ".add_server(", ".quarantine(", ".restore(", ".remove_server(",
    ".add_pattern(", ".remove_pattern(",
    ".update_trust_score(",
    ".register_runner(", ".unregister_runner(",
    ".register_dynamic_handler(",
    ".update_last_active(",
    ".execute_approved_install(",
    "call_tool(",
    "start_runner(", "stop_runner(",
    "subprocess.", "Popen(",
    "os.system(", "os.exec",
)


@pytest.mark.parametrize("token", _FORBIDDEN_MUTATION_TOKENS)
def test_no_mutation_token_in_module(token):
    text = _MODULE_PATH.read_text(encoding="utf-8")
    assert token not in text, f"{token} interdit en Phase 24"


_FORBIDDEN_IMPORTS = (
    "from src.mcp.install_orchestrator",
    "from src.mcp.activation_service",
    "from src.mcp.client_factory",
    "from src.mcp.sandbox_runner",
    "from src.mcp.capability_resolver",
    "from src.mcp.proposal_planner",
    "from src.mcp.auto_approve",
    "import src.mcp.install_orchestrator",
    "import src.mcp.activation_service",
    "import src.mcp.client_factory",
    "import src.mcp.sandbox_runner",
    "import src.mcp.capability_resolver",
    "import src.mcp.proposal_planner",
    "import src.mcp.auto_approve",
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


def test_no_crypto():
    text = _MODULE_PATH.read_text(encoding="utf-8")
    for tok in (
        "Fernet(", ".decrypt(", "_get_cipher_helper",
        "SecretsService", "secrets_service",
    ):
        assert tok not in text


def test_spec_mocks_no_mutation_called():
    cr = MagicMock(spec=CapabilityResolverLike)
    cr.resolve.return_value = _p22_plan(decision="no_capability_found")
    pp = MagicMock(spec=ProposalPlannerLike)
    aq = MagicMock(spec=ApprovalQueueReadLike)
    aq.list_pending.return_value = []
    aq.get.return_value = None
    deps = AutonomousMCPLoopDeps(
        capability_resolver=cr, proposal_planner=pp, approval_queue_read=aq,
    )
    AutonomousMCPLoopPlanner(deps).plan_for_intent("x", caller_kind="test")
    all_calls = []
    for m in (cr, pp, aq):
        for c in m.mock_calls:
            all_calls.append(str(c))
    forbidden = (
        "install", "activate", "approve", "reject",
        "add_pending", "add_server", "register",
        "call_tool", "execute_approved",
    )
    for c in all_calls:
        for fs in forbidden:
            assert fs not in c, f"mutation suspecte : {c}"


# ══════════════════════════════════════════════════════════════════════════════
# Section 11 — Sources optionnelles
# ══════════════════════════════════════════════════════════════════════════════


def test_planner_dep_none_blocker():
    cr = FakeCapabilityResolver([_p22_plan(decision="search_mcp", actionable=True)])
    deps = AutonomousMCPLoopDeps(capability_resolver=cr)
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "x", caller_kind="test",
    )
    assert any(
        b.blocker_code == "no_phase23_planner" for b in plan.blockers
    )


def test_planner_raises_yields_degraded():
    class Raising:
        def plan_proposal(self, *a, **k):
            raise RuntimeError("boom")
    cr = FakeCapabilityResolver([_p22_plan(decision="search_mcp", actionable=True)])
    deps = AutonomousMCPLoopDeps(
        capability_resolver=cr, proposal_planner=Raising(),
    )
    plan = AutonomousMCPLoopPlanner(deps).plan_for_intent(
        "x", caller_kind="test",
    )
    assert plan.decision == AutonomousMCPLoopDecision.NO_SAFE_PATH
    assert "proposal_planner" in plan.evidence["sources_degraded"]


# ══════════════════════════════════════════════════════════════════════════════
# Section 12 — UTF-8 / anti-mojibake
# ══════════════════════════════════════════════════════════════════════════════


def test_audit_no_mojibake(tmp_path):
    audit = tmp_path / "audit.jsonl"
    cr = FakeCapabilityResolver([_p22_plan(decision="no_capability_found")])
    deps = AutonomousMCPLoopDeps(capability_resolver=cr)
    AutonomousMCPLoopPlanner(deps, audit_log_path=audit).plan_for_intent(
        "envoyer email à éàçôê", caller_kind="test",
    )
    raw = audit.read_text(encoding="utf-8")
    for moji in ("Ã©", "Ã¨", "Ã ", "Ã§", "â€™"):
        assert moji not in raw


# ══════════════════════════════════════════════════════════════════════════════
# Section 13 — Audit local optionnel
# ══════════════════════════════════════════════════════════════════════════════


def test_audit_not_written_when_none(tmp_path):
    AutonomousMCPLoopPlanner(_empty_deps(), audit_log_path=None).plan_for_intent(
        "x", caller_kind="test",
    )
    assert list(tmp_path.iterdir()) == []


def test_audit_written_when_path(tmp_path):
    audit = tmp_path / "audit.jsonl"
    AutonomousMCPLoopPlanner(_empty_deps(), audit_log_path=audit).plan_for_intent(
        "x", caller_kind="test",
    )
    raw = audit.read_text(encoding="utf-8").strip()
    event = json.loads(raw.splitlines()[0])
    assert event["phase"] == "24"
    assert event["event"] == "autonomous_plan_completed"


def test_audit_resume_event_name(tmp_path):
    audit = tmp_path / "audit.jsonl"
    valid = "abcdef0123456789abcdef0123456789"
    aq = FakeApprovalQueue(pending_ids=[valid])
    deps = AutonomousMCPLoopDeps(approval_queue_read=aq)
    AutonomousMCPLoopPlanner(deps, audit_log_path=audit).describe_pending_resume(
        valid, caller_kind="test",
    )
    raw = audit.read_text(encoding="utf-8").strip()
    event = json.loads(raw.splitlines()[0])
    assert event["event"] == "autonomous_resume_descriptor_completed"


def test_audit_no_intent_leak(tmp_path):
    audit = tmp_path / "audit.jsonl"
    AutonomousMCPLoopPlanner(_empty_deps(), audit_log_path=audit).plan_for_intent(
        "SECRET_INTENT_LEAK_" + "x" * 300, caller_kind="test",
    )
    raw = audit.read_text(encoding="utf-8")
    assert "SECRET_INTENT_LEAK" not in raw


def test_audit_parent_dir_created(tmp_path):
    audit = tmp_path / "sub" / "deep" / "audit.jsonl"
    AutonomousMCPLoopPlanner(_empty_deps(), audit_log_path=audit).plan_for_intent(
        "x", caller_kind="test",
    )
    assert audit.exists()


# ══════════════════════════════════════════════════════════════════════════════
# Section 14 — caller_kind whitelist
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
    (123, "unknown"),
])
def test_caller_kind_whitelist(ck, expected):
    plan = AutonomousMCPLoopPlanner(_empty_deps()).plan_for_intent(
        "x", caller_kind=ck,
    )
    assert plan.evidence.get("caller_kind") == expected
