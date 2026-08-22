"""LOT Z3b — une étape marquée FAITE par un outil qui ne l'a pas faite.

Run Sentinelle (2026-08-15). Le plan du lead s'est crédité tout seul :

    'Vérifier : filtre masquer serveurs OK'   marquée via browser_navigate
    'Publier et rapporter'                    marquée via browser_evaluate

La case n'avait jamais été cochée, et rien n'était publié.

**CORRIGÉ ICI : la publication.** `publish_task_blocks` exigeait un mot de
contexte (« livrable », « workspace ») absent de « Publier et rapporter ». Or le
contexte d'une étape de plan vit dans la MISSION, pas dans son intitulé — un lead
qui écrit « Publier et rapporter » parle évidemment du livrable. Les mots de
publication EXTERNE restent la seule échappatoire, et ce sont bien eux qui
portaient le risque de faux positif.

**NON CORRIGÉ, ET ASSUMÉ : la vérification par navigation.** J'ai tenté deux fois
et j'ai eu tort deux fois.

1. Allonger les listes de vocabulaire (« filtre », « tri », « bouton »…) :
   « trier les résultats du benchmark » via `run_command` s'est retrouvé bloqué à
   tort. Ces mots vivent aussi hors interface. Empiler du vocabulaire ne devine
   pas une intention.
2. Poser « naviguer n'est pas vérifier » : deux tests l'ont réfutée, et ils
   avaient raison. `test_browser_dom_state_matches_verifier` — lire le DOM PROUVE
   qu'un compte a été créé. `test_browser_navigate_200_marks_verify` — pour
   « vérifier que le site est opérationnel », un HTTP 200 EST le constat.

Le vrai départage n'est pas dans l'intitulé ni dans le nom de l'outil, mais dans
l'OBSERVATION : « HTTP 200 » prouve qu'un site répond, il ne prouve rien d'un
filtre. Or `browser_verify_task_blocks` ne reçoit pas l'observation. Le corriger
demanderait de lui passer ce troisième argument et de reprendre tous ses appels —
hors périmètre de ce lot, et à ne faire que sur une mesure plus large qu'un cas.
"""
from __future__ import annotations

import pytest

from src.reasoning.plan_progress import (
    browser_verify_task_blocks,
    publish_task_blocks,
)


# ── les cas exacts du run ───────────────────────────────────────────────────

def test_the_publish_step_is_no_longer_credited_by_an_evaluate():
    """Rien n'était publié quand cette étape a été marquée faite."""
    assert publish_task_blocks("browser_evaluate", "Publier et rapporter") is True


@pytest.mark.parametrize(
    "outil",
    ["browser_evaluate", "browser_click_index", "browser_type",
     "browser_dom_state", "browser_get_content"],
)
def test_an_acting_or_observing_tool_still_credits(outil):
    """Lire le DOM PROUVE (« vérifier que le compte a bien été créé ») : c'est ce
    que garantit `test_browser_dom_state_matches_verifier`, cassé par ma première
    version du correctif — à juste titre."""
    assert browser_verify_task_blocks(outil, "Vérifier le tri des colonnes") is False


def test_a_navigation_task_is_still_credited_by_a_navigation():
    """Non-régression : `browser_navigate` reste légitime pour ce qu'il fait."""
    for libelle in ("Ouvrir le site en local", "Naviguer vers la page produit"):
        assert browser_verify_task_blocks("browser_navigate", libelle) is False


# ── le piège évité : ces mots existent hors interface ───────────────────────

@pytest.mark.parametrize(
    "libelle",
    [
        "Trier les resultats du benchmark",
        "Filtrer les lignes du log",
        "Lancer les tests unitaires",
        "Ecrire le parseur de log",
    ],
)
def test_non_ui_work_is_never_blocked(libelle):
    """C'est ce que la première version du correctif cassait."""
    assert browser_verify_task_blocks("run_command", libelle) is False
    assert browser_verify_task_blocks("write_file", libelle) is False


# ── publier : le contexte est dans la mission, pas dans l'intitulé ──────────

@pytest.mark.parametrize(
    "libelle",
    ["Publier et rapporter", "Publier le site", "Publier le résultat", "Publier le projet"],
)
def test_short_publish_labels_are_recognised(libelle):
    assert publish_task_blocks("browser_evaluate", libelle) is True


def test_publishing_with_the_right_tool_is_never_blocked():
    assert publish_task_blocks("publish_mission_workspace", "Publier et rapporter") is False


@pytest.mark.parametrize(
    "libelle",
    ["Publier un tweet de bilan", "Publier l'article sur LinkedIn", "Publier par email"],
)
def test_external_publishing_stays_out_of_scope(libelle):
    """Le seul vrai risque de faux positif — et il reste couvert."""
    assert publish_task_blocks("post_tweet", libelle) is False


def test_a_task_without_publishing_is_untouched():
    assert publish_task_blocks("write_file", "Ecrire le parseur") is False
