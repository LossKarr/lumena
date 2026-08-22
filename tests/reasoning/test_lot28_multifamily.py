"""2.8 (runs multi-familles n°1, 2026-07-07/08) — les familles non-web tiennent,
la fabrication ne passe plus.

Runs révélateurs : data ✅ réel, CLI ❌ échec honnête (frottement contrat),
multi-pages ❌ FABRICATION (le lead a halluciné tout le run — pollution ChromaDB —
et seul le claim navigateur a été bannérisé, « fichiers créés » a filé).

B0bis : filtrer les souvenirs épisodiques des prompts mission ; 2.8.2 : harmoniser
les 3 sites truth-lock (has_any_mutation manquait à FINAL/FINALIZE → overclaim_
delivery mort) ; 2.8.1 : contrat CLI fluide ; 2.8.4 : junk hors publish.
"""
from __future__ import annotations

import types
from datetime import datetime
from pathlib import Path

import pytest


# ═══════════════ B0bis — mémoires épisodiques exclues en mission ═══════════════


def _mem(content, mtype, score=0.9, importance=0.5):
    from src.memory.chromadb_store import Memory
    return Memory(id=content[:8], content=content, memory_type=mtype,
                  timestamp=datetime.now(), score=score, importance=importance)


class TestB0bisEpisodicFilter:
    def _store(self, memories):
        from src.memory.chromadb_store import LumenaMemory
        m = LumenaMemory.__new__(LumenaMemory)
        m.facts = {}
        m.recall = lambda q, lim: list(memories)
        return m

    def test_episodic_excluded_in_mission(self):
        """Le récit d'un ancien run (épisodique) ne doit PAS être réinjecté."""
        store = self._store([
            _mem("[2026-07-07] TriboBlog est publié dans workspace/triboblog", "episodic"),
            _mem("Procédure : toujours poser le contrat d'abord", "procedural"),
        ])
        ctx = store.get_context_for_prompt("construis un site", exclude_episodic=True)
        assert "TriboBlog est publié" not in ctx
        assert "poser le contrat" in ctx  # le procedural reste

    def test_episodic_kept_when_not_mission(self):
        """Hors mission (chat), comportement historique : épisodique gardé."""
        store = self._store([
            _mem("[2026-07-07] TriboBlog est publié", "episodic", score=0.9),
        ])
        ctx = store.get_context_for_prompt("triboblog", exclude_episodic=False)
        assert "TriboBlog est publié" in ctx

    def test_facts_always_injected_even_in_mission(self):
        """B0bis ne touche PAS aux faits durables (identité/préférences)."""
        from src.memory.chromadb_store import LumenaMemory
        m = LumenaMemory.__new__(LumenaMemory)
        m.facts = {"user_name": "Losskarr", "formality": "tutoiement"}
        m.recall = lambda q, lim: [_mem("[run] ancien livrable", "episodic")]
        ctx = m.get_context_for_prompt("mission", exclude_episodic=True)
        assert "Losskarr" in ctx           # fait durable préservé
        assert "ancien livrable" not in ctx  # récit de run filtré


# ═══════════════ 2.8.2 — les 3 sites truth-lock harmonisés ═════════════════════

# Final TriboBlog quasi-verbatim : fabrication (site jamais construit).
_TRIBO_FINAL = (
    "Voilà Losskarr, **TriboBlog est construit et vérifié** ✅\n\n"
    "**📁 Fichiers créés** dans workspace/triboblog/ :\n"
    "- index.html — page d'accueil\n- article1.html\n- style.css\n\n"
    "**🔍 Vérification navigateur** : Accueil visible, clic article1 OK"
)


class TestTruthLockCatchesFabricatedDelivery:
    def test_no_mutation_fires_delivery_banner(self):
        """Zéro mutation + « fichiers créés » → bannière (le cas TriboBlog)."""
        from src.reasoning.final_guards import apply_mission_truth_lock
        out, info = apply_mission_truth_lock(
            _TRIBO_FINAL,
            has_green_test=False, has_browser_proof=False,
            has_any_mutation=False, has_published=False,
            web_deliverable=True, has_server_started=False,
        )
        assert info.get("overclaim_delivery") is True
        assert info.get("overclaim_browser") is True
        assert "Aucune modification de fichier" in out

    def test_all_three_sites_pass_full_params(self):
        """Garde-fou anti-régression : les 3 appels react passent le jeu complet
        (has_any_mutation manquait à FINAL/FINALIZE → overclaim_delivery mort)."""
        import re
        src = Path("src/reasoning/react.py").read_text(encoding="utf-8").splitlines()
        sites = []
        for i, l in enumerate(src):
            if "apply_mission_truth_lock(" in l:
                # LOT 2.11.E : le param project_root + son commentaire ont allongé
                # le bloc d'appel ; fenêtre élargie (les 3 sites sont à des milliers
                # de lignes d'écart → aucun risque de fuite d'un param voisin).
                block = "\n".join(src[i:i + 24])
                sites.append(all(p in block for p in (
                    "has_any_mutation", "tests_present_not_run",
                    "web_deliverable", "has_published")))
        assert len(sites) == 3, f"attendu 3 sites, trouvé {len(sites)}"
        assert all(sites), "un site truth-lock ne passe pas le jeu complet"


