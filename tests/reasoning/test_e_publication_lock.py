"""LOT E (run FidéliBar 2026-07-04) — verrou de vérité « publication ».

Le run FidéliBar a écrit des fichiers (has_any_mutation=True), donc le verrou
« livraison sans mutation » ne tirait pas — MAIS `publish_mission_workspace`
n'a JAMAIS tourné. Le lead a pourtant annoncé « 6. Publié ✅ — dans
workspace/fidelibar/ » et « succès complet », et ce claim a atteint l'utilisateur
(le truth-lock ne scrubbait que tests/navigateur). Publier ≠ écrire des fichiers.

E : `claims_published(text) AND not ledger.has_published()` → bannière « Non publié ».
Preuve LEDGER (`publish_mission_workspace` réussi), jamais une devinette du texte.
Négations honnêtes (« non publié car tests rouges ») JAMAIS bannies.
"""
from __future__ import annotations

import src.reasoning.final_guards as fg
from src.runtime.execution_ledger import ExecutionLedger


# ── détecteur claims_published : affirmations vs aveux honnêtes ───────────────────

def test_claims_published_positive():
    assert fg.claims_published("6. Publié ✅ — dans workspace/fidelibar/")
    assert fg.claims_published("Le livrable final a été publié.")
    assert fg.claims_published("Mission déployée avec succès.")
    assert fg.claims_published("Site mis en ligne sur le port 8085.")
    assert fg.claims_published("succès complet livré")


def test_claims_published_negations_never_flag():
    # Le cas explicite de la note de revue : aveu honnête → jamais banni.
    assert not fg.claims_published("Non publié car tests rouges.")
    assert not fg.claims_published("Livrable produit mais pas publié.")
    assert not fg.claims_published("Il reste à publier le livrable.")
    assert not fg.claims_published("Pas encore publié : publication non effectuée.")
    assert not fg.claims_published("Non déployé — publication à faire.")
    assert not fg.claims_published("")


# ── has_published() sur le ledger : preuve déterministe ──────────────────────────

def test_ledger_has_published_false_without_publish():
    led = ExecutionLedger()
    led.append(iteration=1, action="write_file", target="app.py", success=True)
    led.append(iteration=2, action="run_command", target="pytest", success=True)
    assert led.has_published() is False  # écrire des fichiers ≠ publier


def test_ledger_has_published_true_after_publish():
    led = ExecutionLedger()
    led.append(iteration=1, action="publish_mission_workspace",
               target="fidelibar", success=True)
    assert led.has_published() is True


def test_ledger_has_published_false_if_publish_failed():
    led = ExecutionLedger()
    led.append(iteration=1, action="publish_mission_workspace",
               target="fidelibar", success=False)
    assert led.has_published() is False


# ── apply_mission_truth_lock : le claim « publié » sans preuve → bannière ─────────

_FIDELIBAR_FINAL = (
    "La mission FidéliBar est un succès complet.\n"
    "6. Publié ✅ — dans workspace/fidelibar/"
)


def test_publish_claim_without_proof_is_flagged():
    out, info = fg.apply_mission_truth_lock(
        _FIDELIBAR_FINAL, has_green_test=True, has_published=False)
    assert info["changed"] is True
    assert info["overclaim_published"] is True
    assert "Non publié" in out
    assert "publish_mission_workspace" in out


def test_publish_claim_with_proof_passes_untouched():
    # Mission honnête qui a VRAIMENT publié → aucune bannière.
    out, info = fg.apply_mission_truth_lock(
        _FIDELIBAR_FINAL, has_green_test=True, has_published=True)
    assert info["changed"] is False
    assert out == _FIDELIBAR_FINAL


def test_publish_negation_never_downgraded():
    honest = "Livrable produit mais NON publié car les tests sont rouges."
    out, info = fg.apply_mission_truth_lock(
        honest, has_green_test=True, has_published=False)
    # Le détecteur ne s'arme pas → pas de bannière « Non publié » ajoutée.
    assert "Non publié**" not in out


def test_default_has_published_true_no_regression():
    # Les appelants qui ne passent PAS has_published (défaut True) ne voient jamais
    # la bannière publication : zéro régression sur les sites existants.
    out, info = fg.apply_mission_truth_lock(
        "Publié ✅ dans workspace/x", has_green_test=True)
    assert "Non publié**" not in out


def test_publish_banner_idempotent():
    out1, _ = fg.apply_mission_truth_lock(
        _FIDELIBAR_FINAL, has_green_test=True, has_published=False)
    out2, info2 = fg.apply_mission_truth_lock(
        out1, has_green_test=True, has_published=False)
    assert info2.get("already_locked") is True
    assert out2.count("Non publié**") == 1  # pas de double-bannière


def test_publish_and_tests_overclaim_both_banners():
    # Branche « over-claim tests » : la bannière publication est AUSSI ajoutée.
    txt = "10/10 tests verts. Publié ✅ dans workspace/x. Succès complet !"
    out, info = fg.apply_mission_truth_lock(
        txt, has_green_test=False, has_published=False)
    assert info["changed"] is True
    assert "Tests NON certifiés verts" in out or "Tests non exécutés" in out
    assert "Non publié" in out
    assert info.get("overclaim_published") is True


# ── non-régression : les verrous existants inchangés ─────────────────────────────

def test_existing_locks_untouched_when_published_ok():
    # Un over-claim navigateur reste rétrogradé même si has_published=True.
    txt = "Frontend vérifié au navigateur. Publié ✅."
    out, info = fg.apply_mission_truth_lock(
        txt, has_green_test=True, has_browser_proof=False, has_published=True)
    assert info["changed"] is True
    assert "Vérification navigateur NON prouvée" in out
    assert "Non publié**" not in out  # publish OK → pas de bannière publication
