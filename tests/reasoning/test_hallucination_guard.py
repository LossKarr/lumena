"""Tests — Guard anti-hallucination dans react.py.

Vérifie que :
- Les constantes _HC_TOOLS_* sont des frozensets non vides et cohérents
- Les patterns génériques pointent sur des familles sémantiques et non _ALL_MUTATIONS
- _HC_TOOLS_ANY_CREATE et _HC_TOOLS_ANY_SEND couvrent les bonnes familles
- Aucun pattern ne référence _LEDGER_MUTATION_TOOLS en entier (>100 outils)
- Les familles critique (mail, discord, github) sont disjointes de read-only
"""

import pytest
from src.reasoning.react import (
    _HC_TOOLS_FILE,
    _HC_TOOLS_DOC,
    _HC_TOOLS_SITE,
    _HC_TOOLS_TASK,
    _HC_TOOLS_MAIL,
    _HC_TOOLS_DISCORD,
    _HC_TOOLS_MESSAGING,
    _HC_TOOLS_SOCIAL,
    _HC_TOOLS_STRIPE,
    _HC_TOOLS_GITHUB,
    _HC_TOOLS_IMAGE,
    _HC_TOOLS_NOTION,
    _HC_TOOLS_ANY_CREATE,
    _HC_TOOLS_ANY_SEND,
)
from src.runtime.execution_ledger import MUTATION_TOOLS as _ALL_MUTATION_TOOLS

_READONLY_TOOLS = frozenset({
    "read_file", "web_search", "search_web", "read_url", "memory_recall",
    "memory_retrieve", "get_context", "list_files", "list_directory",
    "search_memory", "retrieve_memory", "get_weather",
})