# ═══════════════ 2.8.1 — contrat CLI fluide ════════════════════════════════════


class TestContractCliFriction:
    def test_double_owner_message_is_guiding(self):
        from src.subagents.mission_contract import validate_contract
        errs = validate_contract({"project": "x", "files": [
            {"path": "cli.py", "owner": "a", "exports": ["def f() -> int"]},
            {"path": "cli.py", "owner": "b", "desc": "entry"},
        ]})
        assert any("UN SEUL owner" in e for e in errs)

    def test_cli_entry_point_suggests_no_public_api(self):
        from src.subagents.mission_contract import validate_contract
        errs = validate_contract({"project": "x", "files": [
            {"path": "cli.py", "owner": "b", "desc": "point d'entrée"},
        ]})
        assert any("no_public_api" in e and "CLI" in e for e in errs)

    def test_cli_contract_valid_with_no_public_api(self):
        from src.subagents.mission_contract import validate_contract
        errs = validate_contract({"project": "motcompteur", "files": [
            {"path": "core.py", "owner": "w_core",
             "exports": ["def count_words(text: str) -> dict"]},
            {"path": "cli.py", "owner": "w_cli", "desc": "entrée",
             "no_public_api": True},
            {"path": "tests/test_cli.py", "owner": "w_tests",
             "exports": ["def test_count()"]},
        ]})
        assert errs == []

    def test_write_mission_contract_exempt_from_escalation(self):
        """Anti-régression : le compteur d'escalade ignore write_mission_contract
        (l'escalade CodeAgent l'avait tué au run MotCompteur)."""
        src = Path("src/reasoning/react.py").read_text(encoding="utf-8")
        assert "_ESCALATION_EXEMPT" in src
        assert '"write_mission_contract"' in src.split("_ESCALATION_EXEMPT")[1][:120]


# ═══════════════ 2.8.4 — junk 0 octet hors contrat exclu de publish ════════════


class TestPublishExcludesJunk:
    def _ctx(self, tmp_path):
        from src.runtime.task_orchestrator import TaskOrchestrator
        from src.tools.file_guardrails import WorkspaceFileGuardrails
        orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
        core = types.SimpleNamespace(task_orchestrator=orch)
        fg = WorkspaceFileGuardrails(tmp_path)
        ctx = types.SimpleNamespace(lumena=core, runtime_task_id=None, file_guardrails=fg)
        return ctx, orch

    @pytest.mark.asyncio
    async def test_zero_byte_junk_not_published(self, tmp_path):
        from src.reasoning.handlers import missions as M
        ctx, orch = self._ctx(tmp_path)
        lead = orch.start_task(conversation_id="__missions__", channel="mission",
                               message_preview="lead",
                               metadata={"kind": "mission", "depth": 1})
        ctx.runtime_task_id = lead.task_id
        d = tmp_path / "missions" / lead.task_id
        d.mkdir(parents=True, exist_ok=True)
        (d / "contract.json").write_text(
            '{"project":"vr","files":[{"path":"analyse.py","owner":"w"}]}', encoding="utf-8")
        (d / "analyse.py").write_text("def resume(p): return {}\n", encoding="utf-8")
        (d / "rapport.txt").write_text("Total: 42\n", encoding="utf-8")   # sortie légitime
        (d / "test_log_fixes.py").write_text("", encoding="utf-8")        # JUNK 0 octet
        (d / "__init__.py").write_text("", encoding="utf-8")              # 0 octet légitime

        r = await M.publish_mission_workspace_handler(ctx, target="vr")
        assert r.success, r.output
        dest = tmp_path / "vr"
        assert (dest / "analyse.py").is_file()
        assert (dest / "rapport.txt").is_file()   # sortie avec contenu → gardée
        assert (dest / "__init__.py").is_file()   # 0 octet mais légitime → gardé
        assert not (dest / "test_log_fixes.py").exists()  # junk → exclu
        assert "test_log_fixes.py" not in r.output
