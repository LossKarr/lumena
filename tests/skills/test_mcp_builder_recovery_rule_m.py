"""Phase I-7 Fix M — Verrouille la règle de récupération dans
skills/mcp-builder/SKILL.md.

Bug d'origine : quand Lumena cherchait `slack__list_channels` et que
le tool n'était pas dans sa liste (MCP installed mais pas activé),
elle tâtonnait avec discover_tools puis abandonnait au lieu d'appeler
`run_mcp_autonomy` pour tenter l'activation.

Fix M : ajoute une section "Règle de récupération" au skill avec
l'ordre strict — activate auto AVANT d'abandonner.
"""
from __future__ import annotations

from pathlib import Path

import pytest


SKILL_PATH = Path(__file__).parents[2] / "skills" / "mcp-builder" / "SKILL.md"


@pytest.fixture(scope="module")
def skill_content() -> str:
    return SKILL_PATH.read_text(encoding="utf-8")


class TestRecoveryRuleSection:

    def test_recovery_section_present(self, skill_content):
        assert "Règle de récupération" in skill_content, (
            "Le skill doit avoir une section 'Règle de récupération' "
            "pour gérer le cas tool MCP absent"
        )

    def test_fix_m_marker(self, skill_content):
        assert "fix M" in skill_content or "Fix M" in skill_content, (
            "Marker 'fix M' nécessaire pour audit"
        )


class TestRecoveryRuleSemantic:

    def test_mentions_dont_abandon(self, skill_content):
        """Le skill doit dire 'NE PAS abandonner'."""
        # Cherche un terme parlant
        lc = skill_content.lower()
        assert any(term in lc for term in (
            "ne pas abandonner",
            "abandonner",
        )), "La règle doit explicitement parler d'éviter l'abandon"

    def test_mentions_run_mcp_autonomy_in_recovery(self, skill_content):
        """run_mcp_autonomy doit être mentionné comme l'outil de récupération."""
        # Trouve la section récupération
        idx = skill_content.find("Règle de récupération")
        assert idx != -1
        section = skill_content[idx:idx + 2000]
        assert "run_mcp_autonomy" in section, (
            "La section récupération doit recommander run_mcp_autonomy"
        )

    def test_mentions_confirmation_phrase(self, skill_content):
        """Doit mentionner I-CONFIRM-MCP-AUTONOMY dans la récupération."""
        idx = skill_content.find("Règle de récupération")
        section = skill_content[idx:idx + 2000]
        assert "I-CONFIRM-MCP-AUTONOMY" in section, (
            "La confirmation_phrase exacte doit être présente"
        )

    def test_mentions_provider_namespace(self, skill_content):
        """Doit mentionner la convention <provider>__<tool>."""
        idx = skill_content.find("Règle de récupération")
        section = skill_content[idx:idx + 2000]
        assert "__" in section, (
            "La convention <provider>__<tool> doit apparaître"
        )

    def test_anti_pattern_documented(self, skill_content):
        """Documente l'anti-pattern (tâtonner avec discover_tools)."""
        assert "discover_tools" in skill_content, (
            "Anti-pattern impliquant discover_tools doit être documenté"
        )

    def test_correct_pattern_documented(self, skill_content):
        """Documente le pattern correct (autonomy_activated → retry)."""
        # Cherche les deux blocs Pattern/Anti-pattern
        assert "Pattern correct" in skill_content
        assert "Anti-pattern" in skill_content

    def test_secrets_voie_a_voie_b_mentioned(self, skill_content):
        """Pour secrets manquants : deux voies (coller direct OU panel UI)."""
        idx = skill_content.find("Règle de récupération")
        section = skill_content[idx:idx + 2000]
        # Mentionne les deux options
        assert "MCP > Bibliothèque" in section or "panel" in section.lower()
