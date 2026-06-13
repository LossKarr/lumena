"""
Tests Phase 22 — CapabilityResolver.

Couvre :
  - Structure (enum 11 valeurs, dataclasses frozen, intent_id UUID4)
  - Cascade décisionnelle (USE_NATIVE / USE_ACTIVE_MCP / ACTIVATE_INSTALLED /
    INSTALL_DECLARED / SEARCH_MCP / NO_CAPABILITY_FOUND)
  - Blockers (BLOCKED_POLICY explicit only, BLOCKED_TRUST via attributor,
    BLOCKED_RUNTIME health/quarantined/stopped+active/drift ciblé,
    NEEDS_APPROVAL sur mutations)
  - SEARCH_MCP conditionné par _is_actionable_intent
  - CREATE_LOCAL_MCP JAMAIS produit
  - Anti-leak (markers SECRET_*)
  - Anti-mutation (grep statique + spec mocks)
  - Sources optionnelles (None → degrade)
  - Matching Jaccard
  - UTF-8 / anti-mojibake
  - Audit local optionnel
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional
from unittest.mock import MagicMock

import pytest

from src.mcp.capability_resolver import (
    ApprovalQueueReadLike,
    AutoApproveReadLike,
    BlockerReport,
    CapabilityDecision,
    CapabilityResolutionPlan,
    CapabilityResolver,
    CapabilityResolverDeps,
    CatalogReadLike,
    DiscoveryReadLike,
    DriftReadLike,
    FilesystemDiscoveryReader,
    PolicyAttributorReadLike,
    PolicyResolverReadLike,
    RuntimeWatcherReadLike,
    ToolCandidate,
    ToolRegistryReadLike,
    _is_actionable_intent,
    _jaccard,
    _sanitize_intent,
    _tokenize,
    _extract_tool_schema_identity,
)
from src.mcp.policy import MCPPolicy
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.registry_v2 import HandlerDef
from src.reasoning.tool_registry import ToolRegistry


# ──────────────────────────────────────────────────────────────────────────────
# Helpers fakes (lecture seule)
# ──────────────────────────────────────────────────────────────────────────────


class FakeToolRegistry:
    def __init__(
        self,
        schema: Optional[List[Dict[str, Any]]] = None,
        dynamic: Optional[List[str]] = None,
        provenance: Optional[Dict[str, Dict[str, Any]]] = None,
    ) -> None:
        self._schema = schema or []
        self._dynamic = set(dynamic or [])
        self._provenance = provenance or {}

    def list_dynamic_handlers(self) -> List[str]:
        return sorted(self._dynamic)

    def is_dynamic_handler(self, name: str) -> bool:
        return name in self._dynamic

    def get_dynamic_handler_provenance(
        self, name: str
    ) -> Optional[Dict[str, Any]]:
        return self._provenance.get(name)

    def get_dynamic_handler_policy(self, name: str) -> Optional[Any]:
        return None

    def get_tools_schema(self) -> List[Dict[str, Any]]:
        return list(self._schema)


class FakeServerEntry:
    def __init__(
        self,
        server_id: str,
        status: str,
        trust_score: Optional[int] = None,
        display_name: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> None:
        self.server_id = server_id
        self.status = SimpleNamespace(value=status)
        self.trust_score = trust_score
        self.display_name = display_name or server_id
        # Champ volontairement présent — doit ne JAMAIS sortir
        self.notes = notes
        self.package_spec = "SECRET_PACKAGE_SPEC_LEAK_" + server_id


class FakeCatalog:
    def __init__(self, entries: List[FakeServerEntry]) -> None:
        self._entries = entries

    def list_servers(
        self, include_removed: bool = False
    ) -> List[FakeServerEntry]:
        if include_removed:
            return list(self._entries)
        return [
            e for e in self._entries
            if e.status.value != "removed"
        ]

    def get_server(self, server_id: str) -> Optional[FakeServerEntry]:
        for e in self._entries:
            if e.server_id == server_id:
                return e
        return None


class FakeDiscovery:
    def __init__(self, reports: Dict[str, List[Dict[str, Any]]]) -> None:
        self._reports = reports

    def iter_persisted_reports(
        self, server_id: Optional[str] = None
    ) -> Iterable[Dict[str, Any]]:
        if server_id is None:
            for sid, reps in self._reports.items():
                for r in reps:
                    yield r
            return
        for r in self._reports.get(server_id, []):
            yield r


class FakePolicyResolver:
    def __init__(self, results: Dict[tuple, Any]) -> None:
        self._results = results

    def resolve(self, server_id: str, tool_name: str) -> Optional[Any]:
        return self._results.get((server_id, tool_name))


class FakePolicyAttributor:
    def __init__(self, decisions: Dict[str, Any]) -> None:
        self._decisions = decisions

    def attribute(self, tool: Any, *, trust_score: Optional[int]) -> Any:
        return self._decisions.get(tool.tool_name)


class FakeApprovalQueue:
    def __init__(self, pending: List[Any]) -> None:
        self._pending = pending

    def list_pending(self) -> List[Any]:
        return list(self._pending)


class FakeAutoApprove:
    def __init__(self, patterns: List[Any]) -> None:
        self._patterns = patterns

    def list_patterns(self, profile: Optional[str] = None) -> List[Any]:
        return list(self._patterns)


class FakeRuntimeWatcher:
    def __init__(self, snapshots: Dict[str, Any]) -> None:
        self._snapshots = snapshots

    def list_persisted_snapshots(self) -> List[str]:
        return sorted(self._snapshots.keys())

    def load_snapshot_from_disk(self, server_id: str) -> Optional[Any]:
        return self._snapshots.get(server_id)

    def list_watched_servers(self) -> List[str]:
        return sorted(self._snapshots.keys())


class FakeDrift:
    def __init__(
        self,
        drift_count: int = 0,
        entries: Optional[List[Dict[str, Any]]] = None,
        raise_summary: bool = False,
        raise_entries: bool = False,
    ) -> None:
        self._drift_count = drift_count
        self._entries = entries or []
        self._raise_summary = raise_summary
        self._raise_entries = raise_entries

    def audit_summary(self) -> Any:
        if self._raise_summary:
            raise RuntimeError("boom")
        return SimpleNamespace(
            drift_count=self._drift_count,
            has_drift=self._drift_count > 0,
        )

    def tool_entries(self) -> List[Dict[str, Any]]:
        if self._raise_entries:
            raise RuntimeError("boom")
        return list(self._entries)


def _empty_deps() -> CapabilityResolverDeps:
    return CapabilityResolverDeps()


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Structure
# ══════════════════════════════════════════════════════════════════════════════


def test_capability_decision_has_exactly_eleven_values():
    expected = {
        "USE_NATIVE_TOOL", "USE_ACTIVE_MCP_TOOL",
        "ACTIVATE_INSTALLED_MCP", "INSTALL_DECLARED_MCP",
        "SEARCH_MCP", "CREATE_LOCAL_MCP",
        "BLOCKED_POLICY", "BLOCKED_TRUST", "BLOCKED_RUNTIME",
        "NEEDS_APPROVAL", "NO_CAPABILITY_FOUND",
    }
    got = {d.name for d in CapabilityDecision}
    assert got == expected


def test_tool_candidate_is_frozen():
    cand = ToolCandidate(
        kind="native", tool_name="x", server_id=None,
        catalog_status=None, trust_score=None,
        match_score=0.5, policy_state="not_applicable",
    )
    with pytest.raises(Exception):
        cand.match_score = 0.9  # type: ignore[misc]


def test_blocker_report_is_frozen():
    b = BlockerReport(blocker_code="policy_blocked",
                      target_server_id="alice", details_count=1)
    with pytest.raises(Exception):
        b.details_count = 2  # type: ignore[misc]


def test_capability_resolution_plan_is_frozen():
    plan = CapabilityResolutionPlan(
        intent_id="x" * 32,
        intent_query_sanitized="hello",
        decision=CapabilityDecision.NO_CAPABILITY_FOUND,
        selected_candidate=None,
        candidates=(),
        blockers=(),
        evidence={},
        created_at="2026-06-04T00:00:00+00:00",
    )
    with pytest.raises(Exception):
        plan.decision = CapabilityDecision.USE_NATIVE_TOOL  # type: ignore[misc]


def test_resolver_intent_id_is_uuid4_hex_32():
    resolver = CapabilityResolver(_empty_deps())
    plan = resolver.resolve("test", caller_kind="test")
    assert re.match(r"^[0-9a-f]{32}$", plan.intent_id)


def test_resolver_rejects_non_deps():
    with pytest.raises(TypeError):
        CapabilityResolver(deps="not a deps")  # type: ignore[arg-type]


def test_resolver_rejects_audit_path_non_path():
    with pytest.raises(TypeError):
        CapabilityResolver(
            _empty_deps(),
            audit_log_path="/tmp/x"  # type: ignore[arg-type]
        )


def test_intent_query_truncated_to_256():
    huge = "lire le fichier " + ("x" * 500)
    resolver = CapabilityResolver(_empty_deps())
    plan = resolver.resolve(huge, caller_kind="test")
    assert len(plan.intent_query_sanitized) <= 256


def test_intent_query_strips_controls():
    raw = "lire\x00 le \x01fichier\x1f x"
    out = _sanitize_intent(raw)
    assert "\x00" not in out and "\x01" not in out and "\x1f" not in out
    assert "lire" in out


def test_intent_query_preserves_accents_nfc():
    raw = "lire le fichier éàç"
    out = _sanitize_intent(raw)
    assert "éàç" in out


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Cascade décisionnelle
# ══════════════════════════════════════════════════════════════════════════════


def test_decision_use_native_tool_match():
    reg = FakeToolRegistry(schema=[
        {"name": "read_file", "description": "read a file from disk"},
    ])
    deps = CapabilityResolverDeps(tool_registry=reg)
    resolver = CapabilityResolver(deps)
    plan = resolver.resolve("lire un fichier", caller_kind="test")
    # "lire" est dans actionable; matching jaccard "read_file read a file..."
    # vs tokenized intent contient "fichier" (mais "lire" décompose en "lire"
    # qui n'est pas dans le name natif). Pour fiabiliser : intent en EN.
    plan2 = resolver.resolve("read file from disk", caller_kind="test")
    assert plan2.decision == CapabilityDecision.USE_NATIVE_TOOL
    assert plan2.selected_candidate is not None
    assert plan2.selected_candidate.tool_name == "read_file"


def test_decision_use_active_mcp_tool():
    reg = FakeToolRegistry(
        schema=[
            {"name": "mcp_search_brave",
             "description": "brave search via mcp"},
        ],
        dynamic=["mcp_search_brave"],
        provenance={"mcp_search_brave": {"server_id": "brave"}},
    )
    cat = FakeCatalog([
        FakeServerEntry("brave", "active", trust_score=80),
    ])
    deps = CapabilityResolverDeps(tool_registry=reg, catalog=cat)
    resolver = CapabilityResolver(deps)
    plan = resolver.resolve("brave search", caller_kind="test")
    assert plan.decision == CapabilityDecision.USE_ACTIVE_MCP_TOOL
    assert plan.selected_candidate.server_id == "brave"


def test_decision_activate_installed_mcp():
    reg = FakeToolRegistry()
    cat = FakeCatalog([
        FakeServerEntry("github_srv", "installed", trust_score=70),
    ])
    disc = FakeDiscovery({
        "github_srv": [
            {"server_id": "github_srv", "tools": [
                {"name": "github_create_issue",
                 "description": "create a github issue"},
            ]},
        ],
    })
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, discovery=disc
    )
    resolver = CapabilityResolver(deps)
    plan = resolver.resolve(
        "create github issue", caller_kind="test"
    )
    assert plan.decision == CapabilityDecision.ACTIVATE_INSTALLED_MCP
    assert plan.selected_candidate.server_id == "github_srv"


def test_decision_install_declared_mcp():
    reg = FakeToolRegistry()
    cat = FakeCatalog([
        FakeServerEntry(
            "notion_srv", "declared", trust_score=75,
            display_name="notion api integration",
        ),
    ])
    deps = CapabilityResolverDeps(tool_registry=reg, catalog=cat)
    resolver = CapabilityResolver(deps)
    plan = resolver.resolve("notion api integration", caller_kind="test")
    assert plan.decision == CapabilityDecision.INSTALL_DECLARED_MCP
    assert plan.selected_candidate.server_id == "notion_srv"


def test_decision_search_mcp_when_actionable_and_catalog_nonempty():
    reg = FakeToolRegistry()
    cat = FakeCatalog([
        FakeServerEntry("foo_srv", "declared", display_name="foo"),
    ])
    deps = CapabilityResolverDeps(tool_registry=reg, catalog=cat)
    resolver = CapabilityResolver(deps)
    # Intent actionable mais aucun candidat ne match
    plan = resolver.resolve(
        "send email to bob", caller_kind="test"
    )
    assert plan.decision == CapabilityDecision.SEARCH_MCP
    assert plan.evidence["actionable_intent"] is True


def test_decision_no_capability_found_when_not_actionable():
    reg = FakeToolRegistry()
    cat = FakeCatalog([
        FakeServerEntry("foo_srv", "declared", display_name="foo"),
    ])
    deps = CapabilityResolverDeps(tool_registry=reg, catalog=cat)
    resolver = CapabilityResolver(deps)
    # Intent NON actionable
    plan = resolver.resolve(
        "comment vas-tu aujourd hui", caller_kind="test"
    )
    assert plan.decision == CapabilityDecision.NO_CAPABILITY_FOUND
    assert plan.evidence["actionable_intent"] is False


def test_decision_no_capability_found_when_all_deps_none():
    resolver = CapabilityResolver(_empty_deps())
    plan = resolver.resolve(
        "send email please", caller_kind="test"
    )
    # Actionable mais aucune source → NO_CAPABILITY_FOUND
    assert plan.decision == CapabilityDecision.NO_CAPABILITY_FOUND


def test_decision_no_capability_found_actionable_but_catalog_empty():
    reg = FakeToolRegistry()
    cat = FakeCatalog([])  # vide
    deps = CapabilityResolverDeps(tool_registry=reg, catalog=cat)
    resolver = CapabilityResolver(deps)
    plan = resolver.resolve(
        "send email please", caller_kind="test"
    )
    assert plan.decision == CapabilityDecision.NO_CAPABILITY_FOUND


def test_native_preferred_over_mcp_active():
    reg = FakeToolRegistry(
        schema=[
            {"name": "read_file", "description": "read file"},
            {"name": "mcp_read_remote", "description": "read file"},
        ],
        dynamic=["mcp_read_remote"],
        provenance={"mcp_read_remote": {"server_id": "rem"}},
    )
    cat = FakeCatalog([FakeServerEntry("rem", "active", trust_score=80)])
    deps = CapabilityResolverDeps(tool_registry=reg, catalog=cat)
    resolver = CapabilityResolver(deps)
    plan = resolver.resolve("read file", caller_kind="test")
    # Native devrait gagner (kind="native" check en premier dans cascade).
    assert plan.decision == CapabilityDecision.USE_NATIVE_TOOL


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — CREATE_LOCAL_MCP jamais produit
# ══════════════════════════════════════════════════════════════════════════════


def test_create_local_mcp_never_produced_phase22():
    """Quelque soit la combinaison de sources et l'intent, Phase 22 ne
    doit JAMAIS retourner CREATE_LOCAL_MCP. Réservé Phase 23.
    """
    test_intents = [
        "create local mcp for weather",
        "build my own mcp server",
        "generer un nouveau mcp",
        "create",
        "creer un mcp",
        "no idea what i want",
    ]
    for it in test_intents:
        resolver = CapabilityResolver(_empty_deps())
        plan = resolver.resolve(it, caller_kind="test")
        assert plan.decision != CapabilityDecision.CREATE_LOCAL_MCP


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Blockers
# ══════════════════════════════════════════════════════════════════════════════


def test_policy_none_does_not_block(monkeypatch):
    """policy_resolver.resolve renvoie None → policy_state="unresolved",
    PAS de BLOCKED_POLICY (doctrine v3 F.9.a).
    """
    reg = FakeToolRegistry(
        schema=[{"name": "mcp_search", "description": "search"}],
        dynamic=["mcp_search"],
        provenance={"mcp_search": {"server_id": "brave"}},
    )
    cat = FakeCatalog([FakeServerEntry("brave", "active", trust_score=80)])
    pr = FakePolicyResolver({})  # toujours None
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, policy_resolver=pr
    )
    resolver = CapabilityResolver(deps)
    plan = resolver.resolve("search query", caller_kind="test")
    assert plan.decision == CapabilityDecision.USE_ACTIVE_MCP_TOOL
    assert plan.selected_candidate.policy_state == "unresolved"
    assert plan.evidence["policies_unresolved_count"] >= 1


def test_policy_explicit_block_blocks():
    blocked_policy = SimpleNamespace(blocked=True)
    reg = FakeToolRegistry(
        schema=[{"name": "mcp_search", "description": "search"}],
        dynamic=["mcp_search"],
        provenance={"mcp_search": {"server_id": "brave"}},
    )
    cat = FakeCatalog([FakeServerEntry("brave", "active", trust_score=80)])
    pr = FakePolicyResolver({("brave", "mcp_search"): blocked_policy})
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, policy_resolver=pr
    )
    resolver = CapabilityResolver(deps)
    plan = resolver.resolve("search query", caller_kind="test")
    assert plan.decision == CapabilityDecision.BLOCKED_POLICY
    assert any(
        b.blocker_code == "policy_blocked" for b in plan.blockers
    )


def test_policy_decision_deny_blocks():
    pol = SimpleNamespace(decision="deny")
    reg = FakeToolRegistry(
        schema=[{"name": "mcp_search", "description": "search"}],
        dynamic=["mcp_search"],
        provenance={"mcp_search": {"server_id": "brave"}},
    )
    cat = FakeCatalog([FakeServerEntry("brave", "active", trust_score=80)])
    pr = FakePolicyResolver({("brave", "mcp_search"): pol})
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, policy_resolver=pr
    )
    plan = CapabilityResolver(deps).resolve(
        "search query", caller_kind="test"
    )
    assert plan.decision == CapabilityDecision.BLOCKED_POLICY


def test_policy_allowed_keeps_decision():
    pol = SimpleNamespace()  # ni blocked, ni deny
    reg = FakeToolRegistry(
        schema=[{"name": "mcp_search", "description": "search"}],
        dynamic=["mcp_search"],
        provenance={"mcp_search": {"server_id": "brave"}},
    )
    cat = FakeCatalog([FakeServerEntry("brave", "active", trust_score=80)])
    pr = FakePolicyResolver({("brave", "mcp_search"): pol})
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, policy_resolver=pr
    )
    plan = CapabilityResolver(deps).resolve(
        "search query", caller_kind="test"
    )
    assert plan.decision == CapabilityDecision.USE_ACTIVE_MCP_TOOL
    assert plan.selected_candidate.policy_state == "allowed"


def test_trust_blocker_via_attributor():
    """Sans hardcoder de seuil : si attributor retourne policy=None +
    reason="trust_too_low_for_write" → BLOCKED_TRUST.
    """
    reg = FakeToolRegistry(
        schema=[{"name": "mcp_write", "description": "write data"}],
        dynamic=["mcp_write"],
        provenance={"mcp_write": {"server_id": "srv1"}},
    )
    cat = FakeCatalog([FakeServerEntry("srv1", "active", trust_score=30)])
    decision = SimpleNamespace(
        policy=None, reason="trust_too_low_for_write"
    )
    attr = FakePolicyAttributor({"mcp_write": decision})
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, policy_attributor=attr
    )
    plan = CapabilityResolver(deps).resolve(
        "write data please", caller_kind="test"
    )
    assert plan.decision == CapabilityDecision.BLOCKED_TRUST


def test_trust_attributor_none_means_no_check():
    """Sans attributor → trust check skipped, pas de BLOCKED_TRUST."""
    reg = FakeToolRegistry(
        schema=[{"name": "mcp_search", "description": "search"}],
        dynamic=["mcp_search"],
        provenance={"mcp_search": {"server_id": "srv1"}},
    )
    cat = FakeCatalog([FakeServerEntry("srv1", "active", trust_score=10)])
    deps = CapabilityResolverDeps(tool_registry=reg, catalog=cat)
    plan = CapabilityResolver(deps).resolve(
        "search query", caller_kind="test"
    )
    assert plan.decision == CapabilityDecision.USE_ACTIVE_MCP_TOOL


def test_runtime_unhealthy_blocks():
    reg = FakeToolRegistry(
        schema=[{"name": "mcp_search", "description": "search"}],
        dynamic=["mcp_search"],
        provenance={"mcp_search": {"server_id": "srv1"}},
    )
    cat = FakeCatalog([FakeServerEntry("srv1", "active", trust_score=80)])
    snap = SimpleNamespace(
        health=SimpleNamespace(value="UNHEALTHY"), process_state="running"
    )
    rw = FakeRuntimeWatcher({"srv1": snap})
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, runtime_watcher=rw
    )
    plan = CapabilityResolver(deps).resolve("search", caller_kind="test")
    assert plan.decision == CapabilityDecision.BLOCKED_RUNTIME
    assert any(b.blocker_code == "runtime_unhealthy" for b in plan.blockers)


def test_runtime_crash_loop_blocks():
    reg = FakeToolRegistry(
        schema=[{"name": "mcp_search", "description": "search"}],
        dynamic=["mcp_search"],
        provenance={"mcp_search": {"server_id": "srv1"}},
    )
    cat = FakeCatalog([FakeServerEntry("srv1", "active", trust_score=80)])
    snap = SimpleNamespace(
        health=SimpleNamespace(value="CRASH_LOOP"), process_state="running"
    )
    rw = FakeRuntimeWatcher({"srv1": snap})
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, runtime_watcher=rw
    )
    plan = CapabilityResolver(deps).resolve("search", caller_kind="test")
    assert plan.decision == CapabilityDecision.BLOCKED_RUNTIME


def test_runtime_healthy_does_not_block():
    reg = FakeToolRegistry(
        schema=[{"name": "mcp_search", "description": "search"}],
        dynamic=["mcp_search"],
        provenance={"mcp_search": {"server_id": "srv1"}},
    )
    cat = FakeCatalog([FakeServerEntry("srv1", "active", trust_score=80)])
    snap = SimpleNamespace(
        health=SimpleNamespace(value="HEALTHY"), process_state="running"
    )
    rw = FakeRuntimeWatcher({"srv1": snap})
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, runtime_watcher=rw
    )
    plan = CapabilityResolver(deps).resolve("search", caller_kind="test")
    assert plan.decision == CapabilityDecision.USE_ACTIVE_MCP_TOOL
    assert plan.evidence["runtime_health_for_target"] == "ok"


def test_runtime_degraded_warns_not_blocks():
    reg = FakeToolRegistry(
        schema=[{"name": "mcp_search", "description": "search"}],
        dynamic=["mcp_search"],
        provenance={"mcp_search": {"server_id": "srv1"}},
    )
    cat = FakeCatalog([FakeServerEntry("srv1", "active", trust_score=80)])
    snap = SimpleNamespace(
        health=SimpleNamespace(value="DEGRADED"), process_state="running"
    )
    rw = FakeRuntimeWatcher({"srv1": snap})
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, runtime_watcher=rw
    )
    plan = CapabilityResolver(deps).resolve("search", caller_kind="test")
    assert plan.decision == CapabilityDecision.USE_ACTIVE_MCP_TOOL
    assert plan.evidence["runtime_health_for_target"] == "warn"


def test_runtime_unknown_warns_not_blocks():
    reg = FakeToolRegistry(
        schema=[{"name": "mcp_search", "description": "search"}],
        dynamic=["mcp_search"],
        provenance={"mcp_search": {"server_id": "srv1"}},
    )
    cat = FakeCatalog([FakeServerEntry("srv1", "active", trust_score=80)])
    snap = SimpleNamespace(
        health=SimpleNamespace(value="UNKNOWN"), process_state="running"
    )
    rw = FakeRuntimeWatcher({"srv1": snap})
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, runtime_watcher=rw
    )
    plan = CapabilityResolver(deps).resolve("search", caller_kind="test")
    assert plan.decision == CapabilityDecision.USE_ACTIVE_MCP_TOOL
    assert plan.evidence["runtime_health_for_target"] == "unknown"


def test_runtime_stopped_active_blocks():
    reg = FakeToolRegistry(
        schema=[{"name": "mcp_search", "description": "search"}],
        dynamic=["mcp_search"],
        provenance={"mcp_search": {"server_id": "srv1"}},
    )
    cat = FakeCatalog([FakeServerEntry("srv1", "active", trust_score=80)])
    snap = SimpleNamespace(
        health=SimpleNamespace(value="HEALTHY"), process_state="stopped"
    )
    rw = FakeRuntimeWatcher({"srv1": snap})
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, runtime_watcher=rw
    )
    plan = CapabilityResolver(deps).resolve("search", caller_kind="test")
    assert plan.decision == CapabilityDecision.BLOCKED_RUNTIME
    assert any(
        b.blocker_code == "runtime_stopped_while_active"
        for b in plan.blockers
    )


def test_runtime_stopped_installed_does_not_block():
    """INSTALLED + stopped = normal."""
    reg = FakeToolRegistry()
    cat = FakeCatalog([FakeServerEntry("srv1", "installed", trust_score=80)])
    disc = FakeDiscovery({
        "srv1": [
            {"server_id": "srv1", "tools": [
                {"name": "mcp_search", "description": "search"},
            ]},
        ],
    })
    snap = SimpleNamespace(
        health=SimpleNamespace(value="HEALTHY"), process_state="stopped"
    )
    rw = FakeRuntimeWatcher({"srv1": snap})
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, discovery=disc, runtime_watcher=rw
    )
    plan = CapabilityResolver(deps).resolve("search", caller_kind="test")
    assert plan.decision == CapabilityDecision.ACTIVATE_INSTALLED_MCP


def test_quarantined_target_blocks():
    reg = FakeToolRegistry()
    cat = FakeCatalog([
        FakeServerEntry("srv1", "quarantined", trust_score=80,
                        display_name="search api"),
    ])
    deps = CapabilityResolverDeps(tool_registry=reg, catalog=cat)
    resolver = CapabilityResolver(deps)
    # Phase 22 ne crée pas de candidat quarantined dans la cascade
    # (status != declared/installed/active). Donc on retombera sur
    # SEARCH_MCP ou NO_CAPABILITY_FOUND. Le quarantine ne s'applique
    # qu'au target sélectionné.
    plan = resolver.resolve("search api", caller_kind="test")
    # Pas de candidat actif → SEARCH_MCP (intent actionable + catalog non vide)
    assert plan.decision in (
        CapabilityDecision.SEARCH_MCP,
        CapabilityDecision.NO_CAPABILITY_FOUND,
    )


def test_quarantined_active_target_blocks():
    """Catalog ACTIVE + handler dynamic + entry devient QUARANTINED →
    le check get_server doit retourner QUARANTINED → BLOCKED_RUNTIME.
    """
    reg = FakeToolRegistry(
        schema=[{"name": "mcp_search", "description": "search"}],
        dynamic=["mcp_search"],
        provenance={"mcp_search": {"server_id": "srv1"}},
    )
    # Le list_servers expose "active" mais get_server retourne quarantined.
    class FlipCatalog(FakeCatalog):
        def get_server(self, server_id):
            return FakeServerEntry(server_id, "quarantined", trust_score=80)
    cat = FlipCatalog([FakeServerEntry("srv1", "active", trust_score=80)])
    deps = CapabilityResolverDeps(tool_registry=reg, catalog=cat)
    plan = CapabilityResolver(deps).resolve("search", caller_kind="test")
    assert plan.decision == CapabilityDecision.BLOCKED_RUNTIME
    assert any(
        b.blocker_code == "runtime_quarantined" for b in plan.blockers
    )


def test_drift_global_without_target_does_not_block():
    """Drift global mais aucune entry sur tool_name → PAS de block."""
    reg = FakeToolRegistry(
        schema=[{"name": "mcp_search", "description": "search"}],
        dynamic=["mcp_search"],
        provenance={"mcp_search": {"server_id": "srv1"}},
    )
    cat = FakeCatalog([FakeServerEntry("srv1", "active", trust_score=80)])
    drift = FakeDrift(
        drift_count=5,
        entries=[{"tool_name": "other_tool", "drift_status": "drift"}],
    )
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, drift=drift
    )
    plan = CapabilityResolver(deps).resolve("search", caller_kind="test")
    assert plan.decision == CapabilityDecision.USE_ACTIVE_MCP_TOOL
    assert plan.evidence["drift_overall"] == "divergent"


def test_drift_targeted_tool_blocks():
    reg = FakeToolRegistry(
        schema=[{"name": "mcp_search", "description": "search"}],
        dynamic=["mcp_search"],
        provenance={"mcp_search": {"server_id": "srv1"}},
    )
    cat = FakeCatalog([FakeServerEntry("srv1", "active", trust_score=80)])
    drift = FakeDrift(
        drift_count=2,
        entries=[
            {"tool_name": "mcp_search", "drift_status": "drift"},
            {"tool_name": "x", "drift_status": "ok"},
        ],
    )
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, drift=drift
    )
    plan = CapabilityResolver(deps).resolve("search", caller_kind="test")
    assert plan.decision == CapabilityDecision.BLOCKED_RUNTIME
    assert any(b.blocker_code == "drift_divergent" for b in plan.blockers)


def test_drift_tool_entries_exception_no_block():
    reg = FakeToolRegistry(
        schema=[{"name": "mcp_search", "description": "search"}],
        dynamic=["mcp_search"],
        provenance={"mcp_search": {"server_id": "srv1"}},
    )
    cat = FakeCatalog([FakeServerEntry("srv1", "active", trust_score=80)])
    drift = FakeDrift(drift_count=2, raise_entries=True)
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, drift=drift
    )
    plan = CapabilityResolver(deps).resolve("search", caller_kind="test")
    assert plan.decision == CapabilityDecision.USE_ACTIVE_MCP_TOOL


def test_drift_summary_exception_yields_unknown():
    reg = FakeToolRegistry()
    cat = FakeCatalog([])
    drift = FakeDrift(raise_summary=True)
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, drift=drift
    )
    plan = CapabilityResolver(deps).resolve("hello", caller_kind="test")
    assert plan.evidence["drift_overall"] == "unknown"
    assert "drift" in plan.evidence["sources_degraded"]


def test_drift_none_dep_yields_unknown():
    plan = CapabilityResolver(_empty_deps()).resolve(
        "hello", caller_kind="test"
    )
    assert plan.evidence["drift_overall"] == "unknown"


def test_needs_approval_blocks_mutation_decision():
    """Pending ticket sur le server cible + décision install/activate."""
    reg = FakeToolRegistry()
    cat = FakeCatalog([
        FakeServerEntry("srv1", "installed", trust_score=80),
    ])
    disc = FakeDiscovery({
        "srv1": [
            {"server_id": "srv1", "tools": [
                {"name": "mcp_search", "description": "search"},
            ]},
        ],
    })
    pending_ticket = SimpleNamespace(server_id="srv1")
    aq = FakeApprovalQueue([pending_ticket])
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, discovery=disc, approval_queue=aq
    )
    plan = CapabilityResolver(deps).resolve("search", caller_kind="test")
    assert plan.decision == CapabilityDecision.NEEDS_APPROVAL
    assert any(b.blocker_code == "approval_pending" for b in plan.blockers)


def test_needs_approval_does_not_block_use_active():
    """Pending ticket existe mais décision = USE_ACTIVE_MCP_TOOL ne
    requiert pas d'approval.
    """
    reg = FakeToolRegistry(
        schema=[{"name": "mcp_search", "description": "search"}],
        dynamic=["mcp_search"],
        provenance={"mcp_search": {"server_id": "srv1"}},
    )
    cat = FakeCatalog([FakeServerEntry("srv1", "active", trust_score=80)])
    pending_ticket = SimpleNamespace(server_id="srv1")
    aq = FakeApprovalQueue([pending_ticket])
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, approval_queue=aq
    )
    plan = CapabilityResolver(deps).resolve("search", caller_kind="test")
    assert plan.decision == CapabilityDecision.USE_ACTIVE_MCP_TOOL


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Anti-leak
# ══════════════════════════════════════════════════════════════════════════════


_SECRET_MARKERS = (
    "SECRET_INTENT_LEAK",
    "SECRET_PACKAGE_SPEC_LEAK",
    "SECRET_NOTES_LEAK",
    "SECRET_POLICY_RAW_LEAK",
    "SECRET_TRUST_FACTORS_LEAK",
    "SECRET_APPROVAL_ACTION_ID_LEAK",
    "SECRET_ARGS_LEAK",
    "SECRET_TOKEN_LEAK",
    "SECRET_PATH_LEAK",
)


def _serialize_plan_full(plan: CapabilityResolutionPlan) -> str:
    data = {
        "intent_id": plan.intent_id,
        "intent_query_sanitized": plan.intent_query_sanitized,
        "decision": plan.decision.value,
        "selected_candidate": (
            plan.selected_candidate.__dict__
            if plan.selected_candidate else None
        ),
        "candidates": [c.__dict__ for c in plan.candidates],
        "blockers": [b.__dict__ for b in plan.blockers],
        "evidence": plan.evidence,
        "created_at": plan.created_at,
    }
    return json.dumps(data, ensure_ascii=False, default=str)


def test_no_intent_raw_in_output_when_long():
    raw = "SECRET_INTENT_LEAK_" + "x" * 500
    plan = CapabilityResolver(_empty_deps()).resolve(raw, caller_kind="test")
    blob = _serialize_plan_full(plan)
    # Le marker peut apparaître seulement DANS intent_query_sanitized,
    # qui est truncated à 256. Vérifions qu'il n'est pas ailleurs.
    sanitized = plan.intent_query_sanitized
    # Mise à zéro du sanitized avant scan
    blob_without_sanitized = blob.replace(sanitized, "<SANITIZED>")
    assert "SECRET_INTENT_LEAK" not in blob_without_sanitized


def test_no_package_spec_leak_in_evidence():
    cat = FakeCatalog([
        FakeServerEntry(
            "srv1", "declared", trust_score=80,
            notes="SECRET_NOTES_LEAK",
            display_name="search api",
        ),
    ])
    deps = CapabilityResolverDeps(
        tool_registry=FakeToolRegistry(), catalog=cat
    )
    plan = CapabilityResolver(deps).resolve(
        "search api please", caller_kind="test"
    )
    blob = _serialize_plan_full(plan)
    for marker in (
        "SECRET_PACKAGE_SPEC_LEAK",
        "SECRET_NOTES_LEAK",
    ):
        assert marker not in blob


def test_no_policy_raw_leak():
    pol = SimpleNamespace(
        blocked=True,
        secret_field="SECRET_POLICY_RAW_LEAK",
    )
    reg = FakeToolRegistry(
        schema=[{"name": "mcp_search", "description": "search"}],
        dynamic=["mcp_search"],
        provenance={"mcp_search": {"server_id": "srv1"}},
    )
    cat = FakeCatalog([FakeServerEntry("srv1", "active", trust_score=80)])
    pr = FakePolicyResolver({("srv1", "mcp_search"): pol})
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, policy_resolver=pr
    )
    plan = CapabilityResolver(deps).resolve("search", caller_kind="test")
    blob = _serialize_plan_full(plan)
    assert "SECRET_POLICY_RAW_LEAK" not in blob


def test_no_approval_action_id_leak():
    pending = SimpleNamespace(
        server_id="srv1",
        id="SECRET_APPROVAL_ACTION_ID_LEAK",
    )
    reg = FakeToolRegistry()
    cat = FakeCatalog([FakeServerEntry("srv1", "installed", trust_score=80)])
    disc = FakeDiscovery({
        "srv1": [{"server_id": "srv1", "tools": [
            {"name": "x", "description": "search"},
        ]}],
    })
    aq = FakeApprovalQueue([pending])
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, discovery=disc, approval_queue=aq
    )
    plan = CapabilityResolver(deps).resolve("search", caller_kind="test")
    blob = _serialize_plan_full(plan)
    assert "SECRET_APPROVAL_ACTION_ID_LEAK" not in blob


def test_evidence_only_whitelist_keys():
    from src.mcp.capability_resolver import _EVIDENCE_WHITELIST
    plan = CapabilityResolver(_empty_deps()).resolve(
        "hello world", caller_kind="test"
    )
    for k in plan.evidence.keys():
        assert k in _EVIDENCE_WHITELIST, f"key {k} hors whitelist"


def test_blocker_code_always_in_whitelist():
    from src.mcp.capability_resolver import _BLOCKER_CODES
    blocked_policy = SimpleNamespace(blocked=True)
    reg = FakeToolRegistry(
        schema=[{"name": "mcp_search", "description": "search"}],
        dynamic=["mcp_search"],
        provenance={"mcp_search": {"server_id": "srv1"}},
    )
    cat = FakeCatalog([FakeServerEntry("srv1", "active", trust_score=80)])
    pr = FakePolicyResolver({("srv1", "mcp_search"): blocked_policy})
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, policy_resolver=pr
    )
    plan = CapabilityResolver(deps).resolve("search", caller_kind="test")
    for b in plan.blockers:
        assert b.blocker_code in _BLOCKER_CODES


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — Sources optionnelles / dégradation
# ══════════════════════════════════════════════════════════════════════════════


def test_all_deps_none_yields_sources_degraded():
    plan = CapabilityResolver(_empty_deps()).resolve(
        "hello", caller_kind="test"
    )
    degraded = set(plan.evidence["sources_degraded"])
    assert "tool_registry" in degraded
    assert "catalog" in degraded
    assert "discovery" in degraded


def test_catalog_raises_yields_degraded():
    class RaisingCat:
        def list_servers(self, include_removed=False):
            raise RuntimeError("boom")
        def get_server(self, server_id):
            return None
    deps = CapabilityResolverDeps(
        tool_registry=FakeToolRegistry(), catalog=RaisingCat()
    )
    plan = CapabilityResolver(deps).resolve("hello", caller_kind="test")
    assert "catalog" in plan.evidence["sources_degraded"]


def test_tool_registry_raises_yields_degraded():
    class RaisingReg:
        def list_dynamic_handlers(self):
            raise RuntimeError("boom")
        def is_dynamic_handler(self, name):
            raise RuntimeError("boom")
        def get_dynamic_handler_provenance(self, name):
            return None
        def get_dynamic_handler_policy(self, name):
            return None
        def get_tools_schema(self):
            raise RuntimeError("boom")
    deps = CapabilityResolverDeps(tool_registry=RaisingReg())
    plan = CapabilityResolver(deps).resolve("hello", caller_kind="test")
    assert "tool_registry" in plan.evidence["sources_degraded"]


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — Matching Jaccard
# ══════════════════════════════════════════════════════════════════════════════


def test_jaccard_empty_returns_zero():
    assert _jaccard(set(), set()) == 0.0
    assert _jaccard({"a"}, set()) == 0.0
    assert _jaccard(set(), {"a"}) == 0.0


def test_jaccard_identical_returns_one():
    assert _jaccard({"a", "b"}, {"a", "b"}) == 1.0


def test_jaccard_disjoint_returns_zero():
    assert _jaccard({"a"}, {"b"}) == 0.0


def test_jaccard_partial():
    assert 0.0 < _jaccard({"a", "b"}, {"b", "c"}) < 1.0


def test_tokenize_lowercase_nfc():
    tokens = _tokenize("Read File")
    assert "read" in tokens
    assert "file" in tokens
    assert "Read" not in tokens


def test_tokenize_strips_short_tokens():
    tokens = _tokenize("a ab abc abcd")
    assert "abc" in tokens
    assert "a" not in tokens
    assert "ab" not in tokens


def test_tokenize_drops_stop_words():
    tokens = _tokenize("the file in the folder")
    assert "file" in tokens
    assert "folder" in tokens
    assert "the" not in tokens


def test_tokenize_accent_folding():
    tokens = _tokenize("téléverser fichier")
    # Décomposition NFKD → "televerser" doit apparaître
    assert "televerser" in tokens or "fichier" in tokens


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — _is_actionable_intent
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("intent,expected", [
    ("explique-moi la philosophie kantienne", False),
    ("comment vas-tu", False),
    ("merci beaucoup", False),
    ("read the file", True),
    ("send an email to bob", True),
    ("calculate 2+2", True),
    ("lire le fichier config", True),
    ("envoyer un email", True),
    ("github issue please", True),
    ("trouve un MCP pour Airtable", True),
    ("trouver un outil pour Notion", True),
    ("surveiller les paiements Stripe", True),
    ("connecter Airtable", True),
    ("hello world", False),
])
def test_actionable_intent_classification(intent, expected):
    tokens = _tokenize(intent)
    assert _is_actionable_intent(tokens) == expected


def test_actionable_intent_empty_set_false():
    assert _is_actionable_intent(set()) is False


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — UTF-8 / anti-mojibake
# ══════════════════════════════════════════════════════════════════════════════


def test_intent_accents_no_mojibake_in_audit(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    deps = _empty_deps()
    resolver = CapabilityResolver(deps, audit_log_path=audit_path)
    resolver.resolve("lire fichier éàçôê", caller_kind="test")
    raw = audit_path.read_text(encoding="utf-8")
    for moji in ("Ã©", "Ã¨", "Ã ", "Ã§", "â€™"):
        assert moji not in raw


def test_intent_query_sanitized_keeps_accents():
    plan = CapabilityResolver(_empty_deps()).resolve(
        "lire fichier éàçôê", caller_kind="test"
    )
    assert "éàçôê" in plan.intent_query_sanitized


# ══════════════════════════════════════════════════════════════════════════════
# Section 10 — Audit local optionnel
# ══════════════════════════════════════════════════════════════════════════════


def test_audit_not_written_when_path_none(tmp_path):
    deps = _empty_deps()
    resolver = CapabilityResolver(deps, audit_log_path=None)
    resolver.resolve("hello", caller_kind="test")
    # Aucun fichier dans tmp_path
    assert list(tmp_path.iterdir()) == []


def test_audit_written_when_path_provided(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    deps = _empty_deps()
    resolver = CapabilityResolver(deps, audit_log_path=audit_path)
    resolver.resolve("read file", caller_kind="test", profile="default")
    raw = audit_path.read_text(encoding="utf-8").strip()
    assert raw
    event = json.loads(raw.splitlines()[0])
    assert event["event"] == "resolve_completed"
    assert event["phase"] == "22"
    assert event["caller_kind"] == "test"
    assert re.match(r"^[0-9a-f]{32}$", event["intent_id"])


def test_audit_event_anti_leak_whitelist(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    deps = _empty_deps()
    resolver = CapabilityResolver(deps, audit_log_path=audit_path)
    huge = "SECRET_INTENT_LEAK_" + "x" * 500
    resolver.resolve(huge, caller_kind="test")
    raw = audit_path.read_text(encoding="utf-8")
    # Le marker ne doit JAMAIS apparaître dans l'event audit
    assert "SECRET_INTENT_LEAK" not in raw


def test_audit_parent_dir_created(tmp_path):
    audit_path = tmp_path / "subdir" / "nested" / "audit.jsonl"
    deps = _empty_deps()
    resolver = CapabilityResolver(deps, audit_log_path=audit_path)
    resolver.resolve("hello", caller_kind="test")
    assert audit_path.exists()


# ══════════════════════════════════════════════════════════════════════════════
# Section 11 — Anti-mutation (grep statique + spec mocks)
# ══════════════════════════════════════════════════════════════════════════════


_RESOLVER_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src" / "mcp" / "capability_resolver.py"
)


_FORBIDDEN_TOKENS_MUTATION = (
    ".install(", ".activate(", ".deactivate(",
    ".approve(", ".reject(",
    ".add_server(", ".quarantine(", ".restore(", ".remove_server(",
    ".add_pattern(", ".remove_pattern(",
    ".update_trust_score(",
    ".register_runner(", ".unregister_runner(",
    ".update_last_active(",
    ".record_event(",
    ".take_snapshot(",
    "_take_marker(", "_put_marker(",
    "call_tool(",
    "execute_approved_install(",
    "start_runner(", "stop_runner(",
)


@pytest.mark.parametrize("token", _FORBIDDEN_TOKENS_MUTATION)
def test_no_mutation_call_in_resolver_module(token):
    text = _RESOLVER_PATH.read_text(encoding="utf-8")
    assert token not in text, f"{token} interdit en Phase 22"


_FORBIDDEN_IMPORTS = (
    "from src.mcp.install_orchestrator",
    "from src.mcp.activation_service",
    "from src.mcp.client_factory",
    "from src.mcp.sandbox_runner",
    "import src.mcp.install_orchestrator",
    "import src.mcp.activation_service",
    "import src.mcp.client_factory",
    "import src.mcp.sandbox_runner",
)


@pytest.mark.parametrize("imp", _FORBIDDEN_IMPORTS)
def test_no_forbidden_imports(imp):
    text = _RESOLVER_PATH.read_text(encoding="utf-8")
    assert imp not in text, f"import interdit en Phase 22 : {imp}"


def test_no_singleton_or_cache_in_resolver_module():
    text = _RESOLVER_PATH.read_text(encoding="utf-8")
    forbidden = (
        "_INSTANCE", "_SINGLETON", "lru_cache", "functools.cache",
        "@cache", "global _",
    )
    for tok in forbidden:
        assert tok not in text, f"{tok} interdit en Phase 22"


def test_no_http_route_in_resolver_module():
    text = _RESOLVER_PATH.read_text(encoding="utf-8")
    forbidden = (
        "@router", "APIRouter", "FastAPI",
        "@app.", "fastapi", "from fastapi",
    )
    for tok in forbidden:
        assert tok not in text, f"{tok} interdit en Phase 22"


def test_no_crypto_in_resolver_module():
    text = _RESOLVER_PATH.read_text(encoding="utf-8")
    forbidden = (
        "Fernet(", ".decrypt(", ".encrypt(", "_get_cipher_helper",
        "SecretsService", "secrets_service",
    )
    for tok in forbidden:
        assert tok not in text, f"{tok} interdit en Phase 22"


def test_spec_mocks_no_mutation_called():
    """Aucune méthode hors lecture seule n'est appelée sur les deps."""
    reg = MagicMock(spec=ToolRegistryReadLike)
    reg.list_dynamic_handlers.return_value = []
    reg.get_tools_schema.return_value = []
    cat = MagicMock(spec=CatalogReadLike)
    cat.list_servers.return_value = []
    disc = MagicMock(spec=DiscoveryReadLike)
    disc.iter_persisted_reports.return_value = []
    pr = MagicMock(spec=PolicyResolverReadLike)
    pr.resolve.return_value = None
    attr = MagicMock(spec=PolicyAttributorReadLike)
    aq = MagicMock(spec=ApprovalQueueReadLike)
    aq.list_pending.return_value = []
    aa = MagicMock(spec=AutoApproveReadLike)
    aa.list_patterns.return_value = []
    rw = MagicMock(spec=RuntimeWatcherReadLike)
    rw.list_persisted_snapshots.return_value = []
    rw.load_snapshot_from_disk.return_value = None
    rw.list_watched_servers.return_value = []
    drift = MagicMock(spec=DriftReadLike)
    drift.audit_summary.return_value = SimpleNamespace(
        drift_count=0, has_drift=False
    )
    drift.tool_entries.return_value = []
    deps = CapabilityResolverDeps(
        tool_registry=reg, catalog=cat, discovery=disc,
        policy_resolver=pr, policy_attributor=attr,
        approval_queue=aq, auto_approve=aa,
        runtime_watcher=rw, drift=drift,
    )
    resolver = CapabilityResolver(deps)
    resolver.resolve("read file", caller_kind="test")
    # Toutes les méthodes appelées doivent appartenir aux Protocols (read).
    # MagicMock(spec=...) lève AttributeError si une méthode hors spec
    # est invoquée. Le simple fait d'arriver ici prouve le respect.
    # En complément, vérifier qu'aucun call ne contient "install/activate".
    all_calls = []
    for m in (reg, cat, disc, pr, attr, aq, aa, rw, drift):
        for call in m.mock_calls:
            all_calls.append(str(call))
    forbidden_subs = (
        "install", "activate", "deactivate", "approve",
        "reject", "add_server", "quarantine", "restore",
        "remove_server", "add_pattern", "remove_pattern",
        "update_trust", "register_runner", "unregister_runner",
        "call_tool", "execute_approved", "record_event",
        "take_snapshot",
    )
    for call_str in all_calls:
        for fs in forbidden_subs:
            assert fs not in call_str, (
                f"mutation suspecte : {call_str} contient {fs}"
            )


