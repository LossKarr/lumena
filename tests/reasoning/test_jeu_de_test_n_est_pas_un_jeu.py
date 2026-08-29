"""Run LogTriage (2026-08-29) — « jeu de test » n'est pas un jeu.

L'utilisateur a recu, sur une application de TRI DE LOGS batie autour d'un
classifieur bayesien, la banniere suivante :

    ⚠️ Interaction NON prouvee — le rapport affirme une dynamique DE JEU
    (« demarre / deplace / score augmente »)

Le modele n'avait jamais parle de jeu.

--- La chaine, mesuree ---

L'objectif de la mission contenait :

    exige une exactitude >= 0.85 sur le JEU DE TEST

`_WEB_GAME_OBJECTIVE_RE` cherche `jeux?` comme mot FORT. Il a matche.
`objective_is_web_game` a rendu True, `objective_is_game=True` est passe au
truth-lock, et 2.13.A bannerise alors QUEL QUE SOIT le texte du final — par
construction, justement pour ne pas dependre de la formulation du modele.

--- Le detecteur du FINAL, lui, est INNOCENT ---

Mesure sur 592 finals reels : 2 seuls arment `_INTERACTION_CLAIM_RE` (0,3 %),
et les deux sont de vrais jeux. Le defaut etait entierement du cote de
l'OBJECTIF. On ne touche donc pas au detecteur du final.

--- La lecon etait deja ecrite dans le lot 2.13.A ---

Son propre commentaire disait : « "partie" seul est ambigu ("une partie des
donnees") -> compte seulement avec un verbe de jeu a proximite ».

L'ambiguite avait ete vue, sur l'AUTRE mot. En francais technique, « jeu » en
est la forme la plus courante : jeu de donnees, jeu de test, jeu
d'entrainement, jeu de validation.

Mesure sur 661 objectifs reels — avant : 51 classes jeu web, dont 5 UNIQUEMENT
a cause de cette tournure. Apres masquage : 46, et les 46 sont de vrais jeux.
"""

from __future__ import annotations

import pytest

from src.reasoning.final_guards import (
    interaction_claims_unproven,
    objective_is_web_game,
)


#: L'objectif reel de la mission, reduit a ce qui compte.
OBJECTIF_LOGTRIAGE = (
    "Construis LogTriage dans workspace/logtriage. Une application web locale "
    "qui classe les lignes d'un log en 3 categories (normal / garde / anomalie) "
    "avec un classifieur Naive Bayes ecrit a la main. w_tests : une VALIDATION "
    "CROISEE 80/20 qui exige une exactitude >= 0.85 sur le jeu de test."
)


# ══════════════════════════════════════════════════════════════════════════
#  1. Le cas du run
# ══════════════════════════════════════════════════════════════════════════


def test_LE_cas_du_run_logtriage_n_est_pas_un_jeu_web():
    assert objective_is_web_game(OBJECTIF_LOGTRIAGE) is False


@pytest.mark.parametrize("tournure", [
    "sur le jeu de test",
    "sur les jeux de tests",
    "un jeu de donnees realiste sur 30 jours",
    "le jeu de donnees exemple",
    "le jeu d'entrainement",
    "le jeu de validation",
    "un jeu d'essai",
    "le jeu de caracteres UTF-8",
    "un jeu de parametres par defaut",
    "le jeu de valeurs attendues",
    "un jeu d'echantillons",
])
def test_un_jeu_de_donnees_n_est_pas_un_jeu(tournure):
    """En francais technique, un JEU est aussi un ENSEMBLE."""
    assert objective_is_web_game(
        "Construis une application web qui traite " + tournure + ".") is False


def test_l_apostrophe_typographique_compte_aussi():
    """Le modele ecrit parfois U+2019 au lieu de l'apostrophe droite."""
    assert objective_is_web_game(
        "Une app web avec un jeu d’entrainement de 500 lignes.") is False


# ══════════════════════════════════════════════════════════════════════════
#  2. AUCUN affaiblissement — 2.13.A garde toute sa force
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("objectif", [
    "Cree un jeu de morpion (tic-tac-toe) a 2 joueurs en HTML/CSS/JS.",
    "Cree un Puissance 4 jouable a 2 en HTML/CSS/JS, les jetons tombent.",
    "Cree un jeu Snake jouable au clavier en HTML/CSS/JS.",
    "Construis un jeu du PENDU web (HTML/CSS/JS).",
    "Construis un jeu de DEMINEUR web complet (HTML/CSS/JS).",
    "Construis MemoJeu, un jeu de memory dans workspace/memojeu/.",
    "Construis LumaGrid, un jeu web Lights Out 5x5.",
    "Cree un jeu 3D a monde ouvert en HTML/CSS/JS pur.",
    "Une page avec un joueur qui marque des points.",
    "Un tetris jouable au clavier.",
])
def test_les_VRAIS_jeux_restent_TOUS_detectes(objectif):
    assert objective_is_web_game(objectif) is True


@pytest.mark.parametrize("jeu", [
    "un jeu de morpion",
    "un jeu de memory",
    "un jeu de dames jouable",
    "un jeu de cartes pour 2 joueurs",
])
def test_LE_PIEGE_un_jeu_de_morpion_reste_un_jeu(jeu):
    """Pourquoi on MASQUE au lieu d'exclure « jeu de … » : une exclusion seche
    aurait tue « un jeu de morpion », « un jeu de dames », « un jeu de cartes ».
    Le masque ne retire que les ENSEMBLES nommes, jamais un nom de jeu."""
    assert objective_is_web_game("Construis en HTML " + jeu + ".") is True


def test_la_negation_tient_toujours():
    """2.12.B — « ce n'est pas un jeu » ne doit pas armer le garde."""
    assert objective_is_web_game("Une app web, ce n'est pas un jeu.") is False
    assert objective_is_web_game("Un site sans jeu ni animation.") is False


def test_un_objectif_vide_reste_inerte():
    assert objective_is_web_game("") is False
    assert objective_is_web_game(None) is False


def test_le_masque_ne_change_rien_a_un_objectif_sans_jeu():
    """Innocuite : un objectif ordinaire ne doit pas devenir un jeu."""
    for o in ("Redige un rapport sur Redis en 2026.",
              "Construis une API FastAPI avec 3 routes.",
              "Cree un tableau de bord web avec des compteurs."):
        assert objective_is_web_game(o) is False, o


# ══════════════════════════════════════════════════════════════════════════
#  3. Le detecteur du FINAL est INNOCENT — il n'a pas bouge
# ══════════════════════════════════════════════════════════════════════════


def test_le_detecteur_du_final_voit_toujours_un_vrai_jeu():
    assert interaction_claims_unproven(
        "Le serpent se deplace et le score augmente a chaque pomme.") is True
    assert interaction_claims_unproven(
        "Les jetons tombent par gravite, X a gagne en diagonale.") is True


def test_le_detecteur_du_final_ignore_un_rapport_de_classification():
    assert interaction_claims_unproven(
        "Le classifieur atteint 0.91 d'exactitude sur le jeu de test.") is False
