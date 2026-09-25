"""Lot GATE-2 - le comptage DECLENCHE, `node --check` ARBITRE.

--- Ce que le corpus a mesure le 24/09/2026 ---

Sur les 246 evenements de `data/logs/codeagent/gate_metrics.jsonl` (03/08 -> 24/09),
`JS_UNBALANCED_SYNTAX` est le **premier motif d'echec du gate**, 23 occurrences.

Le run de 03:04 en donne la demonstration complete, dans les journaux :

    gate      : « parentheses desequilibrees: 120 '(' vs 121 ')' »
    node      : `node --check static/js/app.js` -> exit 0
    comptage  : 262 '(' et 262 ')' verifies a la main

Le CodeAgent a passe une quinzaine d'iterations a se defendre, puis a fini par
**ajouter une parenthese dans un commentaire** pour « equilibrer le compteur du
gate ». Un garde faux n'obtient pas de la rigueur : il obtient du contournement.

--- Pourquoi ce lot est si court ---

`node --check` etait **deja** dans le depot : `src/utils/syntax_check.py`, branche
sur `files.py` et `codex_codeagent.py` depuis le LOT 8a. Le gate, lui, comptait les
parentheses a la main. Le fait existait, il etait ecrit, teste, utilise ailleurs -
et il ne servait pas a decider. C'est le troisieme cas du meme motif dans la
journee, apres le perimetre du revert et le corps de l'erreur HTTP.

Ce lot ne change donc pas l'architecture : il branche l'arbitre autoritatif, et
garde le comptage comme declencheur gratuit.
"""
from __future__ import annotations

import shutil

import pytest

from src.tools.code_validator import _arbitrer_js_par_node, _validate_js

node_absent = pytest.mark.skipif(
    shutil.which("node") is None, reason="node requis pour l'arbitrage")


# ── 1. Le faux positif reel du 24/09 ────────────────────────────────────────

# La faille MESUREE de `_strip_js_strings_and_comments` : c'est un automate a etats
# correct sur les chaines, les echappements et les commentaires - mais il ne connait
# pas les **litteraux d'expression reguliere**. En mode code, un `/` qui n'est suivi
# ni de `/` ni de `*` est du code ordinaire, donc les delimiteurs a l'interieur de
# `/\(/g` sont COMPTES.
#
# Mesure du 24/09 sur ce motif : 4 '(' contre 3 ')' au comptage, et `node --check`
# exit 0. Le gate refusait donc un fichier valide.
#
# Premiere version de ce test : un motif de parentheses dans des chaines HTML. Il
# passait au vert... parce que le strip le gere tres bien - le test ne prouvait
# rien. Mesurer avant d'affirmer, y compris pour ses propres tests.
# Une SEULE regex : avec `/\(/g` et `/\)/g` cote a cote, les deux parasites se
# compensent (8 contre 8) et le declencheur ne part plus - deuxieme piege evite
# de justesse dans l'ecriture de ce meme test.
FAUX_POSITIF = r'''const compterOuvrantes = (s) => {
  return (s.match(/\(/g) || []).length;
};
console.log(compterOuvrantes("un (deux) trois"));
'''


@node_absent
def test_le_faux_positif_mesure_ne_produit_plus_derreur():
    """Le cas qui a fait perdre quinze iterations au CodeAgent.

    C'est le test qui justifie le lot : node accepte ce fichier, donc le gate
    ne doit plus le refuser.

    Les deux moities sont verifiees. Sans la premiere, ce test redeviendrait creux
    le jour ou quelqu'un ameliorerait le strip : il passerait au vert sans plus rien
    exercer, exactement comme sa premiere version.
    """
    from src.tools.code_validator import _strip_js_strings_and_comments
    stripped = _strip_js_strings_and_comments(FAUX_POSITIF)
    assert stripped.count("(") != stripped.count(")"), (
        "le comptage naif ne declenche plus sur ce motif : ce test n'exerce plus rien, "
        "il faut lui trouver un nouveau faux positif mesure"
    )

    verdict, _ = _arbitrer_js_par_node("static/js/app.js", FAUX_POSITIF)
    assert verdict == "ok", "node devrait accepter ce fichier"

    issues = _validate_js("static/js/app.js", FAUX_POSITIF, {})
    assert not [i for i in issues if i.code == "JS_UNBALANCED_SYNTAX"], (
        "le gate refuse encore un fichier que node accepte"
    )


# ── 2. Une VRAIE erreur reste une erreur, et mieux nommee ───────────────────