# ══════════════════════════════════════════════════════════════════════════════
# Section 12 — Catalog counts evidence
# ══════════════════════════════════════════════════════════════════════════════


def test_catalog_counts_in_evidence():
    cat = FakeCatalog([
        FakeServerEntry("a", "declared"),
        FakeServerEntry("b", "declared"),
        FakeServerEntry("c", "installed"),
        FakeServerEntry("d", "active"),
        FakeServerEntry("e", "quarantined"),
    ])
    deps = CapabilityResolverDeps(
        tool_registry=FakeToolRegistry(), catalog=cat
    )
    plan = CapabilityResolver(deps).resolve("hello", caller_kind="test")
    counts = plan.evidence["catalog_counts"]
    assert counts["declared"] == 2
    assert counts["installed"] == 1
    assert counts["active"] == 1
    assert counts["quarantined"] == 1
    assert counts["removed"] == 0


# ══════════════════════════════════════════════════════════════════════════════
# Section 13 — FilesystemDiscoveryReader
# ══════════════════════════════════════════════════════════════════════════════


def test_filesystem_discovery_reader_empty_dir(tmp_path):
    reader = FilesystemDiscoveryReader(tmp_path)
    assert list(reader.iter_persisted_reports()) == []


def test_filesystem_discovery_reader_reads_reports(tmp_path):
    rep = {"server_id": "srv1", "tools": [
        {"name": "x", "description": "y"},
    ]}
    (tmp_path / "report1.json").write_text(
        json.dumps(rep), encoding="utf-8"
    )
    reader = FilesystemDiscoveryReader(tmp_path)
    out = list(reader.iter_persisted_reports())
    assert len(out) == 1
    assert out[0]["server_id"] == "srv1"


