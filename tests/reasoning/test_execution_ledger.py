"""
🧪 Tests — ExecutionLedger : méthodes de query avancées (V1)

Couvre :
- has_mutation_in_family : True si au moins une mutation réussie appartient à la famille
- has_mutation_for_target_hint : True si une mutation réussie a une cible contenant le hint
- INTENT_TO_MUTATION_FAMILY : mapping intent → frozenset
"""

import pytest

from src.runtime.execution_ledger import (
    ExecutionLedger,
    INTENT_TO_MUTATION_FAMILY,
    MUTATION_TOOLS,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _ledger_with(*actions: tuple) -> ExecutionLedger:
    """Crée un ledger peuplé de (action, target, success)."""
    ledger = ExecutionLedger()
    for i, (action, target, success) in enumerate(actions):
        ledger.append(
            iteration=i,
            action=action,
            target=target,
            success=success,
        )
    return ledger


# ── Tests : has_mutation_in_family ───────────────────────────────────────────

class TestHasMutationInFamily:
    def test_true_when_matching_tool_present(self):
        family = frozenset({"discord_send", "discord_send_message"})
        ledger = _ledger_with(("discord_send", "#general", True))
        assert ledger.has_mutation_in_family(family) is True

    def test_false_when_no_matching_tool(self):
        family = frozenset({"discord_send", "discord_send_message"})
        ledger = _ledger_with(("write_file", "/tmp/foo.py", True))
        assert ledger.has_mutation_in_family(family) is False

    def test_false_when_tool_matches_but_failed(self):
        family = frozenset({"discord_send"})
        ledger = _ledger_with(("discord_send", "#general", False))
        assert ledger.has_mutation_in_family(family) is False

    def test_false_when_ledger_empty(self):
        family = frozenset({"write_file"})
        ledger = ExecutionLedger()
        assert ledger.has_mutation_in_family(family) is False

    def test_false_when_family_empty(self):
        ledger = _ledger_with(("write_file", "/tmp/a.py", True))
        assert ledger.has_mutation_in_family(frozenset()) is False

    def test_true_with_multiple_tools_one_matches(self):
        family = frozenset({"edit_file", "write_file"})
        ledger = _ledger_with(
            ("run_command", "/tmp", True),
            ("edit_file", "/src/main.py", True),
        )
        assert ledger.has_mutation_in_family(family) is True

    def test_uses_intent_to_mutation_family_discord(self):
        family = INTENT_TO_MUTATION_FAMILY["discord"]
        ledger = _ledger_with(("discord_create_channel", "salon-test", True))
        assert ledger.has_mutation_in_family(family) is True

    def test_uses_intent_to_mutation_family_code_edit(self):
        family = INTENT_TO_MUTATION_FAMILY["code_edit"]
        ledger = _ledger_with(("write_file", "/src/app.py", True))
        assert ledger.has_mutation_in_family(family) is True

    def test_discord_intent_not_matched_by_file_tools(self):
        family = INTENT_TO_MUTATION_FAMILY["discord"]
        ledger = _ledger_with(("write_file", "/src/app.py", True))
        assert ledger.has_mutation_in_family(family) is False


# ── Tests : has_mutation_for_target_hint ─────────────────────────────────────

class TestHasMutationForTargetHint:
    def test_true_when_hint_in_target(self):
        ledger = _ledger_with(("discord_send", "general", True))
        assert ledger.has_mutation_for_target_hint("general") is True

    def test_case_insensitive(self):
        ledger = _ledger_with(("write_file", "/src/Main.py", True))
        assert ledger.has_mutation_for_target_hint("main.py") is True

    def test_partial_match(self):
        ledger = _ledger_with(("write_file", "/project/src/utils.py", True))
        assert ledger.has_mutation_for_target_hint("utils") is True

    def test_false_when_no_match(self):
        ledger = _ledger_with(("write_file", "/src/app.py", True))
        assert ledger.has_mutation_for_target_hint("general") is False

    def test_false_when_failed_mutation(self):
        ledger = _ledger_with(("discord_send", "general", False))
        assert ledger.has_mutation_for_target_hint("general") is False

    def test_empty_hint_returns_true(self):
        # Conservateur : hint vide → pas de signal → on ne bloque pas
        ledger = _ledger_with(("write_file", "/src/app.py", True))
        assert ledger.has_mutation_for_target_hint("") is True

    def test_short_hint_returns_true(self):
        # Hint trop court (1 char) → pas de signal fiable → True
        ledger = ExecutionLedger()
        assert ledger.has_mutation_for_target_hint("x") is True

    def test_false_when_ledger_empty_with_valid_hint(self):
        ledger = ExecutionLedger()
        assert ledger.has_mutation_for_target_hint("general") is False

    def test_hash_stripped_from_hint(self):
        # La méthode strip "#" avant comparaison
        ledger = _ledger_with(("discord_send", "general", True))
        assert ledger.has_mutation_for_target_hint("#general") is True


# ── Tests : INTENT_TO_MUTATION_FAMILY structure ───────────────────────────────

class TestIntentToMutationFamily:
    def test_all_intents_present(self):
        expected_keys = {"discord", "code_edit", "create_project", "file_ops"}
        assert expected_keys <= set(INTENT_TO_MUTATION_FAMILY.keys())

    def test_families_are_frozensets(self):
        for key, val in INTENT_TO_MUTATION_FAMILY.items():
            assert isinstance(val, frozenset), f"La famille '{key}' n'est pas un frozenset"

    def test_families_non_empty(self):
        for key, val in INTENT_TO_MUTATION_FAMILY.items():
            assert len(val) > 0, f"La famille '{key}' est vide"

    def test_family_tools_subset_of_mutation_tools(self):
        # Tous les outils dans les familles doivent être des mutation tools (ou proches)
        # On vérifie uniquement les outils de base — discord tools ne sont pas tous dans MUTATION_TOOLS
        code_family = INTENT_TO_MUTATION_FAMILY["code_edit"]
        for tool in code_family:
            assert tool in MUTATION_TOOLS, f"'{tool}' dans code_edit family mais pas dans MUTATION_TOOLS"

    def test_discord_family_contains_send(self):
        assert "discord_send" in INTENT_TO_MUTATION_FAMILY["discord"]
        assert "discord_send_message" in INTENT_TO_MUTATION_FAMILY["discord"]

    def test_code_edit_family_contains_write_and_edit(self):
        family = INTENT_TO_MUTATION_FAMILY["code_edit"]
        assert "write_file" in family
        assert "edit_file" in family
        assert "apply_patch" in family


# ── Tests : intégration ledger + intent family ────────────────────────────────

class TestLedgerIntentFamilyIntegration:
    def test_discord_task_correct_family(self):
        """Un ledger avec discord_send satisfait la famille discord."""
        ledger = _ledger_with(
            ("discord_send", "#annonces", True),
            ("write_file", "/tmp/log.txt", True),  # mutation hors famille
        )
        assert ledger.has_mutation_in_family(INTENT_TO_MUTATION_FAMILY["discord"]) is True

    def test_code_edit_task_wrong_family(self):
        """Un ledger avec seulement discord_send ne satisfait pas code_edit."""
        ledger = _ledger_with(("discord_send", "#annonces", True))
        assert ledger.has_mutation_in_family(INTENT_TO_MUTATION_FAMILY["code_edit"]) is False

    def test_only_failed_mutations_do_not_satisfy_family(self):
        ledger = _ledger_with(
            ("write_file", "/src/app.py", False),  # échec
            ("edit_file", "/src/utils.py", False),  # échec
        )
        assert ledger.has_mutation_in_family(INTENT_TO_MUTATION_FAMILY["code_edit"]) is False

    def test_target_hint_discord_channel(self):
        """Une mutation pour #general satisfait le hint 'general'."""
        ledger = _ledger_with(("discord_send", "general", True))
        assert ledger.has_mutation_for_target_hint("general") is True

    def test_target_hint_file_path(self):
        """Une mutation pour /src/main.py satisfait le hint 'main.py'."""
        ledger = _ledger_with(("edit_file", "/project/src/main.py", True))
        assert ledger.has_mutation_for_target_hint("main.py") is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
