"""LOT Z16 — l'utilisateur décrit un métier, pas des gestes.

Run « Verdure 2 » (2026-08-16). L'énoncé exigeait quatre comportements, nommés
un par un :

    « enregistrer un client (nom, adresse du jardin) »
    « créer un devis pour ce client avec une liste de prestations »
    « faire passer le devis d'un état au suivant »
    « tout survit à un rechargement de page »

Mesuré dans le log : **0 clic, 0 saisie, 0 interaction**. Le drapeau
`_truth_lock_interaction_flag()` est resté BAS tout le run, donc le garde
`_finalize_interaction_gate_pending` — qui existe et fonctionne — n'a jamais eu
l'occasion de tirer. La mission a conclu proprement sans avoir exercé une seule
des fonctionnalités demandées.

**La cause est un vocabulaire.** `_WEB_INTERACTION_ACTION_RE` ne connaissait que
des verbes de GESTE : cliquer, saisir, cocher, remplir, soumettre. Or personne
n'écrit « clique sur le bouton et vérifie que le DOM change » : on écrit ce que
l'application doit FAIRE. L'exigence était là, explicite et détaillée — le
système ne l'a pas reconnue parce qu'elle était dite en langage de spécification.

C'est le motif de tout ce chantier, appliqué à la demande elle-même : le fait
existe, il est écrit noir sur blanc, et il est ignoré avant de pouvoir agir.

**DÉCISION UTILISATEUR (2026-08-16)** : « Lumena ne peut pas s'arrêter sans avoir
fini ce qu'on lui demande. »
"""

import pytest

from src.reasoning.final_guards import objective_requires_web_interaction_proof as _exige

_VERDURE2 = (
    "Fonctionnement dans devis.html : enregistrer un client (nom, adresse du "
    "jardin), créer un devis pour ce client avec une liste de prestations, "
    "faire passer le devis d'un état au suivant, tout survit à un rechargement "
    "de page"
)


# ── Le cas mesuré ────────────────────────────────────────────────────────────


def test_lenonce_verdure2_exige_bien_une_preuve_dinteraction():
    """Il rendait False. Quatre fonctionnalités demandées, aucune exercée."""
    assert _exige(_VERDURE2) is True


@pytest.mark.parametrize(
    "objectif",
    [
        "page de contact : le formulaire enregistre le message et affiche une confirmation",
        "espace client : créer une commande et changer son état, la page se met à jour",
        "interface de réservation : réserver un créneau, l'état passe à confirmé",
        "formulaire d'inscription : inscrire un membre et vérifier qu'il apparaît",
        "page admin : supprimer une ligne, le tableau se met à jour",
        "modifier une fiche depuis la page produit et constater le changement",
        "se connecter puis vérifier que le bouton de la page change d'état",
    ],
)
def test_les_verbes_metier_declenchent_lexigence(objectif):
    """Le vocabulaire réel des énoncés — jamais celui du geste."""
    assert _exige(objectif) is True


def test_les_verbes_de_geste_marchent_toujours():
    """Z16 AJOUTE, il ne remplace pas : l'ancien vocabulaire reste reconnu."""
    assert _exige("clique sur le bouton du formulaire et vérifie le DOM") is True
    assert _exige("saisir un nom dans le champ puis constater l'affichage") is True


# ── Inertie : la conjonction des trois signaux protège ───────────────────────


@pytest.mark.parametrize(
    "objectif",
    [
        "crée un rapport PDF mensuel des ventes",
        "enregistre les données dans un fichier CSV",
        "écris un script python qui trie une liste",
        "analyse ces logs et donne un résumé",
        "crée un module de calcul avec ses tests",
        "envoie un mail de synthèse chaque lundi",
        "valide le schéma de la base de données",
    ],
)
def test_un_livrable_sans_web_ne_declenche_rien(objectif):
    """Les verbes métier ne suffisent pas : il faut AUSSI un résultat observable
    et un contexte web. Sans quoi, toute mission de code exigerait une preuve
    navigateur — sur-déclenchement garanti."""
    assert _exige(objectif) is False


def test_une_vitrine_sans_interaction_ne_declenche_rien():
    """Une page qui n'affiche que du contenu n'a rien à exercer : la jambe
    navigateur du LOT D suffit, et Z11/Z15 vérifient qu'on l'a ouverte."""
    assert _exige(
        "construis une page vitrine avec 3 sections, des tarifs et les horaires"
    ) is False


@pytest.mark.parametrize("objectif", ["", "   ", None])
def test_une_entree_vide_ne_leve_jamais(objectif):
    assert _exige(objectif) is False


# ── La conjonction elle-même, qui est le garde-fou ───────────────────────────


def test_laction_seule_ne_suffit_pas():
    """Sans résultat observable ni contexte web."""
    assert _exige("enregistrer les clients") is False


def test_le_contexte_web_seul_ne_suffit_pas():
    """Une page mentionnée n'implique pas qu'on doive l'actionner."""
    assert _exige("la page affiche les horaires") is False


def test_les_trois_signaux_restent_exiges_ensemble():
    from src.reasoning import final_guards as g
    import inspect

    src = inspect.getsource(g.objective_requires_web_interaction_proof)
    assert src.count(" and ") >= 2  # action ET résultat ET contexte


# ── La raison, pour que l'élargissement ne soit pas défait ───────────────────


def test_la_mesure_qui_a_motive_le_lot_est_dans_le_code():
    from pathlib import Path

    src = Path("src/reasoning/final_guards.py").read_text(encoding="utf-8")
    i = src.index("LOT Z16")
    entete = src[i : i + 1500]
    assert "Verdure 2" in entete
    assert "0 clic" in entete
    assert "CONJONCTION" in entete


def test_les_verbes_metier_sont_identifies_comme_tels():
    """Sans le repère, un relecteur les prendra pour des doublons et les coupera."""
    from pathlib import Path

    src = Path("src/reasoning/final_guards.py").read_text(encoding="utf-8")
    i = src.index("_WEB_INTERACTION_ACTION_RE = re.compile")
    assert "Z16 : verbes métier" in src[i : i + 700]