class TestHCToolsFamilies:
    def test_all_families_nonempty(self):
        for name, family in [
            ("FILE", _HC_TOOLS_FILE), ("DOC", _HC_TOOLS_DOC), ("SITE", _HC_TOOLS_SITE),
            ("TASK", _HC_TOOLS_TASK), ("MAIL", _HC_TOOLS_MAIL), ("DISCORD", _HC_TOOLS_DISCORD),
            ("MESSAGING", _HC_TOOLS_MESSAGING), ("SOCIAL", _HC_TOOLS_SOCIAL),
            ("STRIPE", _HC_TOOLS_STRIPE), ("GITHUB", _HC_TOOLS_GITHUB),
            ("IMAGE", _HC_TOOLS_IMAGE), ("NOTION", _HC_TOOLS_NOTION),
        ]:
            assert len(family) > 0, f"_HC_TOOLS_{name} est vide"

    def test_all_families_are_frozensets(self):
        for family in [
            _HC_TOOLS_FILE, _HC_TOOLS_DOC, _HC_TOOLS_SITE, _HC_TOOLS_TASK,
            _HC_TOOLS_MAIL, _HC_TOOLS_DISCORD, _HC_TOOLS_MESSAGING, _HC_TOOLS_SOCIAL,
            _HC_TOOLS_STRIPE, _HC_TOOLS_GITHUB, _HC_TOOLS_IMAGE, _HC_TOOLS_NOTION,
            _HC_TOOLS_ANY_CREATE, _HC_TOOLS_ANY_SEND,
        ]:
            assert isinstance(family, frozenset)

    def test_any_create_contains_file_family(self):
        assert _HC_TOOLS_FILE <= _HC_TOOLS_ANY_CREATE

    def test_any_create_contains_doc_family(self):
        assert _HC_TOOLS_DOC <= _HC_TOOLS_ANY_CREATE

    def test_any_create_contains_site_family(self):
        assert _HC_TOOLS_SITE <= _HC_TOOLS_ANY_CREATE

    def test_any_create_contains_task_family(self):
        assert _HC_TOOLS_TASK <= _HC_TOOLS_ANY_CREATE

    def test_any_create_contains_stripe_family(self):
        assert _HC_TOOLS_STRIPE <= _HC_TOOLS_ANY_CREATE

    def test_any_create_contains_github_family(self):
        assert _HC_TOOLS_GITHUB <= _HC_TOOLS_ANY_CREATE

    def test_any_send_contains_mail(self):
        assert _HC_TOOLS_MAIL <= _HC_TOOLS_ANY_SEND

    def test_any_send_contains_messaging(self):
        assert _HC_TOOLS_MESSAGING <= _HC_TOOLS_ANY_SEND

    def test_any_send_contains_social(self):
        assert _HC_TOOLS_SOCIAL <= _HC_TOOLS_ANY_SEND

    def test_families_not_equal_to_all_mutations(self):
        """Les familles sémantiques ne doivent pas couvrir ALL mutations (>100 outils)."""
        assert len(_HC_TOOLS_ANY_CREATE) < len(_ALL_MUTATION_TOOLS), (
            f"_HC_TOOLS_ANY_CREATE ({len(_HC_TOOLS_ANY_CREATE)}) doit être < MUTATION_TOOLS ({len(_ALL_MUTATION_TOOLS)})"
        )

    def test_no_readonly_in_create_family(self):
        assert not (_HC_TOOLS_ANY_CREATE & _READONLY_TOOLS), (
            f"Outils read-only dans ANY_CREATE: {_HC_TOOLS_ANY_CREATE & _READONLY_TOOLS}"
        )

    def test_no_readonly_in_send_family(self):
        assert not (_HC_TOOLS_ANY_SEND & _READONLY_TOOLS), (
            f"Outils read-only dans ANY_SEND: {_HC_TOOLS_ANY_SEND & _READONLY_TOOLS}"
        )

    def test_mail_tools_in_send_not_in_create(self):
        """mail_send est dans ANY_SEND mais pas dans ANY_CREATE."""
        assert "mail_send" in _HC_TOOLS_ANY_SEND
        assert "mail_send" not in _HC_TOOLS_ANY_CREATE

    def test_write_file_in_create_not_in_send(self):
        assert "write_file" in _HC_TOOLS_ANY_CREATE
        assert "write_file" not in _HC_TOOLS_ANY_SEND

    def test_github_tools_in_both(self):
        """GitHub crée ET pousse → dans ANY_CREATE et ANY_SEND."""
        assert _HC_TOOLS_GITHUB <= _HC_TOOLS_ANY_CREATE
        assert _HC_TOOLS_GITHUB <= _HC_TOOLS_ANY_SEND

    def test_stripe_not_in_send(self):
        """Stripe est création de ressources, pas envoi de messages."""
        assert not (_HC_TOOLS_STRIPE & _HC_TOOLS_ANY_SEND)

    def test_image_in_create_not_in_send(self):
        assert _HC_TOOLS_IMAGE <= _HC_TOOLS_ANY_CREATE
        assert not (_HC_TOOLS_IMAGE & _HC_TOOLS_ANY_SEND)

    def test_notion_in_create(self):
        assert _HC_TOOLS_NOTION <= _HC_TOOLS_ANY_CREATE

    def test_discord_in_send(self):
        assert _HC_TOOLS_DISCORD <= _HC_TOOLS_ANY_SEND

    def test_key_tools_present(self):
        """Spot-checks pour les outils les plus critiques."""
        assert "create_task" in _HC_TOOLS_TASK
        assert "schedule_task" in _HC_TOOLS_TASK
        assert "discord_send_message" in _HC_TOOLS_DISCORD
        assert "git_commit" in _HC_TOOLS_GITHUB
        assert "generate_image" in _HC_TOOLS_IMAGE
        assert "stripe_create_invoice" in _HC_TOOLS_STRIPE
        assert "telegram_send_message" in _HC_TOOLS_MESSAGING
        assert "twitter_post_tweet" in _HC_TOOLS_SOCIAL
