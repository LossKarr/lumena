"""Phase I-7 — Verrouille _derive_server_id curated lookup.

Bug d'origine : `_derive_server_id` retournait toujours `proposed_<hash10>`
même pour les MCPs curated (KNOWN_MCPS). Conséquence : le `target_server_id`
des tickets était `proposed_e28f7ba17d` au lieu de `slack`, donc le bypass
auto-approve curated de Phase I-7 ne pouvait jamais matcher → user click
forcé même pour les MCPs officiels (Slack, GitHub, Notion, etc.).

Fix : lookup KNOWN_MCPS par package_spec exact AVANT le fallback hash.
"""
from __future__ import annotations

from src.mcp.known_mcps import find_known_mcp_by_package_spec


class TestFindKnownMcpByPackageSpec:

    def test_slack_npm_package_returns_slug(self):
        mcp = find_known_mcp_by_package_spec("npm:@modelcontextprotocol/server-slack")
        assert mcp is not None
        assert mcp.slug == "slack"

    def test_github_npm_package_returns_slug(self):
        mcp = find_known_mcp_by_package_spec("npm:@modelcontextprotocol/server-github")
        assert mcp is not None
        assert mcp.slug == "github"

    def test_filesystem_npm_returns_slug(self):
        mcp = find_known_mcp_by_package_spec("npm:@modelcontextprotocol/server-filesystem")
        assert mcp is not None
        assert mcp.slug == "filesystem"

    def test_memory_npm_returns_slug(self):
        mcp = find_known_mcp_by_package_spec("npm:@modelcontextprotocol/server-memory")
        assert mcp is not None
        assert mcp.slug == "memory"

    def test_unknown_package_returns_none(self):
        assert find_known_mcp_by_package_spec("npm:@random/uncurated-mcp") is None

    def test_empty_returns_none(self):
        assert find_known_mcp_by_package_spec("") is None
        assert find_known_mcp_by_package_spec("   ") is None
        assert find_known_mcp_by_package_spec(None) is None  # type: ignore

    def test_whitespace_tolerant(self):
        # Strip is applied
        mcp = find_known_mcp_by_package_spec(
            "  npm:@modelcontextprotocol/server-slack  "
        )
        assert mcp is not None
        assert mcp.slug == "slack"

    def test_case_sensitive(self):
        # package_spec match doit être exact (case-sensitive : convention npm)
        assert find_known_mcp_by_package_spec(
            "NPM:@MODELCONTEXTPROTOCOL/SERVER-SLACK"
        ) is None


class TestDeriveServerIdIntegration:
    """Vérifie que _derive_server_id retourne le slug curated."""

    def test_derive_uses_curated_slug_when_package_matches(self):
        """Phase I-7 : le slug curated est préféré au hash."""
        from src.mcp.proposal_planner import MCPProposalPlanner

        # Reconstitue un MCPSearchResult minimal
        class _FakeSR:
            package_spec = "npm:@modelcontextprotocol/server-slack"

        # Méthode privée mais déterministe ; on bypasse l'init de la classe
        # complète (pas besoin des deps pour _derive_server_id qui est pur).
        sid = MCPProposalPlanner._derive_server_id(None, _FakeSR())  # type: ignore[arg-type]
        assert sid == "slack", f"attendu 'slack', vu {sid!r}"

    def test_derive_falls_back_to_hash_for_uncurated(self):
        from src.mcp.proposal_planner import MCPProposalPlanner

        class _FakeSR:
            package_spec = "npm:@random/uncurated-mcp"

        sid = MCPProposalPlanner._derive_server_id(None, _FakeSR())  # type: ignore[arg-type]
        assert sid.startswith("proposed_"), f"attendu fallback 'proposed_*', vu {sid!r}"
        assert len(sid) == len("proposed_") + 10  # hash[:10]

    def test_derive_github_returns_curated_slug(self):
        from src.mcp.proposal_planner import MCPProposalPlanner

        class _FakeSR:
            package_spec = "npm:@modelcontextprotocol/server-github"

        sid = MCPProposalPlanner._derive_server_id(None, _FakeSR())  # type: ignore[arg-type]
        assert sid == "github"
