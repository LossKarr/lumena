"""LOT Z6 — un plafond annoncé doit être un plafond appliqué.

Run réel « Écluse » (2026-08-15, task_b1032276…) :

    [PLAN GUARD] FINAL premature bloque: 2/4 taches, iteration 66 (retry 18/3)
    [PLAN GUARD] Aucune progression en 10 iterations, FINAL force

Dix-huit refus sur un plafond affiché de trois. La condition était
``retries < 3 OR tâches_opérationnelles_restantes`` et le second terme n'avait
aucune borne. Le filet anti-stagnation, qui pose ``_plan_guard_retries = 3`` pour
« empêcher PLAN GUARD de bloquer », était neutralisé par ce même ``or`` : posé,
puis ignoré.

La mission avait pourtant SA preuve — elle avait lu « 5400 s · 90 min · 1.5 h ·
0.0625 j » dans le DOM après un clic réel. Elle est morte d'épuisement sans
jamais pouvoir conclure : ni final, ni tâches faites.

Ces tests fixent la règle : deux plafonds distincts, tous deux FINIS, et un
bypass qui bypasse réellement.
"""

import re
from pathlib import Path

import pytest

from src.reasoning.react import (
    _PLAN_GUARD_MAX_RETRIES,
    _PLAN_GUARD_MAX_RETRIES_OPERATIONAL,
)

_SRC = Path("src/reasoning/react.py").read_text(encoding="utf-8")


def _guard_autorise(retries: int, operationnelles: bool) -> bool:
    """Réplique EXACTE de la condition de blocage du PLAN GUARD (react.py).

    Volontairement recopiée plutôt qu'importée : la condition vit au milieu
    d'une boucle de 3 000 lignes et n'est pas extractible sans refactor risqué.
    Les tests de structure plus bas garantissent que cette copie ne dérive pas.
    """
    return retries < _PLAN_GUARD_MAX_RETRIES or (
        operationnelles and retries < _PLAN_GUARD_MAX_RETRIES_OPERATIONAL
    )


# ── Les deux plafonds sont finis et ordonnés ─────────────────────────────────


def test_les_deux_plafonds_sont_finis():
    """Le défaut d'origine, c'est justement l'absence de borne."""
    assert isinstance(_PLAN_GUARD_MAX_RETRIES, int)
    assert isinstance(_PLAN_GUARD_MAX_RETRIES_OPERATIONAL, int)
    assert 0 < _PLAN_GUARD_MAX_RETRIES < 100
    assert 0 < _PLAN_GUARD_MAX_RETRIES_OPERATIONAL < 100


def test_le_plafond_operationnel_est_le_plus_large():
    """Une tâche à preuve mérite plus d'insistance — pas une insistance infinie."""
    assert _PLAN_GUARD_MAX_RETRIES_OPERATIONAL > _PLAN_GUARD_MAX_RETRIES


def test_le_plafond_metier_reste_a_trois():
    """Z6 ne relâche pas l'existant : le cas sans tâche opérationnelle est intact."""
    assert _PLAN_GUARD_MAX_RETRIES == 3


# ── Le cas du run : 18 refus ne doit plus jamais arriver ─────────────────────


def test_le_dix_huitieme_refus_du_run_ecluse_nest_plus_possible():
    """La valeur exacte observée en production."""
    assert _guard_autorise(18, operationnelles=True) is False


@pytest.mark.parametrize("retries", [8, 9, 12, 18, 40, 200])
def test_au_dela_du_plafond_operationnel_le_final_passe(retries):
    assert _guard_autorise(retries, operationnelles=True) is False


@pytest.mark.parametrize("retries", [0, 1, 2, 3, 5, 7])
def test_en_dessous_du_plafond_le_garde_bloque_toujours(retries):
    """L'insistance légitime est conservée : une tâche à preuve reste réclamée."""
    assert _guard_autorise(retries, operationnelles=True) is True


def test_la_frontiere_operationnelle_est_exacte():
    assert _guard_autorise(_PLAN_GUARD_MAX_RETRIES_OPERATIONAL - 1, True) is True
    assert _guard_autorise(_PLAN_GUARD_MAX_RETRIES_OPERATIONAL, True) is False


# ── Sans tâche opérationnelle : comportement historique inchangé ─────────────


@pytest.mark.parametrize("retries", [0, 1, 2])
def test_sans_tache_operationnelle_le_garde_bloque_jusqua_trois(retries):
    assert _guard_autorise(retries, operationnelles=False) is True


@pytest.mark.parametrize("retries", [3, 4, 8, 18])
def test_sans_tache_operationnelle_le_garde_laisse_passer_des_trois(retries):
    assert _guard_autorise(retries, operationnelles=False) is False


def test_la_frontiere_metier_est_exacte():
    assert _guard_autorise(_PLAN_GUARD_MAX_RETRIES - 1, False) is True
    assert _guard_autorise(_PLAN_GUARD_MAX_RETRIES, False) is False


# ── Le filet anti-stagnation doit réellement débloquer ───────────────────────


def test_le_bypass_anti_stagnation_debloque_meme_avec_une_tache_operationnelle():
    """Le cœur du défaut : le filet existait, il était posé, il ne servait à rien.

    La valeur posée par le bypass doit franchir le PLUS HAUT des deux plafonds,
    sinon « empêche PLAN GUARD de bloquer » est un mensonge dans un commentaire.
    """
    pose_par_le_bypass = max(
        _PLAN_GUARD_MAX_RETRIES, _PLAN_GUARD_MAX_RETRIES_OPERATIONAL
    )
    assert _guard_autorise(pose_par_le_bypass, operationnelles=True) is False
    assert _guard_autorise(pose_par_le_bypass, operationnelles=False) is False


def test_lancienne_valeur_du_bypass_ne_suffisait_pas():
    """Verrouille la RAISON du correctif : poser 3 laissait le garde bloquer."""
    assert _guard_autorise(3, operationnelles=True) is True


# ── La copie de la condition ne doit pas dériver du code réel ────────────────


def test_le_code_reel_borne_bien_le_court_circuit_operationnel():
    """Si quelqu'un retire la borne, ce test tombe — c'est tout son objet."""
    # `rindex` : la chaîne apparaît aussi dans le commentaire d'en-tête du lot ;
    # c'est le SITE D'APPEL, tout en bas, qu'on veut inspecter.
    site = _SRC.rindex("[PLAN GUARD] FINAL premature bloque")
    bloc = _SRC[site - 3000 : site]
    assert "_PLAN_GUARD_MAX_RETRIES_OPERATIONAL" in bloc
    assert "_operational_tasks_remaining" in bloc


def test_le_code_reel_nutilise_plus_de_seuil_ecrit_en_dur():
    """`self._plan_guard_retries < 3` en dur est précisément ce qu'on a retiré."""
    assert "self._plan_guard_retries < 3" not in _SRC


def test_le_bypass_pose_bien_le_maximum_des_deux_plafonds():
    assert re.search(
        r"self\._plan_guard_retries = max\(\s*_PLAN_GUARD_MAX_RETRIES,\s*"
        r"_PLAN_GUARD_MAX_RETRIES_OPERATIONAL\s*,?\s*\)",
        _SRC,
    )


def test_le_message_naffiche_plus_un_plafond_faux():
    """« retry 18/3 » a masqué le défaut aussi longtemps qu'il a duré."""
    assert "(retry {}/3)" not in _SRC
    assert "(retry {}/{})" in _SRC
