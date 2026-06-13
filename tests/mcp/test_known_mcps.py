"""Tests Phase I-1 — `known_mcps.py` (catalogue curated)."""
from __future__ import annotations

import pytest

from src.mcp.config_schema import MCPConfigSchema, Sensitivity
from src.mcp.known_mcps import (
    KnownMCP,
    get_known_mcp,
    list_known_mcp_slugs,
    lookup_known_mcp,
)


class TestRegistry:
    def test_has_at_least_15_entries(self):
        slugs = list_known_mcp_slugs()
        assert len(slugs) >= 15

    def test_essential_mcps_present(self):
        slugs = set(list_known_mcp_slugs())
        for must_have in (
            "memory", "fetch", "filesystem", "slack", "github",
            "postgres", "google-drive",
        ):
            assert must_have in slugs, f"{must_have!r} doit être catalogué"

    def test_all_have_valid_transport(self):
        for slug in list_known_mcp_slugs():
            m = get_known_mcp(slug)
            assert m.transport in ("npm", "pypi")

    def test_all_have_package_spec(self):
        for slug in list_known_mcp_slugs():
            m = get_known_mcp(slug)
            assert m.package_spec.startswith(("npm:", "pypi:"))

    def test_all_have_unique_slug(self):
        slugs = list_known_mcp_slugs()
        assert len(slugs) == len(set(slugs))

    def test_all_have_trust_score_0_100(self):
        for slug in list_known_mcp_slugs():
            m = get_known_mcp(slug)
            assert 0 <= m.trust_score <= 100


class TestLookupExact:
    @pytest.mark.parametrize("query,expected", [
        ("slack", "slack"),
        ("github", "github"),
        ("postgres", "postgres"),
        ("memory", "memory"),
        ("filesystem", "filesystem"),
        ("google-drive", "google-drive"),
    ])
    def test_slug(self, query, expected):
        m = lookup_known_mcp(query)
        assert m is not None
        assert m.slug == expected

    @pytest.mark.parametrize("query,expected", [
        ("gdrive", "google-drive"),
        ("gh", "github"),
        ("fs", "filesystem"),
        ("slack-workspace", "slack"),
    ])
    def test_alias(self, query, expected):
        m = lookup_known_mcp(query)
        assert m is not None
        assert m.slug == expected


class TestLookupNormalized:
    @pytest.mark.parametrize("query,expected", [
        ("SLACK", "slack"),
        ("Slack", "slack"),
        ("  slack  ", "slack"),
        ("slack!!", "slack"),
        ("POSTGRES", "postgres"),
    ])
    def test_case_and_ponctuation(self, query, expected):
        m = lookup_known_mcp(query)
        assert m is not None
        assert m.slug == expected


class TestLookupContains:
    @pytest.mark.parametrize("query,expected", [
        ("Installe le MCP Slack pour moi", "slack"),
        ("Je veux ajouter postgres dans Lumena", "postgres"),
        ("Connecte mon Google Drive", "google-drive"),
        ("Configure GitHub pour mes projets", "github"),
    ])
    def test_intent_libre(self, query, expected):
        m = lookup_known_mcp(query)
        assert m is not None
        assert m.slug == expected


class TestLookupMiss:
    @pytest.mark.parametrize("query", [
        "",
        "   ",
        "blablabla random text",
        "yet-another-unknown-mcp",
    ])
    def test_miss(self, query):
        assert lookup_known_mcp(query) is None

    def test_non_string_returns_none(self):
        assert lookup_known_mcp(None) is None  # type: ignore[arg-type]
        assert lookup_known_mcp(42) is None    # type: ignore[arg-type]


class TestToSchema:
    def test_slack_to_schema(self):
        m = get_known_mcp("slack")
        s = m.to_schema()
        assert isinstance(s, MCPConfigSchema)
        assert s.server_id == "slack"
        assert s.detected_from == "curated"
        names = s.field_names()
        assert "SLACK_BOT_TOKEN" in names
        secret_names = s.secret_field_names()
        assert "SLACK_BOT_TOKEN" in secret_names

    def test_memory_to_schema_zero_fields(self):
        m = get_known_mcp("memory")
        s = m.to_schema()
        assert s.fields == ()

    def test_postgres_mixed_fields(self):
        m = get_known_mcp("postgres")
        s = m.to_schema()
        # Mot de passe = secret, le reste = normal
        assert "POSTGRES_PASSWORD" in s.secret_field_names()
        for f in s.fields:
            if f.name != "POSTGRES_PASSWORD":
                assert f.sensitivity == Sensitivity.NORMAL


class TestGoogleDriveOAuth:
    def test_has_auth_flow(self):
        m = get_known_mcp("google-drive")
        assert m is not None
        s = m.to_schema()
        assert len(s.auth_flows) >= 1
        assert s.auth_flows[0].provider == "google"
        assert s.auth_flows[0].kind == "oauth2_authorization_code"
