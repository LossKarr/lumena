"""LOT M — un parcours ne se prouve pas en ouvrant la première page.

Run CaveÀVin (2026-08-14). Le SaaS était bon : contrat tenu, périmètres tenus,
13 tests verts dont l'isolation A/B, publication et serveur Flask sur 8085. La
consigne disait pourtant :

    « VÉRIFIE AU NAVIGATEUR le parcours inscription → connexion → ajout d'une
      bouteille → elle apparaît dans la liste ;
      ne conclus « livré » que si ce parcours a été réellement observé. »

Ce qui s'est passé, à la seconde près :

    01:58:22  clic « S'inscrire »  → /register
    01:58:32  browser_dom_state    → 3 champs, 1 bouton
    01:58:34  [MISSION FINALIZE]   ← deux secondes plus tard

Aucun champ rempli, aucun compte créé, aucune bouteille ajoutée. Trois filets se
sont tus, chacun pour sa raison :

1. `browser_verify_task_blocks` crédite la tâche dès qu'un outil `browser_*`
   réussit — `browser_navigate` sur la page d'accueil a coché « inscription →
   connexion → ajout → liste » ;
2. `browser_interaction_task_blocks` ne s'est pas reconnu : ses marqueurs sont des
   VERBES (`ajouter`) et le plan écrit des SUBSTANTIFS (`ajout`) ;
3. surtout — `_advance_manual_browser_flow` a accordé la preuve d'interaction à un
   clic sur un LIEN. Or `_mission_completion_evidence` fait dépendre
   `browser_proven` entièrement de ce flag quand l'objectif réclame une
   interaction (vérifié : `objective_requires_web_interaction_proof` est True sur
   l'objectif CaveÀVin). Le FINALIZE est donc tombé sur une preuve vide.

Le point 3 est la cause racine ; 1 et 2 expliquent l'affichage faussement complet.

Calibrage sur les **272 descriptions de tâches réellement produites** (2353 tâches,
6 fichiers de logs) : 3 décrivent un parcours, 2 sont des vérifs navigateur — ce
sont MemoNest et CaveÀVin, les deux qui ont échoué. Le garde d'interaction passe
de 8 à 10 descriptions bloquantes : **+2 gagnées, 0 perdue.**
"""
from __future__ import annotations

import pytest

from src.reasoning.plan_progress import (
    browser_interaction_task_blocks,
    task_has_ui_context,
    task_names_a_journey,
)
from src.reasoning.react import (
    _advance_manual_browser_flow,
    _browser_click_is_link_navigation,
)

# Traces réelles du run (format exact des observations navigateur).
_CLIC_LIEN = (
    "✅ Clic sur [1] link \"S'inscrire\" a (786, 592) → navigation vers "
    "http://localhost:8085/register"
)
_CLIC_BOUTON = (
    "✅ Clic sur [1] button \"S'inscrire\" a (640, 410) → navigation vers "
    "http://localhost:8085/bottles"
)
_TACHE_CAVEAVIN = "Vérifier navigateur (inscription → connexion → ajout → liste)"
_TACHE_MEMONEST = (
    "Démarrer le serveur (port 8081-8099) et vérifier au navigateur : "
    "inscription → connexion → création note → liste"
)


# ── M1 : un clic sur un LIEN n'est pas une interaction produit ───────────────

def test_the_exact_click_that_closed_caveavin():
    """LE cas du lot : ce clic a valu « interaction prouvée »."""
    assert _browser_click_is_link_navigation("browser_click_index", _CLIC_LIEN) is True


def test_a_button_click_is_still_a_real_interaction():
    """Soumettre un formulaire redirige aussi — c'est le TYPE d'élément qui
    tranche, pas le fait de naviguer. Sinon on casserait toute preuve de
    parcours réel."""
    assert _browser_click_is_link_navigation("browser_click_index", _CLIC_BOUTON) is False


@pytest.mark.parametrize(
    "tool", ["browser_type", "browser_type_index", "browser_select", "browser_check"]
)
def test_typing_is_never_a_navigation(tool):
    assert _browser_click_is_link_navigation(tool, "✅ Saisi dans [3]") is False


def test_a_non_browser_tool_is_never_a_navigation():
    assert _browser_click_is_link_navigation("write_file", _CLIC_LIEN) is False
    assert _browser_click_is_link_navigation("", _CLIC_LIEN) is False


def test_garbage_never_raises():
    for tool in (None, "", 42, "browser_click"):
        for obs in (None, "", 42, _CLIC_LIEN):
            assert isinstance(_browser_click_is_link_navigation(tool, obs), bool)


# ── M1 branché : la preuve ne s'arme plus sur une navigation ────────────────