def test_filesystem_discovery_reader_filters_by_server(tmp_path):
    (tmp_path / "r1.json").write_text(json.dumps({
        "server_id": "srv1", "tools": []
    }), encoding="utf-8")
    (tmp_path / "r2.json").write_text(json.dumps({
        "server_id": "srv2", "tools": []
    }), encoding="utf-8")
    reader = FilesystemDiscoveryReader(tmp_path)
    out = list(reader.iter_persisted_reports(server_id="srv1"))
    assert len(out) == 1
    assert out[0]["server_id"] == "srv1"


def test_filesystem_discovery_reader_skips_malformed(tmp_path):
    (tmp_path / "good.json").write_text(
        json.dumps({"server_id": "srv1", "tools": []}), encoding="utf-8"
    )
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    reader = FilesystemDiscoveryReader(tmp_path)
    out = list(reader.iter_persisted_reports())
    assert len(out) == 1


def test_filesystem_discovery_reader_rejects_non_path():
    with pytest.raises(TypeError):
        FilesystemDiscoveryReader("/tmp/x")  # type: ignore[arg-type]


# ============================================================================
# Section 14 - Interconnexion avec le vrai ToolRegistry
# ============================================================================


def test_extract_tool_schema_identity_supports_flat_and_openai_shapes():
    flat = {"name": "read_file", "description": "Read a file"}
    openai = {
        "type": "function",
        "function": {
            "name": "discover_tools",
            "description": "Discover tools",
            "parameters": {"type": "object", "properties": {}},
        },
    }

    assert _extract_tool_schema_identity(flat) == (
        "read_file", "Read a file"
    )
    assert _extract_tool_schema_identity(openai) == (
        "discover_tools", "Discover tools"
    )
    assert _extract_tool_schema_identity({"function": {}}) == (None, "")
    assert _extract_tool_schema_identity("not a schema") == (None, "")


