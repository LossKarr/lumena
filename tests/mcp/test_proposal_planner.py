"""
Tests Phase 23 — MCPProposalPlanner.

Couvre : structure, sources offline, sources réseau gated, scoring,
cascade, creation planner, catalog proposal (Phase 14 compat),
anti-leak, anti-mutation, sources optionnelles, UTF-8,
audit local optionnel, cohérence Phase 22 ↔ Phase 23.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from src.mcp.proposal_planner import (
    CatalogLookupLike,
    CatalogProposal,
    CuratedOfflineCatalogSource,
    LocalFilesystemSource,
    MCPCreationProposal,
    MCPProposalDecision,
    MCPProposalPlan,
    MCPProposalPlanBlocker,
    MCPProposalPlanner,
    MCPProposalPlannerDeps,
    MCPSearchResult,
    MCPSearchSourceLike,
    StubNetworkSource,
    ToolTemplateProposal,
    _BLOCKER_CODES,
    _EVIDENCE_WHITELIST,
    _build_creation_proposal,
    _compute_pre_score,
    _is_valid_package_spec_phase14,
    _phase23_is_actionable_intent,
    _tokenize,
)


# Phase 22 helper (test de cohérence uniquement — JAMAIS dans le code prod
# de Phase 23).
from src.mcp.capability_resolver import (
    _is_actionable_intent as _phase22_is_actionable_intent,
    _tokenize as _phase22_tokenize,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers fakes
# ──────────────────────────────────────────────────────────────────────────────


class FakeStaticSource:
    """Source offline qui retourne une liste fixe de dicts bruts."""

    def __init__(
        self,
        name: str,
        entries: List[Dict[str, Any]],
        is_network: bool = False,
    ) -> None:
        self._name = name
        self._entries = entries
        self._is_network = is_network

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_network(self) -> bool:
        return self._is_network

    def search(self, query_tokens: set[str], *, limit: int):
        return list(self._entries[:limit])


class FakeNetworkSource(FakeStaticSource):
    """Source réseau qui expose `network_enabled`."""

    def __init__(
        self, name: str, entries: List[Dict[str, Any]],
        *, network_enabled: bool,
    ) -> None:
        super().__init__(name, entries, is_network=True)
        self._network_enabled = network_enabled

    @property
    def network_enabled(self) -> bool:
        return self._network_enabled

    def search(self, query_tokens: set[str], *, limit: int):
        if not self._network_enabled:
            return []
        return super().search(query_tokens, limit=limit)


class FakeCatalogLookup:
    def __init__(
        self,
        by_spec: Optional[Dict[str, str]] = None,
        by_id: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._by_spec = by_spec or {}
        self._by_id = by_id or {}

    def find_by_package_spec(self, package_spec: str) -> Optional[str]:
        return self._by_spec.get(package_spec)

    def find_by_server_id(self, server_id: str) -> Optional[Any]:
        return self._by_id.get(server_id)


def _well_formed_entry(
    name: str = "search_brave",
    spec: str = "npm:@modelcontextprotocol/server-brave",
    transport: str = "npm",
    mth: str = "stdio",
    description: str = "Brave search MCP",
    tools: Optional[List[str]] = None,
    downloads: int = 60_000,
    has_repo: bool = True,
    has_license: bool = True,
    last_pub: str = "2026-05-01T00:00:00+00:00",
    license_id: str = "MIT",
    version: str = "1.0.0",
) -> Dict[str, Any]:
    return {
        "package_name": name,
        "package_spec": spec,
        "version": version,
        "package_transport": transport,
        "mcp_transport_hint": mth,
        "description": description,
        "tools_hint": tools or ["brave_search", "brave_news"],
        "downloads_count": downloads,
        "last_publish_date": last_pub,
        "has_repo": has_repo,
        "has_license": has_license,
        "license_id": license_id,
    }


def _empty_deps() -> MCPProposalPlannerDeps:
    return MCPProposalPlannerDeps()


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Structure
# ══════════════════════════════════════════════════════════════════════════════


def test_decision_has_exactly_five_values():
    expected = {
        "USE_EXISTING_CANDIDATE", "PROPOSE_CATALOG_DECLARED",
        "PROPOSE_LOCAL_CREATE", "NO_SAFE_CANDIDATE", "NEEDS_APPROVAL",
    }
    assert {d.name for d in MCPProposalDecision} == expected


def test_search_result_frozen():
    sr = MCPSearchResult(
        source="x", package_name="y", package_spec="npm:y",
        version="", package_transport="npm",
        mcp_transport_hint="stdio", description_hash="h",
        tools_hint=(), trust_pre_score=50, license_id=None,
    )
    with pytest.raises(Exception):
        sr.trust_pre_score = 99  # type: ignore[misc]


def test_catalog_proposal_frozen():
    cp = CatalogProposal(
        proposed_server_id="x", proposed_display_name="y",
        proposed_package_spec="npm:y", proposed_version="",
        proposed_package_transport="npm",
        proposed_mcp_transport_hint="stdio",
        proposed_trust_score_set=50, rationale_code="existing_search_match",
        requires_approval=False, target_status_on_add="declared",
    )
    with pytest.raises(Exception):
        cp.requires_approval = True  # type: ignore[misc]


def test_planner_rejects_non_deps():
    with pytest.raises(TypeError):
        MCPProposalPlanner(deps="x")  # type: ignore[arg-type]


def test_planner_rejects_non_path_audit():
    with pytest.raises(TypeError):
        MCPProposalPlanner(_empty_deps(), audit_log_path="/x")  # type: ignore[arg-type]


def test_proposal_id_is_uuid4_hex_32():
    plan = MCPProposalPlanner(_empty_deps()).plan_proposal(
        "hello", caller_kind="test"
    )
    assert re.match(r"^[0-9a-f]{32}$", plan.proposal_id)


def test_intent_truncated_to_256():
    huge = "send email " + ("x" * 500)
    plan = MCPProposalPlanner(_empty_deps()).plan_proposal(
        huge, caller_kind="test"
    )
    assert len(plan.intent_query_sanitized) <= 256


def test_intent_preserves_accents():
    plan = MCPProposalPlanner(_empty_deps()).plan_proposal(
        "envoyer un email à éàçôê", caller_kind="test"
    )
    assert "éàçôê" in plan.intent_query_sanitized


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Package spec Phase 14 validation
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("spec,expected", [
    ("npm:foo", True),
    ("npm:foo-bar", True),
    ("npm:@scope/foo", True),
    ("npm:@scope/foo-bar", True),
    ("pypi:foo", True),
    ("pypi:foo-bar", True),
    ("local:slug", True),
    ("local:slug-1", True),
    # Refusés
    ("npm:foo@1.2.3", False),
    ("npm:@scope/foo@1.0.0", False),
    ("pypi:foo/bar", False),
    ("local:foo/bar", False),
    ("github:owner/repo", False),
    ("file:/path", False),
    ("", False),
    (None, False),
    (123, False),
    ("pypi:foo@1.0", False),
])
def test_package_spec_phase14_validation(spec, expected):
    assert _is_valid_package_spec_phase14(spec) is expected


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Curated offline catalog source
# ══════════════════════════════════════════════════════════════════════════════


def test_curated_source_rejects_non_path():
    with pytest.raises(TypeError):
        CuratedOfflineCatalogSource("/tmp/x")  # type: ignore[arg-type]


def test_curated_source_missing_file_returns_empty(tmp_path):
    src = CuratedOfflineCatalogSource(tmp_path / "missing.json")
    assert src.search({"x"}, limit=10) == []


def test_curated_source_empty_list_returns_empty(tmp_path):
    p = tmp_path / "cat.json"
    p.write_text("[]", encoding="utf-8")
    src = CuratedOfflineCatalogSource(p)
    assert src.search({"x"}, limit=10) == []


def test_curated_source_malformed_returns_empty(tmp_path):
    p = tmp_path / "cat.json"
    p.write_text("{not json", encoding="utf-8")
    src = CuratedOfflineCatalogSource(p)
    assert src.search({"x"}, limit=10) == []


def test_curated_source_normal_entries(tmp_path):
    p = tmp_path / "cat.json"
    p.write_text(
        json.dumps([_well_formed_entry()]), encoding="utf-8"
    )
    src = CuratedOfflineCatalogSource(p)
    out = src.search({"brave", "search"}, limit=10)
    assert len(out) == 1
    assert out[0]["source"] == "curated"


def test_curated_source_drops_invalid_spec(tmp_path):
    p = tmp_path / "cat.json"
    bad = _well_formed_entry(spec="npm:foo@1.2.3")
    p.write_text(json.dumps([bad]), encoding="utf-8")
    src = CuratedOfflineCatalogSource(p)
    assert src.search({"brave"}, limit=10) == []


def test_curated_source_drops_github_spec(tmp_path):
    p = tmp_path / "cat.json"
    bad = _well_formed_entry(spec="github:owner/repo", transport="local")
    p.write_text(json.dumps([bad]), encoding="utf-8")
    src = CuratedOfflineCatalogSource(p)
    assert src.search({"brave"}, limit=10) == []


def test_curated_source_drops_invalid_transport(tmp_path):
    p = tmp_path / "cat.json"
    bad = _well_formed_entry(transport="binary")
    p.write_text(json.dumps([bad]), encoding="utf-8")
    src = CuratedOfflineCatalogSource(p)
    assert src.search({"brave"}, limit=10) == []


def test_curated_source_token_filter(tmp_path):
    p = tmp_path / "cat.json"
    entries = [
        _well_formed_entry(
            name="search_brave", spec="npm:brave",
            tools=["brave_search"],
        ),
        _well_formed_entry(
            name="weather_pkg", spec="npm:weather",
            tools=["get_weather"],
        ),
    ]
    p.write_text(json.dumps(entries), encoding="utf-8")
    src = CuratedOfflineCatalogSource(p)
    out = src.search({"weather"}, limit=10)
    assert len(out) == 1
    assert out[0]["package_name"] == "weather_pkg"


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Local filesystem source
# ══════════════════════════════════════════════════════════════════════════════


def test_local_fs_source_missing_returns_empty(tmp_path):
    src = LocalFilesystemSource(tmp_path / "missing")
    assert src.search({"x"}, limit=10) == []


def test_local_fs_source_finds_mcp_json(tmp_path):
    pkg_dir = tmp_path / "my_local_mcp"
    pkg_dir.mkdir()
    (pkg_dir / "mcp.json").write_text(json.dumps({
        "name": "my_local_mcp",
        "version": "0.1.0",
        "description": "local mcp",
        "tools_hint": ["weather_local"],
    }), encoding="utf-8")
    src = LocalFilesystemSource(tmp_path)
    out = src.search({"weather"}, limit=10)
    assert len(out) == 1
    assert out[0]["package_spec"] == "local:my_local_mcp"
    assert out[0]["package_transport"] == "local"


def test_local_fs_ignores_node_modules(tmp_path):
    nm = tmp_path / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "package.json").write_text(json.dumps({
        "name": "weatherpkg",
    }), encoding="utf-8")
    src = LocalFilesystemSource(tmp_path)
    assert src.search({"weather"}, limit=10) == []


def test_local_fs_skips_malformed(tmp_path):
    d = tmp_path / "pkg"
    d.mkdir()
    (d / "mcp.json").write_text("{not json", encoding="utf-8")
    src = LocalFilesystemSource(tmp_path)
    assert src.search({"x"}, limit=10) == []


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — StubNetworkSource
# ══════════════════════════════════════════════════════════════════════════════


def test_stub_network_source_is_network_true():
    s = StubNetworkSource("npm", network_enabled=False)
    assert s.is_network is True


def test_stub_network_disabled_returns_empty():
    s = StubNetworkSource("npm", network_enabled=False)
    assert s.search({"x"}, limit=10) == []


def test_stub_network_enabled_still_returns_empty_v1():
    s = StubNetworkSource("npm", network_enabled=True)
    # Phase 23 v1 : aucune vraie source réseau, stub retourne toujours [].
    assert s.search({"x"}, limit=10) == []


def test_stub_network_rejects_bad_name():
    with pytest.raises(ValueError):
        StubNetworkSource("", network_enabled=False)


def test_stub_network_rejects_non_bool_flag():
    with pytest.raises(TypeError):
        StubNetworkSource("npm", network_enabled="yes")  # type: ignore[arg-type]


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — Scoring _compute_pre_score
# ══════════════════════════════════════════════════════════════════════════════


def test_scoring_empty_metadata_is_zero():
    assert _compute_pre_score({}) == 0


def test_scoring_curated_alone():
    assert _compute_pre_score({"source": "curated"}) == 20


def test_scoring_downloads_alone_capped_at_15():
    # 50k saturated downloads → 15 pts
    score = _compute_pre_score({"downloads_count": 200_000})
    assert score == 15


def test_scoring_all_max():
    from datetime import datetime, timezone
    score = _compute_pre_score({
        "source": "curated",
        "downloads_count": 200_000,
        "has_repo": True,
        "has_license": True,
        "last_publish_date": datetime.now(timezone.utc).isoformat(),
        "package_transport": "npm",
        "mcp_transport_hint": "stdio",
    })
    assert score == 100


def test_scoring_deterministic():
    meta = {"source": "curated", "has_repo": True}
    assert _compute_pre_score(meta) == _compute_pre_score(meta)


def test_scoring_caps_at_100():
    score = _compute_pre_score({
        "source": "curated",
        "downloads_count": 999_999_999,
        "has_repo": True,
        "has_license": True,
        "package_transport": "npm",
        "mcp_transport_hint": "sse",
    })
    assert score <= 100


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — Cascade décisionnelle
# ══════════════════════════════════════════════════════════════════════════════


def test_no_safe_candidate_when_all_deps_empty():
    plan = MCPProposalPlanner(_empty_deps()).plan_proposal(
        "comment vas-tu", caller_kind="test"
    )
    assert plan.decision == MCPProposalDecision.NO_SAFE_CANDIDATE


def test_propose_catalog_declared_with_good_curated_match(tmp_path):
    cat_path = tmp_path / "cat.json"
    cat_path.write_text(json.dumps([
        _well_formed_entry(name="brave_search_pkg", spec="npm:brave"),
    ]), encoding="utf-8")
    src = CuratedOfflineCatalogSource(cat_path)
    deps = MCPProposalPlannerDeps(sources=(src,))
    plan = MCPProposalPlanner(deps).plan_proposal(
        "search brave", caller_kind="test"
    )
    assert plan.decision == MCPProposalDecision.PROPOSE_CATALOG_DECLARED
    assert plan.catalog_proposal is not None
    assert plan.catalog_proposal.requires_approval is False


def test_use_existing_candidate_when_catalog_race(tmp_path):
    # Phase I-7 : intent volontairement non-curated (sinon court-circuit KNOWN_MCPS
    # bypasse les sources mock et invalide ce test de race condition catalog).
    cat_path = tmp_path / "cat.json"
    cat_path.write_text(json.dumps([
        _well_formed_entry(name="search_xyz_tool", spec="npm:xyz", tools=["xyz"]),
    ]), encoding="utf-8")
    src = CuratedOfflineCatalogSource(cat_path)
    cl = FakeCatalogLookup(by_spec={"npm:xyz": "existing_sid"})
    deps = MCPProposalPlannerDeps(sources=(src,), catalog_lookup=cl)
    plan = MCPProposalPlanner(deps).plan_proposal(
        "xyz uncurated", caller_kind="test"
    )
    assert plan.decision == MCPProposalDecision.USE_EXISTING_CANDIDATE
    assert plan.catalog_proposal is None


def test_quarantined_match_is_skipped(tmp_path):
    cat_path = tmp_path / "cat.json"
    cat_path.write_text(json.dumps([
        _well_formed_entry(spec="npm:brave"),
        _well_formed_entry(
            name="other_search", spec="npm:other", tools=["search"],
        ),
    ]), encoding="utf-8")
    src = CuratedOfflineCatalogSource(cat_path)
    quarantined_entry = SimpleNamespace(
        status=SimpleNamespace(value="quarantined")
    )
    cl = FakeCatalogLookup(
        by_spec={"npm:brave": "existing_sid"},
        by_id={"existing_sid": quarantined_entry},
    )
    deps = MCPProposalPlannerDeps(sources=(src,), catalog_lookup=cl)
    plan = MCPProposalPlanner(deps).plan_proposal(
        "search", caller_kind="test"
    )
    # Le premier candidat quarantined est skippé → on tombe sur le second
    # ou sur PROPOSE_CATALOG_DECLARED selon scoring.
    assert plan.decision in (
        MCPProposalDecision.PROPOSE_CATALOG_DECLARED,
        MCPProposalDecision.USE_EXISTING_CANDIDATE,
        MCPProposalDecision.NO_SAFE_CANDIDATE,
    )


def test_propose_local_create_when_actionable_no_match():
    deps = _empty_deps()
    plan = MCPProposalPlanner(deps).plan_proposal(
        "send an email to bob", caller_kind="test"
    )
    assert plan.decision == MCPProposalDecision.PROPOSE_LOCAL_CREATE
    assert plan.creation_proposal is not None
    assert plan.creation_proposal.complexity_estimate in ("low", "medium")
    # Doctrine v2 §F2.2 : PROPOSE_LOCAL_CREATE ⇒ catalog_proposal=None
    assert plan.catalog_proposal is None


def test_no_safe_candidate_when_not_actionable():
    deps = _empty_deps()
    plan = MCPProposalPlanner(deps).plan_proposal(
        "comment vas-tu aujourdhui", caller_kind="test"
    )
    assert plan.decision == MCPProposalDecision.NO_SAFE_CANDIDATE
    assert plan.catalog_proposal is None
    assert plan.creation_proposal is None


def test_needs_approval_when_network_source_used(tmp_path):
    # Phase I-8 (Fix AI) : le candidat doit partager au moins un token
    # discriminant avec l'intent (pertinence), sinon il est écarté.
    entry = _well_formed_entry(
        name="remote-thing",
        spec="npm:remote-thing",
        description="Remote thing MCP server",
    )
    net_src = FakeNetworkSource("npm", [entry], network_enabled=True)
    deps = MCPProposalPlannerDeps(sources=(net_src,))
    plan = MCPProposalPlanner(deps).plan_proposal(
        "search remote thing tool", caller_kind="test"
    )
    assert plan.decision == MCPProposalDecision.NEEDS_APPROVAL
    # NEEDS_APPROVAL garde catalog_proposal présent (doctrine v2 §G2)
    assert plan.catalog_proposal is not None
    assert plan.catalog_proposal.requires_approval is True
    assert any(
        b.blocker_code == "network_source_requires_approval"
        for b in plan.blockers
    )


def test_no_safe_candidate_when_security_sensitive():
    deps = _empty_deps()
    plan = MCPProposalPlanner(deps).plan_proposal(
        "send payment with credit card to merchant",
        caller_kind="test",
    )
    assert plan.decision == MCPProposalDecision.NO_SAFE_CANDIDATE
    assert plan.creation_proposal is not None
    assert plan.creation_proposal.rationale_code == "security_sensitive"
    assert any(
        b.blocker_code == "creation_security_sensitive"
        for b in plan.blockers
    )


def test_all_candidates_below_threshold_blocker():
    # source qui retourne un candidat valide mais avec score < 40
    entry = _well_formed_entry(
        downloads=0, has_repo=False, has_license=False,
        last_pub="2000-01-01T00:00:00+00:00",
        mth="unknown",
    )
    src = FakeStaticSource("local_fs", [entry])
    deps = MCPProposalPlannerDeps(sources=(src,))
    plan = MCPProposalPlanner(deps).plan_proposal(
        "send email", caller_kind="test"
    )
    assert any(
        b.blocker_code == "all_candidates_below_threshold"
        for b in plan.blockers
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — Catalog proposal (Phase 14 compat)
# ══════════════════════════════════════════════════════════════════════════════


def test_catalog_proposal_spec_has_no_at_version(tmp_path):
    p = tmp_path / "cat.json"
    p.write_text(json.dumps([_well_formed_entry()]), encoding="utf-8")
    src = CuratedOfflineCatalogSource(p)
    deps = MCPProposalPlannerDeps(sources=(src,))
    plan = MCPProposalPlanner(deps).plan_proposal(
        "search brave", caller_kind="test"
    )
    assert plan.catalog_proposal is not None
    spec = plan.catalog_proposal.proposed_package_spec
    after_colon = spec.split(":", 1)[-1]
    if not spec.startswith("npm:@"):
        assert "@" not in after_colon


def test_catalog_proposal_version_separated(tmp_path):
    # Phase I-7 : intent non-curated pour ne pas court-circuiter vers KNOWN_MCPS
    # (qui forcerait version="latest").
    p = tmp_path / "cat.json"
    p.write_text(json.dumps([_well_formed_entry(
        name="search_xyz_tool", spec="npm:xyz", tools=["xyz"], version="2.5.1"
    )]), encoding="utf-8")
    src = CuratedOfflineCatalogSource(p)
    deps = MCPProposalPlannerDeps(sources=(src,))
    plan = MCPProposalPlanner(deps).plan_proposal(
        "xyz uncurated", caller_kind="test"
    )
    assert plan.catalog_proposal.proposed_version == "2.5.1"


def test_catalog_proposal_package_transport_phase18(tmp_path):
    p = tmp_path / "cat.json"
    p.write_text(json.dumps([_well_formed_entry()]), encoding="utf-8")
    src = CuratedOfflineCatalogSource(p)
    deps = MCPProposalPlannerDeps(sources=(src,))
    plan = MCPProposalPlanner(deps).plan_proposal(
        "search brave", caller_kind="test"
    )
    assert plan.catalog_proposal.proposed_package_transport in (
        "npm", "pypi", "local"
    )


def test_catalog_proposal_target_status_declared(tmp_path):
    p = tmp_path / "cat.json"
    p.write_text(json.dumps([_well_formed_entry()]), encoding="utf-8")
    src = CuratedOfflineCatalogSource(p)
    deps = MCPProposalPlannerDeps(sources=(src,))
    plan = MCPProposalPlanner(deps).plan_proposal(
        "search brave", caller_kind="test"
    )
    assert plan.catalog_proposal.target_status_on_add == "declared"


def test_offline_catalog_proposal_requires_approval_false(tmp_path):
    p = tmp_path / "cat.json"
    p.write_text(json.dumps([_well_formed_entry()]), encoding="utf-8")
    src = CuratedOfflineCatalogSource(p)
    deps = MCPProposalPlannerDeps(sources=(src,))
    plan = MCPProposalPlanner(deps).plan_proposal(
        "search brave", caller_kind="test"
    )
    assert plan.catalog_proposal.requires_approval is False


# ══════════════════════════════════════════════════════════════════════════════
# Section 9 — Creation planner
# ══════════════════════════════════════════════════════════════════════════════


def test_creation_security_sensitive_refuse():
    tokens = _tokenize("send payment with credit card")
    cp = _build_creation_proposal(tokens)
    assert cp.complexity_estimate == "refuse"
    assert cp.rationale_code == "security_sensitive"


def test_creation_intent_too_vague_refuse():
    cp = _build_creation_proposal(_tokenize("hello"))
    assert cp.complexity_estimate == "refuse"
    assert cp.rationale_code == "intent_too_vague"


def test_creation_matched_templates_low():
    cp = _build_creation_proposal(_tokenize("send email"))
    assert cp.complexity_estimate in ("low", "medium")
    assert cp.rationale_code == "matched_templates"
    assert any(t.tool_name == "send_email" for t in cp.suggested_tools)


def test_creation_template_description_present():
    cp = _build_creation_proposal(_tokenize("send email"))
    for tpl in cp.suggested_tools:
        assert tpl.description  # description whitelist autorisée
        assert len(tpl.description) <= 200


def test_creation_input_schema_only_hash():
    cp = _build_creation_proposal(_tokenize("send email"))
    for tpl in cp.suggested_tools:
        assert re.match(r"^[0-9a-f]{12}$", tpl.input_schema_hash)


def test_creation_max_5_tools():
    # Construire un intent qui matche beaucoup
    cp = _build_creation_proposal(_tokenize(
        "send email weather github slack notion calendar database api "
        "translate summarize scrape webhook spotify file"
    ))
    assert len(cp.suggested_tools) <= 5


def test_creation_server_id_deterministic():
    t = _tokenize("send email")
    a = _build_creation_proposal(t)
    b = _build_creation_proposal(t)
    assert a.suggested_server_id == b.suggested_server_id
    assert a.suggested_server_id.startswith("local_")


# ══════════════════════════════════════════════════════════════════════════════
# Section 10 — Anti-leak
# ══════════════════════════════════════════════════════════════════════════════


def _serialize_plan(plan: MCPProposalPlan) -> str:
    data = {
        "proposal_id": plan.proposal_id,
        "intent_query_sanitized": plan.intent_query_sanitized,
        "decision": plan.decision.value,
        "search_results": [sr.__dict__ for sr in plan.search_results],
        "creation_proposal": (
            {
                **plan.creation_proposal.__dict__,
                "suggested_tools": [
                    t.__dict__ for t in plan.creation_proposal.suggested_tools
                ],
            } if plan.creation_proposal else None
        ),
        "catalog_proposal": (
            plan.catalog_proposal.__dict__
            if plan.catalog_proposal else None
        ),
        "blockers": [b.__dict__ for b in plan.blockers],
        "evidence": plan.evidence,
    }
    return json.dumps(data, ensure_ascii=False, default=str)


def test_no_description_raw_leak(tmp_path):
    p = tmp_path / "cat.json"
    p.write_text(json.dumps([_well_formed_entry(
        description="SECRET_DESCRIPTION_LEAK"
    )]), encoding="utf-8")
    src = CuratedOfflineCatalogSource(p)
    deps = MCPProposalPlannerDeps(sources=(src,))
    plan = MCPProposalPlanner(deps).plan_proposal(
        "search brave", caller_kind="test"
    )
    blob = _serialize_plan(plan)
    assert "SECRET_DESCRIPTION_LEAK" not in blob


def test_no_input_schema_raw_leak():
    cp = _build_creation_proposal(_tokenize("send email"))
    blob = json.dumps([t.__dict__ for t in cp.suggested_tools],
                      ensure_ascii=False)
    # Input schema raw a "properties", "required" — vérifions absence
    assert '"properties"' not in blob
    assert '"required"' not in blob


def test_no_intent_raw_in_evidence():
    huge = "SECRET_INTENT_LEAK_" + "x" * 500
    plan = MCPProposalPlanner(_empty_deps()).plan_proposal(
        huge, caller_kind="test"
    )
    sanitized = plan.intent_query_sanitized
    blob = _serialize_plan(plan).replace(sanitized, "<SANITIZED>")
    assert "SECRET_INTENT_LEAK" not in blob


def test_evidence_only_whitelist_keys():
    plan = MCPProposalPlanner(_empty_deps()).plan_proposal(
        "send email", caller_kind="test"
    )
    for k in plan.evidence.keys():
        assert k in _EVIDENCE_WHITELIST


def test_blocker_codes_always_whitelist():
    plan = MCPProposalPlanner(_empty_deps()).plan_proposal(
        "send payment credit card", caller_kind="test"
    )
    for b in plan.blockers:
        assert b.blocker_code in _BLOCKER_CODES


def test_no_url_leak_in_evidence(tmp_path):
    # Inject a URL-like description
    p = tmp_path / "cat.json"
    p.write_text(json.dumps([_well_formed_entry(
        description="https://SECRET_URL_LEAK.example/path"
    )]), encoding="utf-8")
    src = CuratedOfflineCatalogSource(p)
    deps = MCPProposalPlannerDeps(sources=(src,))
    plan = MCPProposalPlanner(deps).plan_proposal(
        "search brave", caller_kind="test"
    )
    blob = _serialize_plan(plan)
    assert "SECRET_URL_LEAK" not in blob


# ══════════════════════════════════════════════════════════════════════════════
# Section 11 — Anti-mutation / anti-subprocess (grep statique)
# ══════════════════════════════════════════════════════════════════════════════


_MODULE_PATH = (
    Path(__file__).resolve().parent.parent.parent
    / "src" / "mcp" / "proposal_planner.py"
)


_FORBIDDEN_MUTATION_TOKENS = (
    ".install(", ".activate(", ".deactivate(",
    ".approve(", ".reject(",
    ".add_server(", ".quarantine(", ".restore(", ".remove_server(",
    ".add_pattern(", ".remove_pattern(",
    ".update_trust_score(",
    ".register_runner(", ".unregister_runner(",
    ".register_dynamic_handler(",
    ".update_last_active(",
    "call_tool(",
    "execute_approved_install(",
    "start_runner(", "stop_runner(",
    ".add_pending(",
    "subprocess.", "Popen(",
    "os.system(", "os.exec",
)


@pytest.mark.parametrize("token", _FORBIDDEN_MUTATION_TOKENS)
def test_no_mutation_or_subprocess_token(token):
    text = _MODULE_PATH.read_text(encoding="utf-8")
    assert token not in text, f"{token} interdit en Phase 23"


_FORBIDDEN_IMPORTS = (
    "from src.mcp.install_orchestrator",
    "from src.mcp.activation_service",
    "from src.mcp.client_factory",
    "from src.mcp.sandbox_runner",
    "import src.mcp.install_orchestrator",
    "import src.mcp.activation_service",
    "import src.mcp.client_factory",
    "import src.mcp.sandbox_runner",
    "import requests",
    "import httpx",
    "import urllib3",
    "import aiohttp",
    "from urllib.request",
    "from urllib import request",
)


@pytest.mark.parametrize("imp", _FORBIDDEN_IMPORTS)
def test_no_forbidden_imports(imp):
    text = _MODULE_PATH.read_text(encoding="utf-8")
    assert imp not in text, f"import interdit en Phase 23 : {imp}"


def test_no_singleton_or_cache():
    text = _MODULE_PATH.read_text(encoding="utf-8")
    forbidden = (
        "_INSTANCE", "_SINGLETON", "lru_cache", "functools.cache",
        "@cache", "global _",
    )
    for tok in forbidden:
        assert tok not in text, f"{tok} interdit"


def test_no_http_route():
    text = _MODULE_PATH.read_text(encoding="utf-8")
    forbidden = (
        "@router", "APIRouter", "FastAPI", "@app.",
        "fastapi", "from fastapi",
    )
    for tok in forbidden:
        assert tok not in text, f"{tok} interdit"


def test_no_crypto():
    text = _MODULE_PATH.read_text(encoding="utf-8")
    forbidden = (
        "Fernet(", ".decrypt(", "_get_cipher_helper",
        "SecretsService", "secrets_service",
    )
    for tok in forbidden:
        assert tok not in text, f"{tok} interdit"


def test_spec_mocks_no_mutation_called():
    src = MagicMock(spec=MCPSearchSourceLike)
    src.name = "stub"
    src.is_network = False
    src.search.return_value = []
    cl = MagicMock(spec=CatalogLookupLike)
    cl.find_by_package_spec.return_value = None
    cl.find_by_server_id.return_value = None
    deps = MCPProposalPlannerDeps(sources=(src,), catalog_lookup=cl)
    MCPProposalPlanner(deps).plan_proposal("send email", caller_kind="test")
    all_calls = []
    for m in (src, cl):
        for c in m.mock_calls:
            all_calls.append(str(c))
    forbidden_subs = (
        "install", "activate", "approve", "reject",
        "add_server", "quarantine", "restore",
        "remove_server", "add_pattern", "remove_pattern",
        "update_trust", "register", "unregister",
        "call_tool", "execute_approved",
    )
    for call_str in all_calls:
        for fs in forbidden_subs:
            assert fs not in call_str, f"mutation suspecte : {call_str}"


# ══════════════════════════════════════════════════════════════════════════════
# Section 12 — Sources optionnelles / dégradation
# ══════════════════════════════════════════════════════════════════════════════


def test_sources_degraded_when_source_raises():
    class Raising:
        @property
        def name(self): return "boom"
        @property
        def is_network(self): return False
        def search(self, q, *, limit):
            raise RuntimeError("boom")
    deps = MCPProposalPlannerDeps(sources=(Raising(),))
    plan = MCPProposalPlanner(deps).plan_proposal(
        "send email", caller_kind="test"
    )
    assert "boom" in plan.evidence["sources_degraded"]


def test_network_sources_enabled_evidence_false_by_default():
    plan = MCPProposalPlanner(_empty_deps()).plan_proposal(
        "x", caller_kind="test"
    )
    assert plan.evidence["network_sources_enabled"] is False


def test_network_sources_enabled_evidence_true_when_set():
    s = StubNetworkSource("npm", network_enabled=True)
    deps = MCPProposalPlannerDeps(sources=(s,))
    plan = MCPProposalPlanner(deps).plan_proposal(
        "x", caller_kind="test"
    )
    assert plan.evidence["network_sources_enabled"] is True


def test_catalog_lookup_none_does_not_break():
    plan = MCPProposalPlanner(_empty_deps()).plan_proposal(
        "x", caller_kind="test"
    )
    assert plan.evidence["catalog_race_detected"] is False


# ══════════════════════════════════════════════════════════════════════════════
# Section 13 — Cohérence Phase 22 ↔ Phase 23
# ══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("intent", [
    "send email to bob",
    "read file",
    "comment vas-tu",
    "merci",
    "translate text",
    "explique-moi la philosophie",
    "github issue",
    "trouve un MCP pour Airtable",
    "trouver un outil pour Notion",
    "surveiller les paiements Stripe",
    "connecter Airtable",
    "weather forecast",
    "hello world",
    "calculate 2+2",
])
def test_actionable_intent_consistent_between_phases(intent):
    p22 = _phase22_is_actionable_intent(_phase22_tokenize(intent))
    p23 = _phase23_is_actionable_intent(_tokenize(intent))
    assert p22 == p23, (
        f"Divergence sur {intent!r} : Phase22={p22} Phase23={p23}"
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section 14 — Audit local optionnel
# ══════════════════════════════════════════════════════════════════════════════


def test_audit_not_written_when_none(tmp_path):
    MCPProposalPlanner(_empty_deps(), audit_log_path=None).plan_proposal(
        "x", caller_kind="test"
    )
    assert list(tmp_path.iterdir()) == []


def test_audit_written_when_path_provided(tmp_path):
    p = tmp_path / "audit.jsonl"
    MCPProposalPlanner(_empty_deps(), audit_log_path=p).plan_proposal(
        "send email", caller_kind="test", profile="default"
    )
    raw = p.read_text(encoding="utf-8").strip()
    assert raw
    event = json.loads(raw.splitlines()[0])
    assert event["event"] == "proposal_planned"
    assert event["phase"] == "23"
    assert event["caller_kind"] == "test"


def test_audit_no_mojibake(tmp_path):
    p = tmp_path / "audit.jsonl"
    MCPProposalPlanner(_empty_deps(), audit_log_path=p).plan_proposal(
        "envoyer un email à éàçôê", caller_kind="test"
    )
    raw = p.read_text(encoding="utf-8")
    for moji in ("Ã©", "Ã¨", "Ã ", "Ã§", "â€™"):
        assert moji not in raw


def test_audit_no_intent_leak(tmp_path):
    p = tmp_path / "audit.jsonl"
    MCPProposalPlanner(_empty_deps(), audit_log_path=p).plan_proposal(
        "SECRET_INTENT_LEAK_" + "x" * 300, caller_kind="test"
    )
    raw = p.read_text(encoding="utf-8")
    assert "SECRET_INTENT_LEAK" not in raw


def test_audit_parent_dir_created(tmp_path):
    p = tmp_path / "sub" / "nested" / "audit.jsonl"
    MCPProposalPlanner(_empty_deps(), audit_log_path=p).plan_proposal(
        "x", caller_kind="test"
    )
    assert p.exists()


# ══════════════════════════════════════════════════════════════════════════════
# Section 15 — Curated json shippé est vide
# ══════════════════════════════════════════════════════════════════════════════


def test_shipped_curated_catalog_is_empty_list():
    p = (
        Path(__file__).resolve().parent.parent.parent
        / "src" / "mcp" / "data" / "curated_mcp_catalog.json"
    )
    assert p.exists()
    text = p.read_text(encoding="utf-8")
    # Pas de BOM, contenu strict []
    assert not text.startswith("﻿")
    assert json.loads(text) == []