def test_the_caveavin_sequence_no_longer_proves_interaction():
    """Rejoué à l'identique : lecture DOM, clic sur le lien, relecture DOM
    différente. C'est exactement ce qui a posé le flag."""
    proven, fingerprint, pending = _advance_manual_browser_flow(
        "", mutation_pending=False, tool_name="browser_dom_state",
        observation="Page: CaveAVin\nInteractive elements: 2\n[1] link S'inscrire",
    )
    assert proven is False and fingerprint

    proven, fingerprint, pending = _advance_manual_browser_flow(
        fingerprint, mutation_pending=pending,
        tool_name="browser_click_index", observation=_CLIC_LIEN,
    )
    assert pending is False, "un clic sur un lien ne doit pas armer la preuve"

    proven, _, _ = _advance_manual_browser_flow(
        fingerprint, mutation_pending=pending,
        tool_name="browser_dom_state",
        observation="Page: CaveAVin\nInteractive elements: 5\n[1] button S'inscrire",
    )
    assert proven is False, "CaveÀVin doit rester NON prouvée"


def test_a_real_form_flow_is_still_proven():
    """Non-régression indispensable : saisir puis soumettre reste une preuve.
    Sans ce test, le lot pourrait « corriger » en bloquant tout le monde."""
    _, fingerprint, pending = _advance_manual_browser_flow(
        "", mutation_pending=False, tool_name="browser_dom_state",
        observation="Page: inscription\n[3] textbox vide",
    )
    _, fingerprint, pending = _advance_manual_browser_flow(
        fingerprint, mutation_pending=pending,
        tool_name="browser_type_index", observation="✅ Saisi 'alice' dans [3]",
    )
    assert pending is True
    proven, _, _ = _advance_manual_browser_flow(
        fingerprint, mutation_pending=pending, tool_name="browser_dom_state",
        observation="Page: mes bouteilles\nChâteau Margaux — Bordeaux 2015",
    )
    assert proven is True


def test_a_button_submission_is_still_proven():
    _, fingerprint, pending = _advance_manual_browser_flow(
        "", mutation_pending=False, tool_name="browser_dom_state",
        observation="Page: inscription\n[1] button S'inscrire",
    )
    _, fingerprint, pending = _advance_manual_browser_flow(
        fingerprint, mutation_pending=pending,
        tool_name="browser_click_index", observation=_CLIC_BOUTON,
    )
    assert pending is True, "cliquer un bouton reste une mutation utilisateur"


# ── M2 : le plan ne se coche plus sur une simple navigation ─────────────────

def test_the_caveavin_task_is_no_longer_credited_by_navigation():
    assert browser_interaction_task_blocks("browser_navigate", _TACHE_CAVEAVIN) is True


def test_the_memonest_task_too():
    assert browser_interaction_task_blocks("browser_navigate", _TACHE_MEMONEST) is True


@pytest.mark.parametrize("tool", ["browser_evaluate", "browser_verify_local_project"])
def test_a_strong_proof_still_credits_the_journey(tool):
    assert browser_interaction_task_blocks(tool, _TACHE_CAVEAVIN) is False


def test_the_nominal_step_forms_are_understood():
    """« ajout » ≠ « ajouter » : c'est ce mot qui a fait taire le garde."""
    task = "Vérifier dans le navigateur que l'ajout d'une bouteille s'affiche"
    assert browser_interaction_task_blocks("browser_navigate", task) is True


def test_a_simple_browser_task_without_journey_is_untouched():
    """Une vérif d'affichage simple garde son comportement : aucun marqueur
    d'action, donc aucun blocage."""
    assert browser_interaction_task_blocks(
        "browser_navigate", "Vérifier au navigateur que la page s'affiche"
    ) is False


# ── M2 non-régression : hors interface, la règle historique est INTACTE ─────

def test_a_non_ui_task_keeps_the_historic_rule():
    """Élargir hors UI rendrait ces tâches INCOCHABLES : le garde exige
    `browser_evaluate`, impossible pour un CSV."""
    task = "Ajouter une ligne au CSV puis vérifier le total"
    assert task_has_ui_context(task) is False
    # « ajouter » + « total » : déjà bloquant AVANT le lot — inchangé.
    assert browser_interaction_task_blocks("write_file", task) is True


def test_a_non_ui_nominal_task_is_not_newly_blocked():
    """« ajout » hors UI ne doit PAS devenir bloquant — sinon régression."""
    task = "Ajout des données au fichier puis contrôle du total"
    assert task_has_ui_context(task) is False
    assert browser_interaction_task_blocks("write_file", task) is False


def test_the_html_to_pdf_task_is_left_alone():
    """Le 3e parcours du corpus réel : une flèche, mais aucune interface."""
    task = "Le formater proprement (HTML → PDF)"
    assert task_names_a_journey(task) is True
    assert browser_interaction_task_blocks("create_pdf", task) is False


# ── helpers purs ────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "task",
    [
        "inscription → connexion",
        "inscription -> connexion",
        "étape A => étape B",
        "remplis le formulaire puis vérifie la liste",
        "ouvre la page ensuite clique",
    ],
)
def test_journey_separators(task):
    assert task_names_a_journey(task) is True


def test_a_single_step_is_not_a_journey():
    assert task_names_a_journey("Vérifier la page d'accueil") is False
    assert task_names_a_journey("") is False
    assert task_names_a_journey(None) is False


def test_ui_context_detection():
    assert task_has_ui_context("vérifier au NAVIGATEUR") is True
    assert task_has_ui_context("remplir le formulaire") is True
    assert task_has_ui_context("compiler le module python") is False
    assert task_has_ui_context(None) is False
