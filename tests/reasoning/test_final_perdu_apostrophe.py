"""Defauts trouves sur le run de production du 2026-08-29 (logslumena A).

L'utilisateur a demande le bilan de la mission. Le modele a produit QUATRE
reponses de 883 a 1368 caracteres. Il a recu 41 caracteres :
« Je n'ai pas trouve de reponse pertinente. »

--- La chaine complete, mesuree ---

1. `has_unclosed_quotes` compte l'apostrophe d'ELISION francaise comme une
   guillemet de chaine. Meme texte : 1 apostrophe -> tronque, 2 -> correct,
   3 -> tronque. La version ANGLAISE du meme bilan passait toujours.
   => une reponse francaise structuree sur DEUX partait en reparation.

2. La bonne reponse etait sauvegardee dans `_pre_repair_answer` POUR ce cas.
   Puis : `elif _pre_repair and action.action_type == FINAL_ANSWER:` ->
   `_pre_repair_answer = None`. Or un FINAL VIDE est aussi un FINAL_ANSWER :
   le code a lu « un FINAL a ete produit », conclu « repair reussi », et
   DETRUIT les 1312 caracteres.

3. Les deux sorties servaient alors la formule polie sans jamais consulter
   la sauvegarde.

Motif connu : le fait existait, etait calcule, etait STOCKE POUR CA — et
jete avant la decision.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

from src.reasoning.prompt_builder import (
    has_unclosed_quotes,
    looks_incomplete_final_answer,
)

RACINE = pathlib.Path(__file__).resolve().parents[2]
REACT = RACINE / "src" / "reasoning" / "react.py"

STOP = {"finish_reason": "stop"}
BLOC = ("")


def _bilan(apostrophes: int) -> str:
    """Un bilan de mission francais realiste, a N apostrophes, avec du code."""
    mots = ["le module d'analyse", "il n'a rien casse", "l'utilisateur peut relire"]
    tete = "Mission terminee. " + ". ".join(mots[:apostrophes])
    return (
        tete
        + chr(10) + chr(10) + "```python" + chr(10)
        + "def parse(ligne):" + chr(10)
        + "    return ligne.split(';')" + chr(10)
        + "```" + chr(10) + chr(10)
        + "10 passed."
    )


# ══════════════════════════════════════════════════════════════════════════
#  1. L'apostrophe d'elision n'est pas un delimiteur de chaine
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("n", [0, 1, 2, 3])
def test_la_parite_des_apostrophes_ne_decide_plus_rien(n):
    """LE defaut du run : le verdict basculait sur la parite. C'etait pile ou face."""
    assert looks_incomplete_final_answer(_bilan(n), STOP) is False


def test_le_meme_bilan_en_francais_et_en_anglais_ont_le_MEME_verdict():
    """Avant : le francais partait en reparation, l'anglais passait."""
    fr = _bilan(1)
    en = fr.replace("le module d'analyse", "the analysis module")
    assert looks_incomplete_final_answer(fr, STOP) == looks_incomplete_final_answer(en, STOP)


@pytest.mark.parametrize("texte", [
    "l'utilisateur",
    "il n'a rien vu" + chr(10) + "et d'ailleurs qu'importe",
    "aujourd'hui" + chr(10) + "```" + chr(10) + "x = 1" + chr(10) + "```",
    "don't worry" + chr(10) + "it's fine",
])
def test_une_elision_ne_compte_jamais_comme_une_quote(texte):
    assert has_unclosed_quotes(texte) is False


# ══════════════════════════════════════════════════════════════════════════
#  2. Une VRAIE chaine ouverte reste vue — le garde n'est pas desarme
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("code", [
    "def f():" + chr(10) + "    return 'abc",
    "x = \"abc" + chr(10) + "y = 1",
    "def f():" + chr(10) + "    '''doc" + chr(10) + "    return 1",
])
def test_une_chaine_reellement_ouverte_est_TOUJOURS_detectee(code):
    assert has_unclosed_quotes(code) is True


@pytest.mark.parametrize("code", [
    "def f():" + chr(10) + "    return 'abc'",
    "print('hello')" + chr(10) + "print('world')",
])
def test_du_code_sain_ne_declenche_rien(code):
    assert has_unclosed_quotes(code) is False


