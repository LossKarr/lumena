"""Phase I-7 — Verrouille la priorité KNOWN_MCPS curated dans Phase 22.

Bug d'origine : `proposal_planner.plan_proposal` cherchait UNIQUEMENT dans
les sources réseau (npm search, pypi search), sans jamais consulter
KNOWN_MCPS. Pour `intent="utiliser Slack"`, npm search retournait
`npm:slack-mcp-server` (un package random non officiel) au lieu du
curated `npm:@modelcontextprotocol/server-slack`. Conséquence :
target_server_id = "proposed_e28f7ba17d" au lieu de "slack" → bypass
auto-approve curated impossible.

Fix : court-circuit prioritaire au début de plan_proposal — si
lookup_known_mcp(intent) matche, on injecte directement un MCPSearchResult
curated avec score max (100) et on SKIP entièrement la search réseau.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.mcp.proposal_planner import (
    MCPProposalDecision,
    MCPProposalPlanner,
    MCPProposalPlannerDeps,
)


# ──────────────────────────────────────────────────────────────────────────────
# Stubs minimum
# ──────────────────────────────────────────────────────────────────────────────


class _NoisyNetworkSource:
    """Simule npm search qui retourne un package non officiel concurrent."""

    name = "npm_search_test"
    is_network = True
    network_enabled = True

    def __init__(self) -> None:
        self.search_called = False

    def search(self, intent_tokens, limit):
        self.search_called = True
        return [{
            "package_name": "slack-mcp-server",
            "package_spec": "npm:slack-mcp-server",
            "version": "0.1.0",
            "package_transport": "npm",
            "mcp_transport_hint": "stdio",
            "description": "Random unofficial Slack MCP server",
            "tools_hint": ["list_channels"],
            "license_id": "MIT",
            "has_repo": True,
            "has_license": True,
            "downloads_count": 50,
            "last_publish_date": "2026-06-01T00:00:00Z",
        }]


class _StubCatalogLookup:
    def find_by_server_id(self, server_id):
        return None

    def find_by_package_spec(self, package_spec):
        return None


def _planner_with_noisy_source() -> tuple[MCPProposalPlanner, _NoisyNetworkSource]:
    src = _NoisyNetworkSource()
    deps = MCPProposalPlannerDeps(
        sources=(src,),
        catalog_lookup=_StubCatalogLookup(),
    )
    return MCPProposalPlanner(deps=deps), src


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────


class TestCuratedPriority:

    def test_slack_intent_returns_curated_server_id(self):
        """intent='utiliser Slack' → server_id='slack' (pas proposed_*)."""
        planner, src = _planner_with_noisy_source()
        plan = planner.plan_proposal("utiliser Slack", caller_kind="react")

        assert plan.catalog_proposal is not None, "catalog_proposal manquante"
        assert plan.catalog_proposal.proposed_server_id == "slack", (
            f"attendu 'slack' (curated), vu {plan.catalog_proposal.proposed_server_id!r}"
        )
        assert plan.catalog_proposal.proposed_package_spec == (
            "npm:@modelcontextprotocol/server-slack"
        ), "package_spec doit être le curated officiel, pas le random npm"

    def test_curated_skips_network_search(self):
        """Quand curated match → on n'appelle PAS source.search (pas de coût réseau)."""
        planner, src = _planner_with_noisy_source()
        planner.plan_proposal("utiliser Slack", caller_kind="react")
        assert src.search_called is False, (
            "Source réseau ne doit PAS être appelée quand curated match"
        )

    def test_non_curated_intent_falls_back_to_network(self):
        """Intent uncurated → network search consultée normalement."""
        planner, src = _planner_with_noisy_source()
        planner.plan_proposal(
            "manage random uncurated capability XYZ", caller_kind="react"
        )
        assert src.search_called is True, (
            "Source réseau doit être consultée si curated ne matche pas"
        )

    def test_github_intent_returns_github_curated_slug(self):
        planner, _ = _planner_with_noisy_source()
        plan = planner.plan_proposal("utiliser GitHub", caller_kind="react")
        assert plan.catalog_proposal is not None
        assert plan.catalog_proposal.proposed_server_id == "github"
        assert plan.catalog_proposal.proposed_package_spec.startswith(
            "npm:@modelcontextprotocol/server-github"
        )

    def test_curated_decision_is_propose_catalog_declared(self):
        """known_mcps_curated n'est pas une source réseau → pas de NEEDS_APPROVAL."""
        planner, _ = _planner_with_noisy_source()
        plan = planner.plan_proposal("utiliser Slack", caller_kind="react")
        assert plan.decision == MCPProposalDecision.PROPOSE_CATALOG_DECLARED, (
            f"attendu PROPOSE_CATALOG_DECLARED (auto-approvable), "
            f"vu {plan.decision}"
        )
        assert plan.catalog_proposal.requires_approval is False, (
            "Curated officiel ne doit pas exiger d'approbation network"
        )
