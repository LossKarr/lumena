"""LOT Z9 — le fait n'est plus perdu avant la décision, ni après l'action : il
l'était au moment de rendre compte.

Run « décision voiture » (2026-08-15) — le test le plus exigeant de la série,
car aucune méthode n'était imposée : seulement un objectif et un critère.
Lumena a décidé seule de travailler sans workers ET l'a justifié, choisi seule
le format (page interactive, jamais demandée), fait 20 recherches web réelles,
et produit un comparateur de 658 lignes sans aucune dépendance, sources citées.
En dix minutes. Puis :

    mission_published = None
    workspace/decision_voiture_2026/                 → n'existe pas
    missions/task_d8c25fef…/decision_voiture_2026/   → le travail est là

Le critère de la mission disait : « utilisable tout de suite, sans que j'aie à
aller chercher dans tes dossiers de travail ». C'est le seul point qu'elle a
raté — et son résumé n'en disait pas un mot.

Mesuré sur le corpus complet des missions leads ayant produit des fichiers :

    ont publié ............ 17
    n'ont PAS publié ...... 78   (dont 17 au livrable introuvable ailleurs)
    résumés qui le disent .. 0

Z9 est le jumeau de H6 (`annotate_unproven_effects`) : même patron, même point
d'appel, mais pour les FICHIERS au lieu des EFFETS.

Z9b traite la seconde moitié : le résumé livré n'était pas la conclusion du
lead — son FINAL était vide — mais le rapport recopié du CodeAgent, via la voie
H7 restée ⬜ trois semaines. Elle a fonctionné (sans elle, rien n'arrivait), mais
rien ne la distinguait d'une vraie conclusion.
"""

from pathlib import Path

import pytest

from src.subagents.runner import (
    annotate_unproven_effects,
    annotate_unpublished_deliverable,
    annotate_worker_report_fallback,
)

_RESUME = "Le CodeAgent a termine avec succes. Rapport: comparateur cree."
_CHEMIN = "workspace/missions/task_d8c25fef821e42f499a43184353f8045"


# ── Z9a — le livrable resté dans l'atelier est annoncé ───────────────────────


def test_le_cas_reel_de_la_mission_voiture():
    note = annotate_unpublished_deliverable(_RESUME, 2, _CHEMIN)
    assert "non publié" in note.lower()
    assert "2 fichier" in note
    assert _CHEMIN in note


def test_le_chemin_reel_est_donne(tmp_path):
    """Sans chemin, l'utilisateur sait qu'il a perdu quelque chose sans savoir où."""
    note = annotate_unpublished_deliverable("x", 5, "workspace/missions/task_abc")
    assert "`workspace/missions/task_abc`" in note


def test_loutil_manquant_est_nomme():
    """Le lead doit savoir QUOI appeler, pas seulement qu'il a oublié."""
    assert "publish_mission_workspace" in annotate_unpublished_deliverable("x", 1, "p")


def test_le_texte_original_est_conserve_entier():
    """Patron du truth-lock : additif en tête, jamais de réécriture."""
    note = annotate_unpublished_deliverable(_RESUME, 2, _CHEMIN)
    assert note.endswith(_RESUME)


def test_la_banniere_est_en_tete():
    note = annotate_unpublished_deliverable(_RESUME, 2, _CHEMIN)
    assert note.index("non publié") < note.index("CodeAgent")


def test_lannotation_est_idempotente():
    """Une re-clôture après reprise ne doit pas empiler les bannières."""
    une = annotate_unpublished_deliverable(_RESUME, 2, _CHEMIN)
    deux = annotate_unpublished_deliverable(une, 2, _CHEMIN)
    assert une == deux


def test_un_resume_vide_recoit_quand_meme_le_fait():
    note = annotate_unpublished_deliverable("", 3, _CHEMIN)
    assert "non publié" in note.lower()
    assert note.strip() == note.strip().lstrip()


# ── Z9a — inertie : ne rien dire quand il n'y a rien à dire ──────────────────


def test_zero_fichier_produit_aucune_banniere():
    """Une mission d'analyse ou d'effets n'a pas de livrable fichier."""
    assert annotate_unpublished_deliverable(_RESUME, 0, _CHEMIN) == _RESUME


def test_sans_chemin_aucune_banniere():
    """Appelant qui ne connaît pas le dossier → on s'abstient plutôt que deviner."""
    assert annotate_unpublished_deliverable(_RESUME, 4, "") == _RESUME
    assert annotate_unpublished_deliverable(_RESUME, 4, None) == _RESUME


@pytest.mark.parametrize("compte", [None, "", "beaucoup", -3, 0])
def test_un_compte_inexploitable_ne_leve_jamais(compte):
    """Une annotation de clôture ne doit jamais faire échouer une clôture."""
    assert annotate_unpublished_deliverable(_RESUME, compte, _CHEMIN) == _RESUME


def test_un_compte_texte_valide_est_accepte():
    assert "7 fichier" in annotate_unpublished_deliverable("x", "7", "p")


def test_la_fonction_est_pure():
    """Aucun accès disque, aucun état : appelable deux fois, même résultat."""
    a = annotate_unpublished_deliverable(_RESUME, 2, _CHEMIN)
    b = annotate_unpublished_deliverable(_RESUME, 2, _CHEMIN)
    assert a == b


