"""RF-9b — deuxième feuille : la détection de stagnation de pensée.

--- Pourquoi celle-ci, et pas la plus grosse ---

Le §15 est explicite : « une réduction supplémentaire n'est acceptée que si
elle diminue **réellement l'état partagé** ». Le nombre de lignes n'est pas le
critère — `react.py` était déjà dans la cible annoncée à l'ouverture de RF-9.

Mesure du gain réel, feuille par feuille (une locale « disparaît » si elle est
écrite dans la feuille et lue nulle part ailleurs) :

    ligne  taille  locales qui disparaissent
    4681      16   **7**   <- celle-ci
    4716      21     5
    8481      28     4
    5751      93     3     <- la plus GROSSE n'en retire que 3

**La feuille de 93 lignes score 3 ; celle de 16 lignes score 7.** Extraire au
nombre de lignes aurait choisi la mauvaise.

--- Le plafond de RF-9, mesuré ---

    gain total possible sur les 39 feuilles pures :  54 locales
    locales de `_run_internal`                    : 664

**RF-9 par extraction de feuilles plafonne à ~8 % de l'état partagé.** Les 25
variables de longue portée — `message` (22 écritures), `action` (250 lectures),
`observation`, `t`, `h` — ne sont pas extractibles : elles *sont* la colonne
vertébrale de la boucle. C'est une limite à écrire, pas à contourner.

--- La décision extraite ---

    (contenu_pensee, pensees_precedentes, requete, risque_boucle) -> bool

Quatre entrées, une décision typée. Restent dans la boucle : l'ajout à
`_previous_thoughts` et sa troncature à 5 — ce sont des mutations.

--- Les deux détections, conservées telles quelles ---

1. **Recouvrement de vocabulaire** sur les 2 pensées précédentes, avec seuil
   adaptatif : 0,65 si la requête fait ≤ 5 mots, 0,80 sinon, puis −0,10 si
   `loop_risk == "high"` et −0,05 si `"medium"` (lot P5).
2. **Préfixe commun** sur 3 pensées + la courante : si les 15 premiers mots se
   recouvrent à plus de 60 %, c'est de la stagnation.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest


RACINE = pathlib.Path(__file__).resolve().parents[2]
REACT = RACINE / "src" / "reasoning" / "react.py"
MODULE = RACINE / "src" / "reasoning" / "observation_synthesis.py"


def _run_internal():
    arbre = ast.parse(REACT.read_text(encoding="utf-8"))
    cls = next(n for n in arbre.body
               if isinstance(n, ast.ClassDef) and n.name == "ReActLoop")
    return next(n for n in cls.body if getattr(n, "name", "") == "_run_internal")


# ══════════════════════════════════════════════════════════════════════════
#  1. La decision existe, PURE et TYPEE
# ══════════════════════════════════════════════════════════════════════════


def test_la_decision_existe_et_est_typee():
    from src.reasoning.observation_synthesis import thought_is_stagnant

    sig = inspect.signature(thought_is_stagnant)
    assert list(sig.parameters) == [
        "thought_content", "previous_thoughts", "original_query", "loop_risk",
    ]
    assert sig.return_annotation in (bool, "bool")


def test_la_decision_est_PURE():
    arbre = ast.parse(MODULE.read_text(encoding="utf-8"))
    fn = next(n for n in arbre.body
              if isinstance(n, ast.FunctionDef) and n.name == "thought_is_stagnant")
    interdits = []
    for x in ast.walk(fn):
        if isinstance(x, (ast.Await, ast.Global, ast.Nonlocal)):
            interdits.append(type(x).__name__)
        if isinstance(x, ast.Attribute) and isinstance(x.ctx, ast.Store):
            interdits.append("mutation ." + x.attr)
        if isinstance(x, ast.Call) and getattr(x.func, "attr", "") in (
                "append", "extend", "pop", "debug", "info", "warning"):
            interdits.append("effet ." + x.func.attr)
    assert not interdits, f"la feuille n'est pas pure : {sorted(set(interdits))}"


# ══════════════════════════════════════════════════════════════════════════
#  2. Detection 1 — recouvrement de vocabulaire, seuil adaptatif
# ══════════════════════════════════════════════════════════════════════════


def _pensee(mots: str) -> str:
    return mots


def test_deux_pensees_quasi_identiques_sont_stagnantes():
    from src.reasoning.observation_synthesis import thought_is_stagnant

    p = "je dois lire le fichier de configuration pour comprendre le probleme"
    assert thought_is_stagnant(p, [p, p], "requete utilisateur assez longue ici", "low")


def test_deux_pensees_differentes_ne_sont_PAS_stagnantes():
    from src.reasoning.observation_synthesis import thought_is_stagnant

    assert not thought_is_stagnant(
        "je vais ecrire le module de calcul",
        ["il faut interroger la base de donnees",
         "commencons par verifier les permissions du dossier"],
        "requete utilisateur assez longue ici", "low",
    )


def test_moins_de_deux_pensees_precedentes_ne_declenche_pas():
    """La detection principale exige un historique d'au moins 2."""
    from src.reasoning.observation_synthesis import thought_is_stagnant

    p = "je dois lire le fichier de configuration"
    assert not thought_is_stagnant(p, [p], "une requete", "low")
    assert not thought_is_stagnant(p, [], "une requete", "low")


def test_une_pensee_vide_ne_declenche_jamais():
    from src.reasoning.observation_synthesis import thought_is_stagnant

    assert not thought_is_stagnant("", ["a b c", "a b c"], "q", "low")


