"""Run du 2026-08-29 — l'ANGLE MORT SYMETRIQUE de Z40b.

La tache de plan « Verifier le resultat avec pytest (run_command) » a ete
refusee QUATRE fois (« preuve insuffisante ») alors que pytest avait tourne et
rendu `10 passed`, exit 0. Elle est restee `in-progress` sur une mission
cloturee `completed`.

Cause : le marqueur d'execution est la chaine « run » AVEC UNE ESPACE, et
« run_command » n'en a pas. Et `_test_outcome_task` saute le mot « test »
quand le caractere qui precede est alphanumerique — ce qui est le cas dans
« pytest ».

Z40b avait ferme le sens « la prose coche sans preuve ». Il laissait ouvert le
sens INVERSE : une preuve REELLE qui ne coche pas.

Le correctif ne devine aucune intention (lecon Z3b) : il lit DEUX NOMS PROPRES
— « pytest » et le nom de l'outil qui execute des commandes.
"""

from __future__ import annotations

import pytest

from src.reasoning.plan_progress import (
    pytest_execution_task,
    pytest_plan_task_proven,
)

VERT = {"is_test_cmd": True, "ran_something": True, "green": True}
ROUGE = {"is_test_cmd": True, "ran_something": True, "green": False}


# ══════════════════════════════════════════════════════════════════════════
#  1. L'intitule du run est desormais reconnu
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("intitule", [
    "Verifier le resultat avec pytest (run_command)",
    "Vérifier le résultat avec pytest (run_command)",
    "pytest via run_tests",
    "controle final : pytest (bash)",
])
def test_un_intitule_qui_NOMME_pytest_et_son_outil_est_une_execution(intitule):
    assert pytest_execution_task(intitule) is True


def test_la_tache_du_run_est_creditee_par_un_pytest_vert():
    assert pytest_plan_task_proven(
        "Verifier le resultat avec pytest (run_command)", "run_command", VERT) is True


# ══════════════════════════════════════════════════════════════════════════
#  2. Z40b tient toujours — on n'a pas affaibli le garde
# ══════════════════════════════════════════════════════════════════════════


def test_Z40b_tient_un_run_ROUGE_ne_credite_pas_les_tests_passent():
    """La 2e moitie de Z40b : exiger l'ISSUE quand l'intitule l'exige."""
    assert pytest_plan_task_proven("Verifier que les tests passent", "run_command", VERT) is True
    assert pytest_plan_task_proven("Verifier que les tests passent", "run_command", ROUGE) is False


@pytest.mark.parametrize("intitule", [
    "Ecrire les tests unitaires",
    "Rediger la doc de run_command",
    "Trier les resultats du benchmark",
    "Creer le fichier de configuration",
])
def test_ce_qui_ne_demande_PAS_une_execution_ne_matche_pas(intitule):
    """Lecon Z3b : on ne devine pas l'intention avec du vocabulaire."""
    assert pytest_execution_task(intitule) is False


def test_un_outil_non_executant_ne_prouve_rien():
    """Nommer pytest ne suffit pas : il faut que l'OUTIL appele l'ait lance."""
    assert pytest_plan_task_proven(
        "Verifier le resultat avec pytest (run_command)", "read_file", VERT) is False


def test_une_commande_qui_n_a_rien_lance_ne_prouve_rien():
    assert pytest_plan_task_proven(
        "Verifier le resultat avec pytest (run_command)", "run_command",
        {"is_test_cmd": True, "ran_something": False, "green": True}) is False


def test_les_intitules_historiques_restent_reconnus():
    for h in ("Lancer pytest", "executer pytest", "faire tourner pytest",
              "relancer pytest jusqu'au vert"):
        assert pytest_execution_task(h) is True, h
