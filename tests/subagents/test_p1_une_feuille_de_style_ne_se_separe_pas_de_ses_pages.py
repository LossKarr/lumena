"""LOT P1 — ÉCRIT, PUIS RETIRÉ. Mes propres données l'ont réfuté.

P1 devait refuser qu'un contrat confie une feuille de style et ses pages à des
workers différents. Le code était écrit, 25 tests étaient verts, et il refusait
19 des 95 contrats réels du disque — dont les quatre pires couvertures jamais
mesurées (palier 4 %, rustine 0 %, marée 31 %, Verdure 56 %).

**La suite complète l'a tué**, via un verrou posé au LOT Q :

    def test_no_grouping_rule_was_introduced():
        \"\"\"Verrou sur l'audit : les données ne justifient AUCUNE contrainte de
        découpage. Si quelqu'un veut en ajouter une un jour, qu'il refasse la
        mesure d'abord — 3 projets séparés sur 6 sont à 100 %.\"\"\"

J'ai refait la mesure, sur 29 missions au lieu de 6. **Elle confirme le verrou** :

    1 fichier par worker    (7 missions)   médiane  56 %
    plusieurs par worker   (22 missions)   médiane  67 %

Onze points d'écart, et les deux groupes vont de 0 % à 100 % — cordée et tanière
sont fragmentées ET à 100 %. J'avais moi-même conclu « le découpage n'explique
pas », puis j'ai construit P1 dessus. Le verrou m'a rattrapé : c'est exactement
son rôle, et la règle du chantier est de corriger le code, jamais le test.

**Ce qui reste vrai** : le worker CSS démarre bien quand ses pages ne sont encore
que des stubs. Mais la cause n'est pas le découpage — c'est que le CodeAgent
suivait une consigne périmée alors qu'il avait le bon fichier sous les yeux, à
200 ms près. C'est le LOT Z14 qui traite ça, sur une preuve directe du log plutôt
que sur une corrélation à 11 points.

Ce fichier reste pour que le prochain qui a cette idée trouve la mesure déjà
faite — deux fois — et ne la refasse pas une troisième.
"""

import inspect


def test_la_regle_de_regroupement_reste_absente():
    """Le miroir du verrou du LOT Q, côté chantier de clôture : si P1 revient un
    jour, ce test tombe en même temps que l'autre."""
    from src.subagents import mission_contract

    src = inspect.getsource(mission_contract)
    for interdiction in ("même owner", "meme owner", "same_owner", "css_owner"):
        assert interdiction not in src


def test_le_validateur_ne_juge_plus_les_perimetres_frontend():
    """Un contrat qui sépare page et style doit rester ACCEPTABLE — c'est ce que
    la mesure dit, même si l'intuition dit l'inverse."""
    from src.subagents.mission_contract import validate_contract

    contrat = {"project": "x", "files": [
        {"path": "index.html", "owner": "w1", "desc": "Page publique."},
        {"path": "styles.css", "owner": "w2", "desc": "Présentation."},
    ]}
    assert validate_contract(contrat) == []


def test_la_mesure_qui_a_refute_le_lot_est_conservee():
    """Sans les chiffres, le refus se lit comme un abandon plutôt que comme une
    décision fondée."""
    from pathlib import Path

    doc = Path(__file__).read_text(encoding="utf-8")
    assert "56 %" in doc and "67 %" in doc
    assert "29 missions" in doc
