"""LOT L1 — « ne pas utiliser le navigateur » doit vouloir dire NON.

Audit des **80 contrats réellement produits** (2026-08-14). `_objective_wants_browser`
juge 58 objectifs « web ». En croisant avec les contrats, 4 missions auraient été
signalées comme « interface attendue mais absente » — et **3 de ces 4 étaient des
faux positifs** :

| mission            | nature réelle                | verdict |
|--------------------|------------------------------|---------|
| MotCompteur        | outil EN LIGNE DE COMMANDE   | faux    |
| TempConv           | outil EN LIGNE DE COMMANDE   | faux    |
| fiche pyproject    | mission d'EFFETS, 0 fichier  | faux    |
| MemoNest           | SaaS avec interface          | VRAI    |

Deux trous, tous deux dans du code **déjà en production** (le BROWSER GATE) :

1. la négation n'existait qu'à la forme NOMINALE (`pas DE navigateur`). Les
   objectifs de MotCompteur et TempConv disent « **Ne pas utiliser le
   navigateur** » — forme verbale, non couverte → la garde se déclenchait, et
   ces missions CLI recevaient une bannière « navigateur non vérifié » ;
2. le nom de l'outil NIÉ était lu comme signal POSITIF : la fiche mémo
   déclenchait sur « utilise web_search_brave, **pas browser_navigate** ».

Mesure après correctif, sur le même échantillon de 80 :
`objectifs web 58 → 55`, `avertissements 4 → 1`, **3 verdicts changés, exactement
les 3 faux positifs.** Aucune mission web n'est perdue.
"""
from __future__ import annotations

import pytest

from src.reasoning.react import _objective_wants_browser

_APO = "'"
_APO_COURBE = "’"


# ── les négations qui échouaient : forme VERBALE ────────────────────────────

@pytest.mark.parametrize(
    "objective",
    [
        "Ne pas utiliser le navigateur.",
        "ne pas utiliser de navigateur",
        "Ne pas utiliser browser_navigate",
        f"n{_APO}utilise pas le navigateur",
        f"n{_APO_COURBE}utilise pas le navigateur",
        "n utilise pas le navigateur",   # apostrophe oubliée — arrive vraiment
        "ne utilise pas le navigateur",
        "sans utiliser de navigateur",
        "sans utiliser le navigateur",
    ],
)
def test_the_verbal_negation_means_no(objective):
    assert _objective_wants_browser(objective) is False


def test_the_exact_objective_of_motcompteur():
    """Cité du run : un outil CLI recevait une bannière navigateur."""
    obj = (
        "Construis MotCompteur, un outil Python en ligne de commande.\n"
        "2. cli.py : point d'entrée `python cli.py <fichier.txt>`\n"
        "Ne pas utiliser le navigateur. Ne dire 'tests verts' que si c'est prouvé."
    )
    assert _objective_wants_browser(obj) is False


def test_the_exact_objective_of_the_memo_mission():
    """Le nom de l'outil, NIÉ, était lu comme un signal positif."""
    obj = (
        "Constituer une fiche mémo sur le format `pyproject.toml`. Ce travail ne "
        "produit AUCUN fichier de code : le premier cherche les sections "
        "principales (utilise web_search_brave ou web_search_ddg, pas "
        "browser_navigate) ; le second rédige un mémo."
    )
    assert _objective_wants_browser(obj) is False


@pytest.mark.parametrize(
    "objective", ["pas browser_navigate", "sans browser_navigate", "pas d'interface"]
)
def test_the_tool_name_negated_is_not_a_positive_signal(objective):
    assert _objective_wants_browser(objective) is False


# ── NON-RÉGRESSION : une vraie mission web reste une mission web ────────────

@pytest.mark.parametrize(
    "objective",
    [
        "VÉRIFIE AU NAVIGATEUR le parcours complet : inscription → connexion",
        "Construis un site web statique",
        "page d'accueil publique avec index.html",
        "application web Flask avec une interface web",
        "utilise browser_navigate pour vérifier la page",
        "le frontend doit afficher la liste",
        "dépose les assets dans static/",
    ],
)
def test_a_real_web_mission_is_still_detected(objective):
    assert _objective_wants_browser(objective) is True


def test_the_memonest_objective_is_still_web():
    """Le SEUL vrai positif de l'échantillon doit le rester : c'est lui qui
    justifie l'avertissement du lot L2."""
    obj = (
        "Construis MemoNest, un SaaS de prise de notes multi-utilisateur en Flask.\n"
        "- page d'accueil publique expliquant le produit ;\n"
        "- démarre le serveur et VÉRIFIE AU NAVIGATEUR le parcours complet."
    )
    assert _objective_wants_browser(obj) is True


# ── les négations historiques (2.9.A, 2.12.B) ne doivent pas régresser ──────

@pytest.mark.parametrize(
    "objective",
    [
        "pas de navigateur",
        "sans navigateur",
        "aucun navigateur",
        "Pas d'interface web, uniquement JSON",
        "API REST sans interface",
        "valide uniquement par les tests",
        "sans page html",
    ],
)
def test_the_historic_negations_still_hold(objective):
    assert _objective_wants_browser(objective) is False


# ── robustesse ──────────────────────────────────────────────────────────────

def test_empty_and_garbage_are_false():
    assert _objective_wants_browser("") is False
    assert _objective_wants_browser(None) is False
    assert _objective_wants_browser(12345) is False


def test_a_negation_anywhere_in_a_long_objective_wins():
    """La négation arrive souvent en DERNIÈRE ligne (« Ne pas utiliser le
    navigateur »), après des lignes qui parlent de fichiers et de tests."""
    obj = "Construis un outil CLI.\n" * 40 + "Ne pas utiliser le navigateur."
    assert _objective_wants_browser(obj) is False