# ── Z9b — la provenance du bilan ─────────────────────────────────────────────


def test_le_rapport_de_sous_agent_est_signale():
    note = annotate_worker_report_fallback(_RESUME, True)
    assert "sous-agent" in note
    assert _RESUME in note


def test_il_est_dit_que_la_mission_na_pas_conclu():
    """C'est le fait décisif : le FINAL du lead était vide."""
    assert "pas produit de réponse finale" in annotate_worker_report_fallback("x", True)


def test_les_chemins_relatifs_sont_signales():
    """Le rapport du CodeAgent cite `decision_voiture_2026/…` sans dire d'où."""
    assert "relatifs" in annotate_worker_report_fallback("x", True)


def test_un_bilan_normal_nest_pas_annote():
    assert annotate_worker_report_fallback(_RESUME, False) == _RESUME


def test_z9b_est_idempotent():
    une = annotate_worker_report_fallback(_RESUME, True)
    assert annotate_worker_report_fallback(une, True) == une


def test_z9b_ignore_un_texte_vide():
    assert annotate_worker_report_fallback("", True) == ""
    assert annotate_worker_report_fallback(None, True) == ""


# ── Les trois annotations cohabitent ─────────────────────────────────────────


def test_les_trois_bannieres_se_cumulent_sans_se_detruire():
    """H6 (effets), Z9 (fichiers), Z9b (provenance) peuvent être vraies ensemble."""
    t = annotate_unproven_effects(_RESUME, ["w_mail"])
    t = annotate_unpublished_deliverable(t, 2, _CHEMIN)
    t = annotate_worker_report_fallback(t, True)
    assert "Effet(s) non réalisé(s)" in t
    assert "non publié" in t.lower()
    assert "sous-agent" in t
    assert _RESUME in t


def test_lordre_des_annotations_ne_perd_aucun_fait():
    a = annotate_worker_report_fallback(
        annotate_unpublished_deliverable(_RESUME, 2, _CHEMIN), True
    )
    b = annotate_unpublished_deliverable(
        annotate_worker_report_fallback(_RESUME, True), 2, _CHEMIN
    )
    for t in (a, b):
        assert "non publié" in t.lower() and "sous-agent" in t and _RESUME in t


def test_le_cumul_reste_idempotent():
    t = annotate_unpublished_deliverable(_RESUME, 2, _CHEMIN)
    t = annotate_worker_report_fallback(t, True)
    encore = annotate_worker_report_fallback(
        annotate_unpublished_deliverable(t, 2, _CHEMIN), True
    )
    assert t == encore


# ── Le branchement à la clôture ──────────────────────────────────────────────


_RUNNER = Path("src/subagents/runner.py").read_text(encoding="utf-8")
_REACT = Path("src/reasoning/react.py").read_text(encoding="utf-8")


def test_la_cloture_annote_bien_la_publication():
    """Même point d'appel que H6 : juste avant `mark_done`."""
    i = _RUNNER.index("orch.mark_done(mission_id, result_summary=")
    avant = _RUNNER[i - 2500 : i]
    assert "annotate_unpublished_deliverable" in avant
    assert "annotate_unproven_effects" in avant


def test_la_cloture_annote_la_provenance():
    i = _RUNNER.index("orch.mark_done(mission_id, result_summary=")
    assert "annotate_worker_report_fallback" in _RUNNER[i - 2500 : i]


def _bloc_cloture() -> str:
    """Le SITE D'APPEL, pas la définition : le nom apparaît aussi plus haut,
    dans la fonction elle-même (piège déjà rencontré au LOT Z6)."""
    fin = _RUNNER.index("orch.mark_done(mission_id, result_summary=")
    return _RUNNER[fin - 2500 : fin]


def test_la_publication_est_jugee_sur_le_disque():
    """LOT N : le périmètre se juge sur le DISQUE, pas sur une déclaration."""
    autour = _bloc_cloture()
    assert "rglob" in autour
    assert "is_dir()" in autour
    assert 'get("mission_published")' in autour


def test_les_fichiers_de_service_ne_sont_pas_comptes():
    """Compter `CONTRAT.md` ferait dire « livrable non publié » à une mission
    qui n'a produit que son propre contrat."""
    autour = _bloc_cloture()
    assert "CONTRAT.md" in autour
    assert "__pycache__" in autour


def test_lannotation_ne_peut_pas_casser_la_cloture():
    for nom in ("annotate_unpublished_deliverable", "annotate_worker_report_fallback"):
        i = _RUNNER.index(nom, _RUNNER.index("orch.mark_done") - 2500)
        autour = _RUNNER[i - 900 : i + 500]
        assert "try:" in autour and "except Exception" in autour


def test_le_marqueur_de_fallback_est_pose_au_bon_endroit():
    """Là où le FINAL vide bascule sur le rapport du sous-agent — nulle part ailleurs."""
    i = _REACT.index("_delegate_success_fallback_message()")
    autour = _REACT[i : i + 1400]
    assert "final_from_worker_report=True" in autour
    assert "if not answer.strip():" in autour


def test_le_marqueur_nest_pas_pose_quand_le_lead_a_parle():
    """`answer.strip()` non vide = vraie conclusion → aucun marqueur."""
    i = _REACT.index("final_from_worker_report=True")
    avant = _REACT[i - 800 : i]
    assert "if not answer.strip():" in avant
