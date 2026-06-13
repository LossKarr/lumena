"""Tests Phase I-5 — `autonomy_orchestrator.MCPAutonomyOrchestrator`."""
from __future__ import annotations

from pathlib import Path

import pytest

from src.mcp.autonomy_orchestrator import (
    AutonomyLevel,
    AutonomyResult,
    AutonomyState,
    MCPAutonomyOrchestrator,
    PendingQuestion,
    ResolvedField,
)
from src.mcp.config_service import MCPConfigService
from src.mcp.credentials_service import MCPCredentialsService
from src.mcp.secrets_resolver_service import MCPSecretsResolverService
from src.services.secrets_service import SecretsService


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def env(tmp_path: Path):
    secrets = SecretsService(
        secrets_dir=tmp_path / "secrets",
        master_key_path=tmp_path / "master.key",
    )
    creds = MCPCredentialsService(secrets)
    config = MCPConfigService(config_root=tmp_path / "mcp_config")
    resolver = MCPSecretsResolverService(
        credentials_service=creds, secrets_service=secrets,
    )
    orch = MCPAutonomyOrchestrator(
        credentials_service=creds,
        config_service=config,
        secrets_resolver=resolver,
    )
    return {
        "secrets": secrets, "creds": creds, "config": config,
        "resolver": resolver, "orch": orch,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Section 1 — Validation construction
# ══════════════════════════════════════════════════════════════════════════════


class TestConstruction:
    def test_creds_required(self, env):
        with pytest.raises(TypeError):
            MCPAutonomyOrchestrator(
                credentials_service="bad",  # type: ignore[arg-type]
                config_service=env["config"],
                secrets_resolver=env["resolver"],
            )

    def test_config_required(self, env):
        with pytest.raises(TypeError):
            MCPAutonomyOrchestrator(
                credentials_service=env["creds"],
                config_service="bad",  # type: ignore[arg-type]
                secrets_resolver=env["resolver"],
            )

    def test_resolver_required(self, env):
        with pytest.raises(TypeError):
            MCPAutonomyOrchestrator(
                credentials_service=env["creds"],
                config_service=env["config"],
                secrets_resolver="bad",  # type: ignore[arg-type]
            )


# ══════════════════════════════════════════════════════════════════════════════
# Section 2 — Intent dégénérés
# ══════════════════════════════════════════════════════════════════════════════


class TestEmptyIntent:
    @pytest.mark.parametrize("bad", ["", "   ", None, 42])
    def test_empty_returns_not_resolved(self, env, bad):
        r = env["orch"].fulfill_capability(bad)  # type: ignore[arg-type]
        assert r.state == AutonomyState.NOT_RESOLVED
        assert r.error_reason == "empty_intent"


class TestUnknownIntent:
    def test_unknown_mcp_returns_not_resolved(self, env):
        r = env["orch"].fulfill_capability(
            "blablabla unknown thing random xyz 12345"
        )
        assert r.state == AutonomyState.NOT_RESOLVED
        assert r.server_id is None


# ══════════════════════════════════════════════════════════════════════════════
# Section 3 — Scénario "tout résolu en autonomie"
# ══════════════════════════════════════════════════════════════════════════════


class TestFullyAutonomous:
    def test_slack_with_existing_token_in_other_mcp(self, env):
        # Un autre workspace Slack a déjà les clés posées
        env["creds"].set("other-slack-prod", "SLACK_BOT_TOKEN", "xoxb-prod")
        env["creds"].set("other-slack-prod", "SLACK_TEAM_ID", "T01")

        r = env["orch"].fulfill_capability("installe le MCP slack")

        assert r.state == AutonomyState.READY
        assert r.server_id == "slack"
        names = {rf.field_name for rf in r.resolved_fields}
        assert names == {"SLACK_BOT_TOKEN", "SLACK_TEAM_ID"}
        for rf in r.resolved_fields:
            assert rf.source == "credentials:other-slack-prod"
            assert rf.applied is True
        # Les valeurs ont été appliquées au scope 'slack'
        assert env["creds"].get("slack", "SLACK_BOT_TOKEN") == "xoxb-prod"

    def test_resolution_from_env(self, env, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-from-env")
        monkeypatch.setenv("SLACK_TEAM_ID", "T-env")
        r = env["orch"].fulfill_capability("slack")
        assert r.state == AutonomyState.READY
        for rf in r.resolved_fields:
            assert rf.source == "env"

    def test_memory_curated_zero_config(self, env):
        # 'memory' est un MCP curated sans aucun champ
        r = env["orch"].fulfill_capability("installe le MCP memory")
        assert r.state == AutonomyState.READY
        assert r.pending_questions == ()
        assert r.resolved_fields == ()


# ══════════════════════════════════════════════════════════════════════════════
# Section 4 — Scénario "questions à l'user"
# ══════════════════════════════════════════════════════════════════════════════


class TestNeedsUserInput:
    def test_slack_vierge(self, env):
        r = env["orch"].fulfill_capability("slack")
        assert r.state == AutonomyState.NEEDS_USER_INPUT
        names = {q.field_name for q in r.pending_questions}
        assert names == {"SLACK_BOT_TOKEN", "SLACK_TEAM_ID"}
        # Les questions portent les bonnes métadonnées
        token_q = next(q for q in r.pending_questions
                       if q.field_name == "SLACK_BOT_TOKEN")
        assert token_q.is_secret is True
        assert token_q.placeholder == "xoxb-..."
        assert "api.slack.com" in (token_q.obtained_from or "")

    def test_partially_resolved(self, env):
        env["creds"].set("other-mcp", "SLACK_BOT_TOKEN", "xoxb-cached")
        # SLACK_TEAM_ID toujours manquant
        r = env["orch"].fulfill_capability("slack")
        assert r.state == AutonomyState.NEEDS_USER_INPUT
        resolved_names = {rf.field_name for rf in r.resolved_fields}
        pending_names = {q.field_name for q in r.pending_questions}
        assert "SLACK_BOT_TOKEN" in resolved_names
        assert "SLACK_TEAM_ID" in pending_names

    def test_already_set_for_target_skips_question(self, env):
        # L'user a déjà mis ses propres valeurs pour 'slack' :
        #   - SLACK_BOT_TOKEN est SECRET → CredentialsService
        #   - SLACK_TEAM_ID est NORMAL → ConfigService
        env["creds"].set("slack", "SLACK_BOT_TOKEN", "xoxb-mine")
        env["config"].set("slack", "SLACK_TEAM_ID", "T-mine")
        r = env["orch"].fulfill_capability("slack")
        assert r.state == AutonomyState.READY
        # Source = already_set (pas re-applied)
        sources = {rf.source for rf in r.resolved_fields}
        assert sources == {"already_set"}


# ══════════════════════════════════════════════════════════════════════════════
# Section 5 — OAuth (auth_flows) → NEEDS_OAUTH
# ══════════════════════════════════════════════════════════════════════════════


class TestOAuthFlow:
    def test_google_drive_returns_needs_oauth(self, env):
        # google-drive a un AuthFlow OAuth dans KNOWN_MCPS
        r = env["orch"].fulfill_capability("google drive")
        assert r.state == AutonomyState.NEEDS_OAUTH
        assert "OAuth" in r.next_step_hint or "oauth" in r.next_step_hint.lower()


# ══════════════════════════════════════════════════════════════════════════════
# Section 6 — Niveaux d'autonomie
# ══════════════════════════════════════════════════════════════════════════════


class TestAutonomyLevels:
    def test_read_only_with_pending_returns_needs_input(self, env):
        r = env["orch"].fulfill_capability(
            "slack", level=AutonomyLevel.READ_ONLY,
        )
        assert r.state == AutonomyState.NEEDS_USER_INPUT
        assert "read-only" in r.next_step_hint.lower()

    def test_read_only_zero_config_returns_ready(self, env):
        r = env["orch"].fulfill_capability(
            "memory", level=AutonomyLevel.READ_ONLY,
        )
        assert r.state == AutonomyState.READY

    def test_full_level_does_not_install_yet(self, env):
        """Phase I-5 prépare uniquement — l'install effectif est en I-6/I-7."""
        env["creds"].set("other-mcp", "SLACK_BOT_TOKEN", "xoxb-x")
        env["creds"].set("other-mcp", "SLACK_TEAM_ID", "T-x")
        r = env["orch"].fulfill_capability(
            "slack", level=AutonomyLevel.FULL,
        )
        # FULL ne change pas le retour de l'orchestrateur (juste tag)
        assert r.state == AutonomyState.READY


# ══════════════════════════════════════════════════════════════════════════════
# Section 7 — Routage SECRET vs NORMAL (apply correct au bon service)
# ══════════════════════════════════════════════════════════════════════════════


class TestRoutageStorage:
    def test_secret_goes_to_credentials_service(self, env, monkeypatch):
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-env")
        monkeypatch.setenv("SLACK_TEAM_ID", "T-env")
        env["orch"].fulfill_capability("slack")
        # SECRET → CredentialsService
        assert env["creds"].has("slack", "SLACK_BOT_TOKEN") is True
        # NORMAL → ConfigService
        assert env["config"].has("slack", "SLACK_TEAM_ID") is True
        # Et PAS l'inverse
        assert env["config"].has("slack", "SLACK_BOT_TOKEN") is False
        assert env["creds"].has("slack", "SLACK_TEAM_ID") is False


# ══════════════════════════════════════════════════════════════════════════════
# Section 8 — Privacy : aucune valeur de secret dans AutonomyResult
# ══════════════════════════════════════════════════════════════════════════════


class TestPrivacy:
    def test_no_secret_value_in_result(self, env, monkeypatch):
        secret = "xoxb-VERY-SECRET-VALUE-12345"
        monkeypatch.setenv("SLACK_BOT_TOKEN", secret)
        monkeypatch.setenv("SLACK_TEAM_ID", "T-x")
        r = env["orch"].fulfill_capability("slack")
        # Aucun champ de l'AutonomyResult ne doit contenir la valeur
        blob = repr(r)
        assert secret not in blob
        # Y compris dans les ResolvedField
        for rf in r.resolved_fields:
            assert secret not in repr(rf)
