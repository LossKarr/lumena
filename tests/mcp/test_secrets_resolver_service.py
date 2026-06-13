"""Tests Phase I-4 — `secrets_resolver_service.MCPSecretsResolverService`."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.mcp.credentials_service import MCPCredentialsService
from src.mcp.secrets_resolver_service import (
    FoundSecret,
    MCPSecretsResolverService,
)
from src.services.secrets_service import SecretsService


@pytest.fixture
def secrets(tmp_path: Path) -> SecretsService:
    return SecretsService(
        secrets_dir=tmp_path / "secrets",
        master_key_path=tmp_path / "master.key",
    )


@pytest.fixture
def creds(secrets) -> MCPCredentialsService:
    return MCPCredentialsService(secrets)


@pytest.fixture
def resolver(creds, secrets) -> MCPSecretsResolverService:
    return MCPSecretsResolverService(
        credentials_service=creds, secrets_service=secrets,
    )


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Construction
# ══════════════════════════════════════════════════════════════════════════════


class TestConstruction:
    def test_wrong_creds_raises(self, secrets):
        with pytest.raises(TypeError):
            MCPSecretsResolverService(
                credentials_service="bad",  # type: ignore[arg-type]
                secrets_service=secrets,
            )

    def test_wrong_secrets_raises(self, creds):
        with pytest.raises(TypeError):
            MCPSecretsResolverService(
                credentials_service=creds,
                secrets_service="bad",  # type: ignore[arg-type]
            )

    def test_wrong_memory_raises(self, creds, secrets):
        with pytest.raises(TypeError):
            MCPSecretsResolverService(
                credentials_service=creds,
                secrets_service=secrets,
                memory_lookup="not callable",  # type: ignore[arg-type]
            )


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Source 1 : autres MCPs (credentials)
# ══════════════════════════════════════════════════════════════════════════════


class TestSourceCredentialsOtherMcp:
    def test_finds_in_other_mcp(self, resolver, creds):
        creds.set("other-slack", "SLACK_BOT_TOKEN", "xoxb-from-other")
        r = resolver.find_existing_value(
            "SLACK_BOT_TOKEN", exclude_server_id="alice",
        )
        assert r is not None
        assert r.value == "xoxb-from-other"
        assert r.source == "credentials:other-slack"
        assert r.confidence == 1.0
        assert r.requires_user_confirmation is False

    def test_exclude_server_id_self(self, resolver, creds):
        creds.set("alice", "OWN_TOKEN", "private-to-alice")
        r = resolver.find_existing_value(
            "OWN_TOKEN", exclude_server_id="alice",
        )
        # 'alice' est exclu, et personne d'autre n'a la clé
        assert r is None


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Source 2 : scope global lumena
# ══════════════════════════════════════════════════════════════════════════════


class TestSourceGlobalScope:
    def test_finds_in_global(self, resolver, secrets):
        secrets.set("lumena_global", "BRAVE_API_KEY", "BSA-global")
        r = resolver.find_existing_value("BRAVE_API_KEY")
        assert r is not None
        assert r.value == "BSA-global"
        assert r.source == "global"


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Source 3 : os.environ (.env legacy)
# ══════════════════════════════════════════════════════════════════════════════


class TestSourceEnv:
    def test_finds_in_env(self, resolver, monkeypatch):
        monkeypatch.setenv("CUSTOM_LEGACY_API_KEY", "from-env-123")
        r = resolver.find_existing_value("CUSTOM_LEGACY_API_KEY")
        assert r is not None
        assert r.value == "from-env-123"
        assert r.source == "env"
        assert r.requires_user_confirmation is False

    def test_env_skipped_if_empty(self, resolver, monkeypatch):
        monkeypatch.setenv("EMPTY_VAR", "")
        r = resolver.find_existing_value("EMPTY_VAR")
        assert r is None


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — Source 4 : Mémoire (optionnel)
# ══════════════════════════════════════════════════════════════════════════════


class TestSourceMemory:
    def test_memory_hit_high_confidence(self, creds, secrets):
        def fake_memory(canonical, aliases):
            return ("from-memory", 0.92)
        resolver = MCPSecretsResolverService(
            credentials_service=creds,
            secrets_service=secrets,
            memory_lookup=fake_memory,
        )
        r = resolver.find_existing_value("SOMETHING_KEY")
        assert r is not None
        assert r.source == "memory"
        assert r.confidence == pytest.approx(0.92)
        assert r.requires_user_confirmation is False

    def test_memory_hit_low_confidence(self, creds, secrets):
        def fake_memory(canonical, aliases):
            return ("low-conf", 0.6)
        resolver = MCPSecretsResolverService(
            credentials_service=creds, secrets_service=secrets,
            memory_lookup=fake_memory,
        )
        r = resolver.find_existing_value("SOMETHING_KEY")
        assert r is not None
        assert r.requires_user_confirmation is True

    def test_memory_failure_swallowed(self, creds, secrets):
        def boom(canonical, aliases):
            raise RuntimeError("chromadb down")
        resolver = MCPSecretsResolverService(
            credentials_service=creds, secrets_service=secrets,
            memory_lookup=boom,
        )
        # Ne crash pas
        r = resolver.find_existing_value("WHATEVER_KEY")
        assert r is None

    def test_no_memory_lookup_returns_none(self, resolver):
        r = resolver.find_existing_value("UNFINDABLE_KEY")
        assert r is None


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — Priorité des sources
# ══════════════════════════════════════════════════════════════════════════════


class TestPriority:
    def test_credentials_wins_over_global(self, resolver, creds, secrets):
        creds.set("other-mcp", "SHARED_TOKEN", "from-other-mcp")
        secrets.set("lumena_global", "SHARED_TOKEN", "from-global")
        r = resolver.find_existing_value(
            "SHARED_TOKEN", exclude_server_id="alice",
        )
        assert r.source == "credentials:other-mcp"

    def test_global_wins_over_env(self, resolver, secrets, monkeypatch):
        secrets.set("lumena_global", "PRIORITY_TEST_KEY", "from-global")
        monkeypatch.setenv("PRIORITY_TEST_KEY", "from-env")
        r = resolver.find_existing_value("PRIORITY_TEST_KEY")
        assert r.source == "global"


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — Batch / Aliases
# ══════════════════════════════════════════════════════════════════════════════


class TestBatch:
    def test_find_for_keys(self, resolver, creds):
        creds.set("other-x", "KEY_A", "value-a")
        results = resolver.find_for_keys(
            ["KEY_A", "MISSING_KEY"], exclude_server_id="alice",
        )
        assert isinstance(results["KEY_A"], FoundSecret)
        assert results["MISSING_KEY"] is None


class TestAliases:
    def test_aliases_searched(self, resolver, secrets):
        secrets.set("lumena_global", "SLACK_TOKEN", "xoxb")
        r = resolver.find_existing_value(
            "SLACK_BOT_TOKEN", aliases=("SLACK_TOKEN",),
        )
        assert r is not None
        assert r.value == "xoxb"


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — Robustesse
# ══════════════════════════════════════════════════════════════════════════════


class TestRobustness:
    @pytest.mark.parametrize("bad", ["", None, 42])
    def test_invalid_canonical_returns_none(self, resolver, bad):
        assert resolver.find_existing_value(bad) is None  # type: ignore[arg-type]

    def test_nothing_found_returns_none(self, resolver):
        assert resolver.find_existing_value("NOTHING_HERE_AT_ALL") is None
