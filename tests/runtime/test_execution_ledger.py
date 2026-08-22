"""
🧪 Tests — ExecutionLedger (V1)

Couvre :
- LedgerEntry dataclass
- append / read / query API
- _extract_target et _extract_proof helpers
- has_any_mutation / successful_mutations / snapshot
- clear / reset entre runs
- Intégration ReActLoop (attribut présent)
"""

import pytest
from time import perf_counter

from src.runtime.execution_ledger import (
    ExecutionLedger,
    LedgerEntry,
    MUTATION_TOOLS,
    _extract_target,
    _extract_proof,
)


# ── LedgerEntry ──────────────────────────────────────────────────────────────

class TestLedgerEntry:
    def test_frozen(self):
        entry = LedgerEntry(
            iteration=0, action="write_file", target="/tmp/a.txt",
            success=True, proof="✅ Fichier écrit", timestamp=perf_counter(),
        )
        with pytest.raises(AttributeError):
            entry.success = False  # type: ignore[misc]

    def test_to_dict(self):
        entry = LedgerEntry(
            iteration=1, action="discord_send", target="#general",
            success=True, proof=None, timestamp=1.0, meta={"k": "v"},
        )
        d = entry.to_dict()
        assert d["iteration"] == 1
        assert d["action"] == "discord_send"
        assert d["target"] == "#general"
        assert d["meta"] == {"k": "v"}

    def test_default_meta(self):
        entry = LedgerEntry(
            iteration=0, action="x", target=None,
            success=False, proof=None, timestamp=0.0,
        )
        assert entry.meta == {}


# ── ExecutionLedger API ──────────────────────────────────────────────────────

class TestExecutionLedger:
    def test_append_and_size(self):
        led = ExecutionLedger()
        assert led.size == 0
        led.append(iteration=0, action="read_file", success=True)
        assert led.size == 1

    def test_recent(self):
        led = ExecutionLedger()
        for i in range(20):
            led.append(iteration=i, action=f"tool_{i}", success=True)
        recent = led.recent(5)
        assert len(recent) == 5
        assert recent[0].action == "tool_15"
        assert recent[-1].action == "tool_19"

    def test_latest_for_target(self):
        led = ExecutionLedger()
        led.append(iteration=0, action="write_file", target="/a.txt", success=True)
        led.append(iteration=1, action="edit_file", target="/a.txt", success=True)
        led.append(iteration=2, action="write_file", target="/b.txt", success=True)
        latest_a = led.latest_for_target("/a.txt")
        assert latest_a is not None
        assert latest_a.action == "edit_file"
        assert latest_a.iteration == 1

    def test_latest_for_target_case_insensitive(self):
        led = ExecutionLedger()
        led.append(iteration=0, action="write_file", target="/Workspace/File.TXT", success=True)
        result = led.latest_for_target("/workspace/file.txt")
        assert result is not None

    def test_latest_for_target_not_found(self):
        led = ExecutionLedger()
        led.append(iteration=0, action="read_file", target="/a.txt", success=True)
        assert led.latest_for_target("/missing.txt") is None

    def test_has_successful_action(self):
        led = ExecutionLedger()
        led.append(iteration=0, action="write_file", success=False)
        assert not led.has_successful_action("write_file")
        led.append(iteration=1, action="write_file", success=True)
        assert led.has_successful_action("write_file")

    def test_has_any_mutation(self):
        led = ExecutionLedger()
        # Lecture seule
        led.append(iteration=0, action="read_file", success=True)
        assert not led.has_any_mutation()
        # Mutation échouée
        led.append(iteration=1, action="write_file", success=False)
        assert not led.has_any_mutation()
        # Mutation réussie
        led.append(iteration=2, action="write_file", success=True)
        assert led.has_any_mutation()

    def test_successful_mutations(self):
        led = ExecutionLedger()
        led.append(iteration=0, action="read_file", success=True)
        led.append(iteration=1, action="write_file", success=True, target="/a.txt")
        led.append(iteration=2, action="edit_file", success=False, target="/b.txt")
        led.append(iteration=3, action="discord_send", success=True, target="#general")
        muts = led.successful_mutations()
        assert len(muts) == 2
        assert muts[0].action == "write_file"
        assert muts[1].action == "discord_send"

    def test_successful_actions_dedup(self):
        led = ExecutionLedger()
        led.append(iteration=0, action="read_file", success=True)
        led.append(iteration=1, action="read_file", success=True)
        led.append(iteration=2, action="write_file", success=True)
        actions = led.successful_actions()
        assert actions == ["read_file", "write_file"]

    def test_snapshot(self):
        led = ExecutionLedger()
        led.append(iteration=0, action="write_file", success=True, target="/x.txt")
        snap = led.snapshot()
        assert snap["total_entries"] == 1
        assert snap["successful_mutations"] == 1
        assert len(snap["entries"]) == 1
        assert snap["entries"][0]["action"] == "write_file"

    def test_summary_empty(self):
        led = ExecutionLedger()
        assert "vide" in led.summary()

    def test_summary_with_entries(self):
        led = ExecutionLedger()
        led.append(iteration=0, action="write_file", success=True, target="/a.txt",
                    proof="✅ Fichier écrit: /a.txt (10 lignes)")
        s = led.summary()
        assert "write_file" in s
        assert "1 mutations" in s

    def test_clear(self):
        led = ExecutionLedger()
        led.append(iteration=0, action="write_file", success=True)
        assert led.size == 1
        led.clear()
        assert led.size == 0
        assert not led.has_any_mutation()

    def test_green_test_proof_becomes_stale_after_source_mutation(self):
        led = ExecutionLedger()
        green = {"is_test_cmd": True, "green": True, "passed": 3, "failed": 0}
        led.append(iteration=1, action="run_command", target="workspace/app", success=True,
                   meta={"test_outcome": green})
        assert led.has_fresh_green_test_run() is True
        led.append(iteration=2, action="edit_file", target="workspace/app/app.py", success=True)
        assert led.has_green_test_run() is True
        assert led.has_fresh_green_test_run() is False
        led.append(iteration=3, action="run_command", target="workspace/app", success=True,
                   meta={"test_outcome": green})
        assert led.has_fresh_green_test_run() is True

    def test_browser_proof_becomes_stale_after_source_mutation(self):
        led = ExecutionLedger()
        led.append(iteration=1, action="browser_navigate", target="http://localhost", success=True)
        assert led.has_fresh_browser_action() is True
        led.append(iteration=2, action="write_file", target="static/app.js", success=True)
        assert led.has_browser_action() is True
        assert led.has_fresh_browser_action() is False
        led.append(iteration=3, action="browser_evaluate", target="http://localhost", success=True)
        assert led.has_fresh_browser_action() is True

    def test_document_mutation_does_not_stale_code_proofs(self):
        led = ExecutionLedger()
        green = {"is_test_cmd": True, "green": True, "passed": 1, "failed": 0}
        led.append(iteration=1, action="run_command", success=True, meta={"test_outcome": green})
        led.append(iteration=2, action="write_file", target="README.md", success=True)
        assert led.has_fresh_green_test_run() is True


