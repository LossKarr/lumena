"""A5 (Phase A, run FitLog) — la preuve au LEDGER, sur TOUTES les voies de sortie.

Trois trous du run FitLog fermés :
  1. w_storage : « Module storage.py — livré et validé ! » avec ZÉRO écriture au
     ledger (storage.py resté stub) → verrou « claim sans mutation » ;
  2. w_tests : conclu SANS pytest — gate éteint par le plafond d'itérations (les
     réparations thought-leak/tronqué avaient consommé les iters) → bannière
     déterministe « tests présents non exécutés » AU CHOKEPOINT ;
  3. w_storage avait brûlé la relance UNIQUE du gate sur un FINAL d'avant-travail
     → compteur à 2 tirs.
"""
from __future__ import annotations

import inspect

from src.reasoning.final_guards import (
    apply_mission_truth_lock,
    claims_artifact_delivery,
)


# ── claims_artifact_delivery ─────────────────────────────────────────────────────

def test_delivery_claims_positive():
    assert claims_artifact_delivery("Module storage.py — livré et validé !")
    assert claims_artifact_delivery("L'API est implémentée conformément au contrat")
    assert claims_artifact_delivery("Fonctionnalité développée et testée")
    assert claims_artifact_delivery("3 fichiers créés dans le dossier mission")
    assert claims_artifact_delivery("Le module est prêt à être utilisé par stats.py")


def test_delivery_claims_negative():
    assert not claims_artifact_delivery("Le module n'est pas implémenté")
    assert not claims_artifact_delivery("storage.py est non implémenté (stub)")
    assert not claims_artifact_delivery("raise NotImplementedError('TODO worker')")
    assert not claims_artifact_delivery("Je vais analyser le code existant")
    assert not claims_artifact_delivery("La livraison sera faite plus tard")


# ── verrou « claim sans mutation » ───────────────────────────────────────────────

_W_STORAGE_FINAL = (
    "✅ **Module `storage.py` - livré et validé !**\n\n"
    "**Tests effectués ✅ :**\n- Création de 3 séances → IDs 1, 2, 3\n"
    "- Persistance → les données survivent à un re-import\n\n"
    "Le module est prêt à être utilisé par `stats.py` et `app.py`."
)


def test_w_storage_lie_downgraded():
    """Le cas FitLog figé : claim de livraison + zéro mutation → bannière."""
    out, info = apply_mission_truth_lock(
        _W_STORAGE_FINAL, has_green_test=False, has_any_mutation=False)
    assert info["changed"]
    assert "Aucune modification de fichier réalisée" in out
    assert "PAS effective" in out


def test_delivery_claim_with_real_mutations_untouched():
    out, info = apply_mission_truth_lock(
        "Module stats.py implémenté (3 fonctions remplies via edit_file).",
        has_green_test=False, has_any_mutation=True)
    assert not info["changed"]


def test_no_claim_no_mutation_untouched():
    """Worker d'analyse légitime (zéro write, zéro claim de livraison) : intact."""
    out, info = apply_mission_truth_lock(
        "Synthèse : le marché des trackers sportifs est dominé par trois acteurs.",
        has_green_test=False, has_any_mutation=False)
    assert not info["changed"]


def test_no_mutation_banner_idempotent():
    out1, _ = apply_mission_truth_lock(
        _W_STORAGE_FINAL, has_green_test=False, has_any_mutation=False)
    out2, info2 = apply_mission_truth_lock(
        out1, has_green_test=False, has_any_mutation=False)
    assert out2 == out1
    assert info2.get("already_locked")


# ── bannière « tests présents non exécutés » ─────────────────────────────────────

def test_tests_present_not_run_banner():
    out, info = apply_mission_truth_lock(
        "Frontend terminé : index.html, style.css et app.js sont en place.",
        has_green_test=False, tests_present_not_run=True)
    assert info["changed"]
    assert "présents mais NON exécutés dans ce run" in out


def test_tests_not_run_banner_suppressed_when_tests_ran():
    """Un pytest a réellement tourné (même rouge) → la bannière « non exécutés »
    serait fausse : supprimée. B0.4b : c'est le statut honnête « NON certifiés
    verts » (chiffres réels) qui prend le relais — déterministe, hors regex."""
    out, info = apply_mission_truth_lock(
        "Frontend terminé.", has_green_test=False, tests_present_not_run=True,
        last_test_outcome={"is_test_cmd": True, "passed": 1, "failed": 2, "errors": 0})
    assert "présents mais NON exécutés" not in out  # bannière A5 bien supprimée
    assert info.get("tests_not_green_note")          # relais B0.4b
    assert "Tests NON certifiés verts" in out
    assert "1 passed, 2 failed" in out


def test_no_duplicate_with_tests_overclaim():
    """Over-claim « tests verts » + tests présents non lancés → UNE seule info
    tests (la bannière over-claim), pas de doublon « non exécutés »."""
    out, info = apply_mission_truth_lock(
        "Les 8 tests pytest sont verts, tout est validé.",
        has_green_test=False, tests_present_not_run=True)
    assert info["changed"]
    assert out.count("NON exécutés dans ce run") == 0  # pas la bannière A5
    assert "Tests non exécutés** —" in out or "Tests NON certifiés verts" in out


def test_combined_delivery_and_tests_banners():
    """Le pire cas : mensonge de livraison + tests jamais lancés → les deux dits."""
    out, info = apply_mission_truth_lock(
        "Module livré, prêt à être utilisé.",
        has_green_test=False, has_any_mutation=False, tests_present_not_run=True)
    assert info["changed"]
    assert "Aucune modification de fichier réalisée" in out
    assert "présents mais NON exécutés dans ce run" in out


def test_defaults_are_inert():
    """Aucun appelant existant ne change de comportement (défauts neutres)."""
    out, info = apply_mission_truth_lock(
        "Rapport final : travail terminé proprement.", has_green_test=False)
    assert not info["changed"]


# ── structurels : chokepoint + gate à 2 tirs ─────────────────────────────────────

def _react_src() -> str:
    import src.reasoning.react as react_mod
    return inspect.getsource(react_mod)


def test_chokepoint_passes_ledger_proofs():
    src = _react_src()
    i = src.find("def _stream_and_return_final")
    block = src[i:i + 3000]
    assert "tests_present_not_run=self._tests_present_but_not_run()" in block
    assert "has_any_mutation=self.execution_ledger.has_any_mutation()" in block


def test_gate_uses_bounded_counter():
    """L'invariant : le gate est BORNÉ par compteur, jamais de boucle infinie.
    2.13.D : plafond extérieur 4 (tirs 3-4 = branche budget-aware rouge) ; la
    branche « aucun test lancé » garde ses 2 tirs historiques."""
    src = _react_src()
    assert "_pytest_gate_shots" in src
    i = src.find("_gate_shots = getattr(self, \"_pytest_gate_shots\", 0)")
    assert i > 0
    block = src[i:i + 1200]
    assert "_gate_shots < 4" in block   # 2.13.D — plafond dur global
    assert "_gate_shots < 2" in block   # branche no-test inchangée
    # La branche budget-aware passe par le helper pur (décision testée à part).
    assert "pytest_gate_extra_shot_allowed" in src


def test_helper_tests_present_but_not_run_exists():
    from src.reasoning.react import ReActLoop
    assert hasattr(ReActLoop, "_tests_present_but_not_run")
