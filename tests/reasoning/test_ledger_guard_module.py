"""Garde-fou structurel + unitaires — extraction ledger_guard.py (Phase 2).

Pattern « decision-core » : les fonctions sont PURES (la coquille de contrôle —
flags, history.pop, _finish_iteration, continue — reste dans react). On vérifie :
- module autonome (aucun import react → pas de cycle) ;
- re-export identité via react ;
- chaque fonction de décision bloque/passe selon les bons booléens.
"""
import ast
from pathlib import Path
from types import SimpleNamespace

import src.reasoning.ledger_guard as lg
import src.reasoning.react as r

_PUBLIC = [
    "_LEDGER_CLAIM_PATTERNS", "ledger_text_claims_action",
    "compute_effective_successful_tools", "extract_h3_target_hint",
    "ledger_final_guard_query", "ledger_h2_guard_query", "ledger_h3_guard_query",
]


def test_module_auto_contenu_pas_de_cycle():
    tree = ast.parse(Path(lg.__file__).read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not [m for m in imported if "react" in m], f"cycle: {imported}"
    assert imported <= {"re", "typing", "__future__"}, f"imports inattendus: {imported}"


def test_react_reexporte_les_memes_objets():
    for s in _PUBLIC:
        assert hasattr(r, s) and getattr(r, s) is getattr(lg, s), s


# ── ledger_text_claims_action ───────────────────────────────────────────────

def test_claims_action_detecte_et_ignore():
    assert lg.ledger_text_claims_action("c'est fait") is True
    assert lg.ledger_text_claims_action("a été installé sur le système") is True
    assert lg.ledger_text_claims_action("voici les résultats de ma lecture") is False


# ── compute_effective_successful_tools (déplie parallel_tools) ───────────────

def _step(tool_name, success=True, sub_results=None):
    obs = SimpleNamespace(success=success, sub_results=sub_results or ())
    act = SimpleNamespace(tool_name=tool_name)
    return SimpleNamespace(action=act, observation=obs)


def test_eff_tools_basique_et_echec_ignore():
    hist = [_step("write_file"), _step("read_file", success=False)]
    assert lg.compute_effective_successful_tools(hist) == ["write_file"]


def test_eff_tools_deplie_parallel():
    subs = (SimpleNamespace(success=True, tool_name="mail_send"),
            SimpleNamespace(success=False, tool_name="discord_send"))
    hist = [_step("parallel_tools", sub_results=subs)]
    assert lg.compute_effective_successful_tools(hist) == ["mail_send"]


def test_eff_tools_parallel_sans_sub_ignore_agregateur():
    hist = [_step("parallel_tools", sub_results=())]
    assert lg.compute_effective_successful_tools(hist) == []


# ── extract_h3_target_hint ──────────────────────────────────────────────────

def test_h3_hint_channel_puis_fichier_puis_none():
    assert lg.extract_h3_target_hint("poste dans #general stp") == "general"
    assert lg.extract_h3_target_hint("corrige main.py") == "main.py"
    assert lg.extract_h3_target_hint("fais un résumé") is None


# ── 3 décisions pures ───────────────────────────────────────────────────────

_BASE = dict(original_query="o", led_tools=["x"])


def test_final_guard_bloque_si_claim_sans_mutation():
    q = lg.ledger_final_guard_query(
        claims_action=True, runtime_claim=False, has_any_mutation=False,
        readonly_exoneration=False, real_action_done=False, **_BASE)
    assert q is not None and "AUCUNE mutation" in q


def test_final_guard_passe_si_mutation_ou_action_reelle_ou_exonere():
    common = dict(claims_action=True, runtime_claim=False, **_BASE)
    assert lg.ledger_final_guard_query(has_any_mutation=True, readonly_exoneration=False, real_action_done=False, **common) is None
    assert lg.ledger_final_guard_query(has_any_mutation=False, readonly_exoneration=False, real_action_done=True, **common) is None
    assert lg.ledger_final_guard_query(has_any_mutation=False, readonly_exoneration=True, real_action_done=False, **common) is None


def test_final_guard_passe_si_pas_de_claim_ou_runtime_proof():
    assert lg.ledger_final_guard_query(claims_action=False, runtime_claim=False, has_any_mutation=False, readonly_exoneration=False, real_action_done=False, **_BASE) is None
    assert lg.ledger_final_guard_query(claims_action=True, runtime_claim=True, has_any_mutation=False, readonly_exoneration=False, real_action_done=False, **_BASE) is None


def test_h2_bloque_hors_famille_attendue():
    q = lg.ledger_h2_guard_query(
        claims_action=True, runtime_claim=False, has_any_mutation=True,
        expected_family_nonempty=True, has_mutation_in_expected_family=False,
        guard_intent="discord", **_BASE)
    assert q is not None and "discord" in q


def test_h2_passe_si_mutation_dans_famille_ou_famille_vide():
    common = dict(claims_action=True, runtime_claim=False, has_any_mutation=True,
                  guard_intent="discord", **_BASE)
    assert lg.ledger_h2_guard_query(expected_family_nonempty=True, has_mutation_in_expected_family=True, **common) is None
    assert lg.ledger_h2_guard_query(expected_family_nonempty=False, has_mutation_in_expected_family=False, **common) is None


def test_h3_bloque_cible_sans_mutation():
    q = lg.ledger_h3_guard_query(
        claims_action=True, runtime_claim=False, has_any_mutation=True,
        target_hint="general", has_mutation_for_target=False, **_BASE)
    assert q is not None and "general" in q


def test_h3_passe_si_cible_traitee_ou_absente():
    common = dict(claims_action=True, runtime_claim=False, has_any_mutation=True, **_BASE)
    assert lg.ledger_h3_guard_query(target_hint="general", has_mutation_for_target=True, **common) is None
    assert lg.ledger_h3_guard_query(target_hint=None, has_mutation_for_target=False, **common) is None