# ── _extract_target ──────────────────────────────────────────────────────────

class TestExtractTarget:
    def test_file_path(self):
        assert _extract_target("write_file", {"path": "/workspace/a.txt"}) == "/workspace/a.txt"

    def test_file_path_key(self):
        assert _extract_target("edit_file", {"file_path": "/b.py"}) == "/b.py"

    def test_discord_channel(self):
        assert _extract_target("discord_send", {"channel_name": "general"}) == "general"

    def test_mail(self):
        assert _extract_target("mail_send", {"to": "a@b.com"}) == "a@b.com"

    def test_run_command_cwd(self):
        assert _extract_target("run_command", {"cwd": "/workspace/proj"}) == "/workspace/proj"

    def test_delegate_task(self):
        result = _extract_target("delegate_task", {"description": "Build the API endpoint for users"})
        assert result == "Build the API endpoint for users"

    def test_empty_args(self):
        assert _extract_target("write_file", {}) is None

    def test_non_dict_args(self):
        assert _extract_target("write_file", "not a dict") is None  # type: ignore


# ── _extract_proof ───────────────────────────────────────────────────────────

class TestExtractProof:
    def test_write_file_success(self):
        proof = _extract_proof("write_file", "✅ Fichier écrit: /a.txt (10 lignes)", True)
        assert proof is not None
        assert "écrit" in proof.lower() or "✅" in proof

    def test_write_file_failure(self):
        assert _extract_proof("write_file", "❌ Erreur", False) is None

    def test_discord_send_success(self):
        proof = _extract_proof("discord_send", "✅ Message envoyé dans #general", True)
        assert proof is not None
        assert "envoyé" in proof.lower()

    def test_unknown_tool(self):
        assert _extract_proof("some_tool", "ok", True) is None

    def test_empty_observation(self):
        assert _extract_proof("write_file", "", True) is None


# ── MUTATION_TOOLS set ───────────────────────────────────────────────────────

