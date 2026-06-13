"""Phase I-7 fix H — Verrouille l'interdiction cross-provider du auto-fix.

Bug d'origine : `auto_fix_action_name` utilisait `difflib.get_close_matches`
avec cutoff=0.75 sans contrainte. Conséquence :
  - `slack__list_channels` (MCP Slack non actif)
  - → fuzzy match vers `discord_list_channels` (handler natif Discord)
  - → Lumena appelle Discord en croyant appeler Slack
  - → Risque sécurité : un `slack__post_message(channel=#general, text=...)`
    aurait pu poster sur Discord à la place.

Fix : refuser le fuzzy si le préfixe provider (avant `_` ou `__`) diffère.
"""
from __future__ import annotations

import pytest

from src.llm.output_normalizer import (
    _provider_prefix,
    auto_fix_action_name,
)


class TestProviderPrefix:
    """Phase I-7 fix H : seul le format MCP (`__`) déclenche la garde."""

    def test_mcp_double_underscore_detected(self):
        assert _provider_prefix("slack__list_channels") == "slack"
        assert _provider_prefix("github__create_issue") == "github"

    def test_single_underscore_is_not_mcp(self):
        # Important : `_` simple n'est PAS un préfixe MCP, juste un séparateur.
        # Sinon on bloquerait les typos légitimes type `liste_files`→`list_files`.
        assert _provider_prefix("discord_list_channels") == ""
        assert _provider_prefix("stripe_create_subscription") == ""

    def test_no_underscore(self):
        assert _provider_prefix("foo") == ""
        assert _provider_prefix("") == ""


class TestAutoFixCrossProviderRejected:

    def test_slack_mcp_does_not_match_discord_native(self):
        """LE bug critique : slack__list_channels ne doit JAMAIS muter en discord_list_channels."""
        known = {
            "discord_list_channels",
            "discord_create_channel",
            "discord_fetch_messages",
            # Aucun tool slack__ enregistré (MCP non actif)
        }
        result = auto_fix_action_name("slack__list_channels", known)
        assert result == "slack__list_channels", (
            f"SÉCURITÉ : slack__* ne doit pas muter vers discord_* "
            f"(fuzzy cross-provider interdit). Vu : {result!r}"
        )

    def test_slack_mcp_does_not_match_stripe(self):
        known = {"stripe_create_subscription", "stripe_list_customers"}
        result = auto_fix_action_name("slack__post_message", known)
        assert result == "slack__post_message"

    def test_github_mcp_does_not_match_discord(self):
        known = {"discord_create_channel", "discord_list_channels"}
        result = auto_fix_action_name("github__create_issue", known)
        assert result == "github__create_issue"


class TestAutoFixSameProviderAllowed:

    def test_typo_within_same_provider_corrected(self):
        """slack__list_channelz (typo) → slack__list_channels OK même provider."""
        known = {"slack__list_channels", "discord_list_channels"}
        result = auto_fix_action_name("slack__list_channelz", known)
        assert result == "slack__list_channels", (
            "Le auto-fix doit corriger un typo dans le même provider"
        )

    def test_discord_typo_to_discord_real(self):
        known = {"discord_list_channels", "discord_create_channel"}
        result = auto_fix_action_name("discord_lst_channels", known)
        assert result == "discord_list_channels"


class TestAutoFixExistingBehaviorPreserved:

    def test_exact_match_returns_as_is(self):
        known = {"read_file"}
        assert auto_fix_action_name("read_file", known) == "read_file"

    def test_empty_returns_empty(self):
        assert auto_fix_action_name("", {"read_file"}) == ""

    def test_no_match_returns_original(self):
        known = {"read_file"}
        assert auto_fix_action_name("unknown_xyz", known) == "unknown_xyz"

    def test_no_underscore_in_request_no_constraint(self):
        """Si le nom demandé n'a pas de préfixe, pas de contrainte cross-provider."""
        known = {"read_file"}
        # "readfile" → fuzzy → "read_file" (score > 0.75)
        # Pas de préfixe détectable dans "readfile" → fuzzy autorisé sans garde
        result = auto_fix_action_name("readfile", known)
        # Peut matcher ou non selon difflib, mais ne doit pas planter
        assert result in ("readfile", "read_file")