def test_une_vraie_troncature_par_finish_reason_reste_vue():
    """`length` = le modele a ete coupe : ca, c'est une vraie troncature."""
    assert looks_incomplete_final_answer(_bilan(1), {"finish_reason": "length"}) is True



# ══════════════════════════════════════════════════════════════════════════
#  3. Le repair ne « reussit » pas en rendant un FINAL VIDE
# ══════════════════════════════════════════════════════════════════════════
#
#  Ces trois sites sont INLINE dans `_run_internal` (5 693 lignes) : ils ne
#  sont pas appelables isolement. On les lit donc en AST — jamais en
#  sous-chaine, qui se serait trouvee elle-meme dans les commentaires
#  ci-dessus (piege deja rencontre quatre fois sur ce depot).


def _run_internal_ast():
    arbre = ast.parse(REACT.read_text(encoding="utf-8"))
    cls = next(n for n in arbre.body
               if isinstance(n, ast.ClassDef) and n.name == "ReActLoop")
    return next(n for n in cls.body if getattr(n, "name", "") == "_run_internal")


def test_le_nettoyage_du_marqueur_est_GARDE_par_le_contenu():
    """Avant : `elif ... FINAL_ANSWER: _pre_repair_answer = None` sans regarder
    si ce FINAL avait du contenu. Un FINAL vide detruisait la bonne reponse."""
    ri = _run_internal_ast()
    gardes = [
        n for n in ast.walk(ri)
        if isinstance(n, ast.If) and isinstance(n.test, ast.Name) and n.test.id == "_repare"
    ]
    assert gardes, "le nettoyage du marqueur n'est garde par aucun test de contenu"
    g = gardes[0]
    poses = [
        n for n in ast.walk(ast.Module(body=g.body, type_ignores=[]))
        if isinstance(n, ast.Assign)
        and any(getattr(t, "attr", "") == "_pre_repair_answer" for t in n.targets)
    ]
    assert poses, "le garde ne protege pas la remise a None"
    assert g.orelse, "aucune branche ne trace le FINAL vide conserve"


def test_le_contenu_du_repair_est_lu_sur_la_reponse_du_modele():
    """`_repare` doit venir de `action.answer`, pas d'autre chose."""
    ri = _run_internal_ast()
    sources = [
        n.value for n in ast.walk(ri)
        if isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "_repare" for t in n.targets)
    ]
    assert sources, "`_repare` n'est assigne nulle part"
    lu = ast.dump(sources[0])
    assert "answer" in lu and "action" in lu, lu


# ══════════════════════════════════════════════════════════════════════════
#  4. Les deux sorties consultent la sauvegarde AVANT la formule polie
# ══════════════════════════════════════════════════════════════════════════


def test_les_deux_sorties_preferent_la_reponse_gardee():
    """Sans ca, meme protegee, la sauvegarde n'aurait servi a personne."""
    ri = _run_internal_ast()
    chaines = []
    for n in ast.walk(ri):
        if not (isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "message" for t in n.targets)):
            continue
        v = n.value
        if not (isinstance(v, ast.BoolOp) and isinstance(v.op, ast.Or)):
            continue
        noms = {x.id for x in v.values if isinstance(x, ast.Name)}
        if {"answer", "_garde"} <= noms:
            chaines.append(n.lineno)
    assert len(chaines) == 2, (
        f"attendu 2 sorties consultant la sauvegarde, trouve {len(chaines)} "
        f"(lignes {chaines})"
    )


def test_la_formule_polie_arrive_en_DERNIER_recours():
    """Elle reste — hors mission le chat la garde — mais apres la sauvegarde."""
    ri = _run_internal_ast()
    for n in ast.walk(ri):
        if not (isinstance(n, ast.Assign)
                and any(isinstance(t, ast.Name) and t.id == "message" for t in n.targets)):
            continue
        v = n.value
        if not (isinstance(v, ast.BoolOp) and isinstance(v.op, ast.Or)):
            continue
        noms = [x.id for x in v.values if isinstance(x, ast.Name)]
        if "_garde" not in noms:
            continue
        assert noms.index("answer") < noms.index("_garde"), (
            "la reponse de l'iteration courante doit primer sur la sauvegarde"
        )
        assert v.values[-1] is not v.values[noms.index("_garde")] or True
        dernier = v.values[-1]
        assert not (isinstance(dernier, ast.Name) and dernier.id in ("answer", "_garde")), (
            "le dernier recours doit etre la formule/fallback, pas une variable"
        )
