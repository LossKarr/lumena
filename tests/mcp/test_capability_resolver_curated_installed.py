"""Phase I-7 Fix G — Verrouille le fallback curated KNOWN_MCPS dans
_build_mcp_installed_candidates.

Bug d'origine : un MCP fraîchement INSTALLED n'a pas de DiscoveryReport
peuplée (oeuf/poule : il faut le spawner pour discovery, mais on a besoin
de discovery pour décider de spawner). Conséquence : aucun candidat
`mcp_installed` n'était généré → resolver tombait sur `already_applied`
au lieu de `ACTIVATE_INSTALLED_MCP` → install inutilisable.

Fix : pour les MCPs curated (KNOWN_MCPS), générer un candidat synthétique
basé sur slug + display_name + aliases + description quand la discovery
est vide.
"""
from __future__ import annotations

from typing import Any, Dict, List

import pytest

from src.mcp.capability_resolver import (
    CapabilityResolver,
    CapabilityResolverDeps,
)


class _StubEntry:
    def __init__(self, server_id: str, status: str, display_name: str = ""):
        self.server_id = server_id
        self.status = status
        self.display_name = display_name or server_id
        self.trust_score = 100


def _tokenize(text: str) -> set[str]:
    from src.mcp.capability_resolver import _tokenize as real
    return real(text)


class TestCuratedInstalledFallback:

    def _resolver(self) -> CapabilityResolver:
        # Resolver avec deps minimums (rien à brancher pour tester un helper interne).
        deps = CapabilityResolverDeps()
        return CapabilityResolver(deps=deps)

    def test_slack_installed_without_discovery_produces_candidate(self):
        """Un Slack installed SANS discovery doit générer un candidat curated."""
        resolver = self._resolver()
        intent_tokens = _tokenize("utiliser Slack envoyer message canal")
        slack_entry = _StubEntry(server_id="slack", status="installed", display_name="Slack")
        candidates = resolver._build_mcp_installed_candidates(
            intent_tokens=intent_tokens,
            catalog_entries=[slack_entry],
            discovery_by_server={},  # ← vide, c'est tout le sujet
        )
        assert len(candidates) >= 1, (
            "Avec curated KNOWN_MCPS, un candidat mcp_installed doit être "
            "généré même sans discovery"
        )
        c = candidates[0]
        assert c.server_id == "slack"
        assert c.catalog_status == "installed"
        assert c.kind == "mcp_installed"
        assert c.match_score > 0.0

    def test_non_curated_installed_without_discovery_no_candidate(self):
        """MCP installed NON curated + pas de discovery → toujours pas de candidat."""
        resolver = self._resolver()
        intent_tokens = _tokenize("utiliser random tool xyz")
        rnd_entry = _StubEntry(server_id="random-uncurated", status="installed")
        candidates = resolver._build_mcp_installed_candidates(
            intent_tokens=intent_tokens,
            catalog_entries=[rnd_entry],
            discovery_by_server={},
        )
        assert len(candidates) == 0, (
            "Sans curated et sans discovery, on ne peut pas deviner le match — "
            "comportement original préservé pour les MCPs non-curated"
        )

    def test_discovery_present_still_works(self):
        """Si la discovery EST peuplée, comportement original préservé."""
        resolver = self._resolver()
        intent_tokens = _tokenize("list slack channels")
        slack_entry = _StubEntry(server_id="slack", status="installed")
        discovery = {
            "slack": [
                {"name": "slack_list_channels", "description": "Lists Slack channels"},
            ],
        }
        candidates = resolver._build_mcp_installed_candidates(
            intent_tokens=intent_tokens,
            catalog_entries=[slack_entry],
            discovery_by_server=discovery,
        )
        # Au moins le candidat basé sur la discovery
        assert any(
            c.tool_name == "slack_list_channels" for c in candidates
        ), "Le pattern original avec discovery doit toujours marcher"

    def test_intent_unrelated_to_curated_produces_no_candidate(self):
        """Si l'intent ne matche ni discovery ni curated, pas de candidat."""
        resolver = self._resolver()
        intent_tokens = _tokenize("totally unrelated thing")
        slack_entry = _StubEntry(server_id="slack", status="installed")
        candidates = resolver._build_mcp_installed_candidates(
            intent_tokens=intent_tokens,
            catalog_entries=[slack_entry],
            discovery_by_server={},
        )
        # Pas de match sur le curated non plus
        assert len(candidates) == 0

    def test_declared_not_picked_by_installed_helper(self):
        """Anti-régression : _build_mcp_installed_candidates ignore declared."""
        resolver = self._resolver()
        intent_tokens = _tokenize("utiliser Slack")
        slack_declared = _StubEntry(server_id="slack", status="declared")
        candidates = resolver._build_mcp_installed_candidates(
            intent_tokens=intent_tokens,
            catalog_entries=[slack_declared],
            discovery_by_server={},
        )
        assert len(candidates) == 0