class TestMutationTools:
    def test_write_tools_present(self):
        assert "write_file" in MUTATION_TOOLS
        assert "edit_file" in MUTATION_TOOLS
        assert "discord_send" in MUTATION_TOOLS
        assert "delegate_task" in MUTATION_TOOLS

    def test_apply_patches_present(self):
        """apply_patches (batch) doit être reconnu comme mutation — sinon ledger guard faux positif."""
        assert "apply_patches" in MUTATION_TOOLS
        assert "apply_patch" in MUTATION_TOOLS  # le singulier également

    def test_read_tools_absent(self):
        assert "read_file" not in MUTATION_TOOLS
        assert "list_directory" not in MUTATION_TOOLS
        assert "web_search" not in MUTATION_TOOLS


# ── apply_patches — couverture complète ─────────────────────────────────────

class TestApplyPatchesMutationRecognition:
    """apply_patches est un outil batch distinct de apply_patch (singulier).
    Il doit être reconnu comme mutation à tous les niveaux du ledger."""

    def test_has_any_mutation_after_apply_patches(self):
        led = ExecutionLedger()
        led.append(iteration=0, action="read_file", success=True)
        assert not led.has_any_mutation()
        led.append(iteration=1, action="apply_patches", success=True)
        assert led.has_any_mutation()

    def test_apply_patches_failure_not_counted(self):
        led = ExecutionLedger()
        led.append(iteration=0, action="apply_patches", success=False)
        assert not led.has_any_mutation()

    def test_extract_target_from_patches_list(self):
        args = {"patches": [{"file": "/project/app.py", "old": "x = 1", "new": "x = 2"}]}
        target = _extract_target("apply_patches", args)
        assert target == "/project/app.py"

    def test_extract_target_apply_patches_empty_list(self):
        assert _extract_target("apply_patches", {"patches": []}) is None

    def test_extract_target_apply_patches_no_patches_key(self):
        assert _extract_target("apply_patches", {}) is None

    def test_extract_proof_apply_patches_success(self):
        obs = "✅ apply_patches: 3 patch(es) appliqué(s) sur 2 fichier(s)"
        proof = _extract_proof("apply_patches", obs, True)
        assert proof is not None
        assert "apply_patches" in proof.lower() or "✅" in proof

    def test_extract_proof_apply_patches_failure(self):
        assert _extract_proof("apply_patches", "❌ Rollback effectué", False) is None

    def test_successful_mutations_includes_apply_patches(self):
        led = ExecutionLedger()
        led.append(iteration=0, action="apply_patches", success=True,
                   target="/project/style.css",
                   proof="✅ apply_patches: 2 patch(es)")
        muts = led.successful_mutations()
        assert len(muts) == 1
        assert muts[0].action == "apply_patches"


# ── Skills (Phase 1 anti-fabrication) — mutations vérifiables + cible ────────

class TestSkillMutationsLedger:
    """update_skill / delete_skill sont des mutations vérifiables, et leur cible
    est le NOM du skill (args `name`), pas un path."""

    def test_skill_tools_in_mutation_tools(self):
        assert "create_skill" in MUTATION_TOOLS
        assert "update_skill" in MUTATION_TOOLS
        assert "delete_skill" in MUTATION_TOOLS

    def test_has_any_mutation_after_update_skill(self):
        led = ExecutionLedger()
        led.append(iteration=0, action="read_skill_reference", success=True)
        assert not led.has_any_mutation()
        led.append(iteration=1, action="update_skill", success=True,
                   target="compte-rendu-reunion")
        assert led.has_any_mutation()

    def test_extract_target_update_skill_name(self):
        target = _extract_target(
            "update_skill", {"name": "compte-rendu-reunion", "content": "..."}
        )
        assert target == "compte-rendu-reunion"

    def test_extract_target_delete_skill_name(self):
        assert _extract_target("delete_skill", {"name": "obsolete-skill"}) == "obsolete-skill"

    def test_extract_target_create_skill_skill_name_fallback(self):
        # tolère aussi la clé `skill_name`
        assert _extract_target("create_skill", {"skill_name": "demo"}) == "demo"

    def test_extract_target_skill_no_name(self):
        assert _extract_target("update_skill", {"content": "x"}) is None

    def test_mutation_for_target_hint_on_skill(self):
        led = ExecutionLedger()
        led.append(iteration=0, action="update_skill", success=True,
                   target="compte-rendu-reunion")
        assert led.has_mutation_for_target_hint("compte-rendu") is True
        assert led.has_mutation_for_target_hint("autre-skill") is False


# ── Intégration ReActLoop ────────────────────────────────────────────────────

class TestReActLoopLedgerIntegration:
    def test_react_loop_has_ledger(self):
        from src.reasoning.react import ReActLoop
        loop = ReActLoop()
        assert hasattr(loop, 'execution_ledger')
        assert isinstance(loop.execution_ledger, ExecutionLedger)

    def test_react_loop_ledger_is_empty_on_init(self):
        from src.reasoning.react import ReActLoop
        loop = ReActLoop()
        assert loop.execution_ledger.size == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