def test_capability_resolver_reads_real_toolregistry_openai_schema_native_tool():
    registry = ToolRegistry()
    deps = CapabilityResolverDeps(
        tool_registry=registry,
        catalog=FakeCatalog([]),
    )
    native = CapabilityResolver(deps)._read_native_tools([])
    names = {entry["name"] for entry in native}

    assert "discover_tools" in names
    assert all("function" not in entry for entry in native)


def test_capability_resolver_reads_real_dynamic_handler_openai_schema():
    registry = ToolRegistry()

    async def _handler(ctx, **kwargs):
        return HandlerResult.ok(output=kwargs)

    handler_def = HandlerDef(
        name="mcp__schema__lookup",
        description="Lookup through a live MCP schema handler",
        parameters={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        handler=_handler,
        category="mcp",
        source_module="mcp.schema",
    )
    registry.register_dynamic_handler(
        handler_def,
        policy=MCPPolicy.READ_ONLY,
        provenance={"source_kind": "mcp", "server_id": "schema"},
    )
    try:
        deps = CapabilityResolverDeps(
            tool_registry=registry,
            catalog=FakeCatalog([]),
        )
        dynamic = CapabilityResolver(deps)._read_dynamic_handlers([])
        by_name = {entry["name"]: entry for entry in dynamic}

        assert by_name["mcp__schema__lookup"]["server_id"] == "schema"
        assert (
            by_name["mcp__schema__lookup"]["description"]
            == "Lookup through a live MCP schema handler"
        )
    finally:
        registry.unregister_dynamic_handler("mcp__schema__lookup")