@node_absent
def test_une_vraie_erreur_de_syntaxe_est_toujours_refusee():
    """Brancher un arbitre ne doit pas desarmer le gate.

    Le risque symetrique de ce lot est de tout laisser passer. node doit refuser
    ce qui est reellement casse.
    """
    casse = "function f() {\n  const x = 1;\n  return x;\n"  # accolade jamais fermee
    verdict, detail = _arbitrer_js_par_node("app.js", casse)
    assert verdict == "erreur", f"node aurait du refuser : {detail!r}"

    issues = _validate_js("app.js", casse, {})
    fautes = [i for i in issues if i.code == "JS_UNBALANCED_SYNTAX"]
    assert fautes, "une vraie erreur de syntaxe n'est plus signalee"
    assert "node --check" in fautes[0].message, (
        "le message doit venir de node, qui porte la ligne et la colonne"
    )


@node_absent
def test_le_message_de_node_remplace_un_comptage_muet():
    """Le comptage disait « 120 vs 121 » sans jamais dire OU. node le dit."""
    casse = "const o = {\n  a: 1,\n  b: (2,\n};\n"
    issues = _validate_js("app.js", casse, {})
    fautes = [i for i in issues if i.code == "JS_UNBALANCED_SYNTAX"]
    assert fautes
    # Le detail de node cite le fichier et la ligne fautive.
    assert len(fautes[0].message) > len("node --check : "), "message de node vide"


# ── 3. Ce que node ne sait pas juger, on le DIT ─────────────────────────────

@pytest.mark.parametrize("chemin", ["c.jsx", "d.ts", "e.tsx"])
def test_jsx_et_typescript_sont_annonces_non_verifiables(chemin):
    """node --check refuserait du JSX/TS valide : reproduire la faute en sens
    inverse ne vaudrait pas mieux que la faute d'origine."""
    verdict, detail = _arbitrer_js_par_node(chemin, FAUX_POSITIF)
    assert verdict == "non_verifiable"
    assert "node --check ne valide pas" in detail


def test_sans_arbitrage_le_soupcon_est_annonce_comme_tel(monkeypatch):
    """node absent ou muet : le gate refuse encore, mais ne pretend pas prouver.

    C'est la lecon du LOT 8a appliquee ici : « pas d'outil » ne veut dire ni
    « correct » ni « casse ». Le message doit porter le doute.
    """
    monkeypatch.setattr(
        "src.tools.code_validator._arbitrer_js_par_node",
        lambda *_a, **_k: ("non_verifiable", "node introuvable"),
    )
    desequilibre = "function f() {\n  return (1;\n}\n"
    issues = _validate_js("app.js", desequilibre, {})
    fautes = [i for i in issues if i.code == "JS_UNBALANCED_SYNTAX"]
    assert fautes, "sans arbitre, un desequilibre doit rester signale"
    assert "non confirmé" in fautes[0].message, fautes[0].message


# ── 4. Gratuit en regime normal ─────────────────────────────────────────────

def test_aucun_arbitrage_sur_un_fichier_equilibre(monkeypatch):
    """L'arbitre ne doit couter un processus que lorsque le comptage declenche.

    `validate_project` passe sur tous les fichiers d'un projet : lancer node
    partout rendrait le gate lent, donc contournable pour une autre raison.
    """
    appels = []
    monkeypatch.setattr(
        "src.tools.code_validator._arbitrer_js_par_node",
        lambda *a, **k: appels.append(a) or ("ok", ""),
    )
    _validate_js("app.js", "function f() {\n  return 1;\n}\n", {})
    assert appels == [], "node a ete lance sur un fichier equilibre"


# ── 5. Une seule source de verite ───────────────────────────────────────────

def test_larbitre_utilise_le_verificateur_du_depot():
    """Pas un second appel a node ecrit a cote : celui de `syntax_check`.

    Deux implementations divergeraient, et on retomberait sur le defaut d'origine
    - deux sources de verite dont une seule decide.
    """
    from src.utils import syntax_check
    assert callable(syntax_check.verifier_js_par_node)
    import inspect
    source = inspect.getsource(_arbitrer_js_par_node)
    assert "verifier_js_par_node" in source
    # On cherche un APPEL concurrent, pas la chaine « --check » : elle figure
    # legitimement dans la prose qui explique la limite JSX/TS.
    for motif in ("subprocess", "shutil.which", "Popen", "os.system"):
        assert motif not in source, (
            f"l'arbitre lance node lui-meme ({motif}) au lieu de passer par syntax_check"
        )