@pytest.mark.parametrize("risque,attendu", [
    ("low", False),     # seuil 0,80 -> pas atteint
    ("medium", True),   # 0,80 - 0,05 = 0,75
    ("high", True),     # 0,80 - 0,10 = 0,70
])
def test_le_seuil_baisse_avec_le_risque_de_boucle(risque, attendu):
    """LOT P5 — un modele a `loop_risk` eleve voit son seuil abaisse pour une
    detection plus precoce. Ce reglage est le coeur du lot : il ne doit pas
    bouger d'un centieme."""
    from src.reasoning.observation_synthesis import thought_is_stagnant

    # Recouvrement construit a 10/13 = 0,769 : strictement entre 0,75 (medium)
    # et 0,80 (low). Un premier jeu donnait exactement 0,75 — et la comparaison
    # est stricte, donc `medium` ne declenchait pas.
    base = "a1 a2 a3 a4 a5 a6 a7 a8 a9 a10 a11 a12"
    variante = "a1 a2 a3 a4 a5 a6 a7 a8 a9 a10 b1"
    requete = "une requete utilisateur de plus de cinq mots pour le seuil haut"
    assert thought_is_stagnant(variante, [base, base], requete, risque) is attendu


def test_le_seuil_est_plus_bas_sur_une_requete_COURTE():
    """0,65 si la requete fait <= 5 mots, 0,80 sinon."""
    from src.reasoning.observation_synthesis import thought_is_stagnant

    base = "alpha beta gamma delta epsilon zeta eta theta"
    variante = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu"
    # requete courte -> seuil 0,65 -> stagnant
    assert thought_is_stagnant(variante, [base, base], "repare le bug", "low")
    # requete longue -> seuil 0,80 -> pas stagnant
    assert not thought_is_stagnant(
        variante, [base, base],
        "une requete utilisateur de plus de cinq mots pour monter le seuil", "low",
    )


# ══════════════════════════════════════════════════════════════════════════
#  3. Detection 2 — prefixe commun sur trois pensees
# ══════════════════════════════════════════════════════════════════════════


def test_trois_pensees_au_prefixe_commun_sont_stagnantes():
    """« 3+ actions read-only consecutives sur meme sujet »."""
    from src.reasoning.observation_synthesis import thought_is_stagnant

    prefixe = "je dois analyser le module de configuration des utilisateurs pour "
    p1 = prefixe + "trouver la fonction principale"
    p2 = prefixe + "comprendre la structure des donnees"
    p3 = prefixe + "identifier les dependances externes"
    courant = prefixe + "verifier les valeurs par defaut"
    assert thought_is_stagnant(courant, [p1, p2, p3], "une requete longue ici", "low")


def test_trois_pensees_sans_prefixe_commun_ne_declenchent_pas():
    from src.reasoning.observation_synthesis import thought_is_stagnant

    assert not thought_is_stagnant(
        "maintenant je publie le livrable",
        ["il faut lire le fichier de configuration",
         "je vais interroger la base de donnees distante",
         "verifions les permissions du dossier partage"],
        "une requete longue ici", "low",
    )


# ══════════════════════════════════════════════════════════════════════════
#  4. La boucle garde ses MUTATIONS — §15
# ══════════════════════════════════════════════════════════════════════════


def test_l_historique_des_pensees_reste_gere_dans_la_boucle():
    """`_previous_thoughts.append(...)` et la troncature a 5 sont des
    mutations : elles restent dans `_run_internal`."""
    src = REACT.read_text(encoding="utf-8")
    i = src.index("thought_is_stagnant(")
    bloc = src[i:i + 900]
    assert "_previous_thoughts.append" in bloc, (
        "l'ajout a l'historique a quitte la boucle"
    )
    assert "_previous_thoughts[-5:]" in bloc, (
        "la troncature de l'historique a quitte la boucle"
    )


def test_le_squelette_de_la_boucle_est_INTACT():
    ri = _run_internal()
    conts = sum(1 for x in ast.walk(ri) if isinstance(x, ast.Continue))
    rets = sum(1 for x in ast.walk(ri) if isinstance(x, ast.Return))
    trys = sum(1 for x in ast.walk(ri) if isinstance(x, ast.Try))
    assert (conts, rets, trys) == (48, 33, 77), (
        f"le squelette a bouge : continue={conts} return={rets} try={trys}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  5. LE critere du §15 — l'etat partage diminue
# ══════════════════════════════════════════════════════════════════════════


def test_les_locales_de_la_feuille_ont_DISPARU_de_la_boucle():
    """Le seul gain qui compte. Ces onze locales ne servaient qu'au calcul de
    stagnation ; elles n'ont plus de raison de vivre dans une fonction de
    5 800 lignes."""
    ri = _run_internal()
    locales = {x.id for x in ast.walk(ri)
               if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}
    for mort in ("_current_words", "_last_words", "_overlap", "_overlap2",
                 "_prev_words", "_q_words", "_base_thresh", "_thresh",
                 "_recent_3", "_common_prefix", "_all_share"):
        assert mort not in locales, (
            f"`{mort}` vit encore dans la boucle : la feuille n'a pas emporte "
            f"son etat"
        )


def test_l_etat_partage_a_DIMINUE():
    ri = _run_internal()
    locales = {x.id for x in ast.walk(ri)
               if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}
    assert len(locales) <= 655, (
        f"{len(locales)} locales — RF-9a en avait laisse 664, cette feuille "
        f"doit en emporter onze"
    )
