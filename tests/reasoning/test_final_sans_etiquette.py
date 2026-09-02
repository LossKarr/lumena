"""UN FINAL SANS ÉTIQUETTE RESTE UN FINAL.

═══════════════════════════════════════════════════════════════════════════════
  CE QUI A ÉTÉ OBSERVÉ, SUR UN RUN RÉEL
═══════════════════════════════════════════════════════════════════════════════

Log du 02/09, 944 lignes. Neuf déclenchements du garde Z29, TOUS du même cas —
`final_sans_contenu`, aucun autre. Sur le tour qui a échoué :

    itér. 4   réponse 1123 caractères   →   FINAL extrait : 0
    itér. 5   réponse 1001 caractères   →   FINAL extrait : 0
    itér. 6   réponse  990 caractères   →   FINAL extrait : 0
    itér. 7   réponse 1346 caractères   →   FINAL extrait : 0
    ────────────────────────────────────────────────────────
    servi à l'utilisateur                :   41 caractères

Le modèle ne renvoyait PAS du vide : il renvoyait un kilo-octet à chaque fois.

═══════════════════════════════════════════════════════════════════════════════
  LA CAUSE
═══════════════════════════════════════════════════════════════════════════════

`extract_action_input` cherchait le libellé `ACTION_INPUT:` et faisait
`return ""` s'il ne le trouvait pas — AVANT même d'atteindre la branche FINAL.
Quand le modèle écrit sa réponse juste après `ACTION: FINAL`, sans étiquette,
le texte complet était jeté.

Et la réparation ne pouvait pas aboutir : elle demandait au modèle de
reformuler le CONTENU, alors que le défaut était l'ÉTIQUETTE. Il reformulait
dans le même format. Quatre fois.

Asymétrie qui rendait le défaut fatal : un appel d'OUTIL sans `ACTION_INPUT:`
disposait de replis (JSON équilibré, frontière au libellé suivant). Le FINAL,
lui, sortait avant tout repli — c'était le seul cas où l'absence tuait.

═══════════════════════════════════════════════════════════════════════════════
  POURQUOI LE CORRECTIF EST INCONDITIONNEL
═══════════════════════════════════════════════════════════════════════════════

Le dépôt porte **41 profils modèle** (29 modèles + 10 fournisseurs). Aucun ne
touche à l'extraction : leur `parser_severity` ne règle QUE le budget de
réparation — un bouton qui porte le nom d'un parseur sans jamais l'atteindre.

Et l'omission d'étiquette n'est pas une lubie d'un modèle : LangChain,
LlamaIndex et CrewAI ont le même ticket ouvert, le comportement varie d'un
modèle à l'autre ET d'une version à l'autre (le run montre d'ailleurs un modèle
mort en vol, `deepseek-v4-flash`, arrivé en fin de vie). Une rustine par modèle
est une course perdue — le code en portait déjà une, pour Kimi.
"""

from __future__ import annotations

import pytest

from src.reasoning.response_parser import parse_response
from src.reasoning.react import ActionType

N = chr(10)


def _rep(txt):
    _t, a, _h, _p = parse_response(txt)
    return a


# ══════════════════════════════════════════════════════════════════════════
#  1. LE DÉFAUT MESURÉ
# ══════════════════════════════════════════════════════════════════════════


def test_un_final_SANS_etiquette_n_est_plus_jete():
    """Le cas exact du log : la réponse suit `ACTION: FINAL` sans libellé."""
    a = _rep("THOUGHT: Mission terminée, je livre le résumé." + N
             + "ACTION: FINAL" + N + N
             + "C'est fait ! Les 3 fichiers sont créés." + N
             + "Tout est vérifié.")
    assert a.action_type == ActionType.FINAL_ANSWER
    assert "C'est fait" in a.answer and "Tout est vérifié." in a.answer


def test_un_final_AVEC_etiquette_marche_toujours():
    """Le chemin nominal ne bouge pas."""
    a = _rep("THOUGHT: t" + N + "ACTION: FINAL" + N
             + "ACTION_INPUT: La réponse étiquetée.")
    assert a.answer == "La réponse étiquetée."


def test_l_etiquette_INLINE_marche_toujours():
    """Le repli Kimi historique — tout sur la même ligne."""
    a = _rep("THOUGHT: t" + N + "ACTION: FINAL ACTION_INPUT: réponse inline")
    assert a.answer == "réponse inline"


@pytest.mark.parametrize("taille", [50, 500, 1123, 1346])
def test_une_reponse_LONGUE_arrive_entiere(taille):
    """Les quatre finals perdus du log faisaient 990 à 1346 caractères."""
    corps = "x" * taille
    a = _rep("THOUGHT: t" + N + "ACTION: FINAL" + N + corps)
    assert len(a.answer) == taille


# ══════════════════════════════════════════════════════════════════════════
#  2. CE QUE LE CORRECTIF NE DOIT PAS CASSER
# ══════════════════════════════════════════════════════════════════════════


def test_un_final_VRAIMENT_vide_reste_vide():
    """La réparation doit encore se déclencher quand il n'y a rien : sinon on
    échangerait un faux négatif contre un faux positif."""
    assert _rep("THOUGHT: t" + N + "ACTION: FINAL").answer == ""


def test_des_espaces_ne_sont_PAS_une_reponse():
    assert _rep("THOUGHT: t" + N + "ACTION: FINAL" + N + N + "   " + N).answer == ""


def test_le_THOUGHT_ne_devient_JAMAIS_la_reponse():
    """Invariant du parseur, écrit dans son propre commentaire : « ne jamais
    recycler thought_content comme réponse finale ». Le correctif le tient PAR
    CONSTRUCTION — il ne lit que ce qui suit la ligne `ACTION:`, et la pensée
    est écrite au-dessus."""
    pensee = "Je dois maintenant livrer la confirmation à l'utilisateur."
    a = _rep("THOUGHT: " + pensee + N + "ACTION: FINAL")
    assert a.answer == ""
    assert pensee not in (a.answer or "")


def test_un_bloc_SUIVANT_n_est_pas_avale():
    a = _rep("THOUGHT: t" + N + "ACTION: FINAL" + N + "La réponse." + N
             + "THOUGHT: parasite")
    assert a.answer == "La réponse."


def test_une_OBSERVATION_hallucinee_n_est_pas_avalee():
    a = _rep("THOUGHT: t" + N + "ACTION: FINAL" + N + "La réponse." + N
             + "OBSERVATION: fausse")
    assert "fausse" not in a.answer


def test_un_ACTION_INPUT_plus_bas_reste_prioritaire():
    """Si l'étiquette existe, c'est elle qui fait foi — le repli ne doit pas
    la court-circuiter."""
    a = _rep("THOUGHT: t" + N + "ACTION: FINAL" + N + N
             + "ACTION_INPUT: la vraie réponse")
    assert a.answer == "la vraie réponse"


# ══════════════════════════════════════════════════════════════════════════
#  3. LE REPLI EST RÉSERVÉ AU FINAL
# ══════════════════════════════════════════════════════════════════════════


def test_un_OUTIL_sans_etiquette_ne_recupere_PAS_du_texte_libre():
    """Un appel d'outil a besoin d'ARGUMENTS, pas de prose. Lui servir le
    texte qui suit produirait des arguments inventés — bien pire qu'un vide."""
    from src.reasoning.response_parser import extract_action_input
    txt = "THOUGHT: t" + N + "ACTION: write_file" + N + "du texte libre"
    assert extract_action_input(txt, txt.index("ACTION: write_file"), "write_file") == ""


def test_le_repli_ne_s_applique_qu_a_FINAL():
    from src.reasoning.response_parser import extract_action_input
    txt = "ACTION: FINAL" + N + "contenu"
    assert extract_action_input(txt, 0, "FINAL") == "contenu"
    assert extract_action_input(txt, 0, "read_file") == ""


# ══════════════════════════════════════════════════════════════════════════
#  4. LE GARDE Z29 NE DOIT PLUS SE DÉCLENCHER SUR CE CAS
# ══════════════════════════════════════════════════════════════════════════


def test_le_cas_du_log_ne_produit_plus_final_sans_contenu():
    """Reconstitution de la forme qui a coûté 41 caractères à l'utilisateur.

    Le garde Z29 s'arme quand `answer` est vide ET que la pensée existe ET que
    l'action est FINAL. Ici la réponse existe : les trois conditions ne peuvent
    plus être réunies."""
    a = _rep(
        "THOUGHT: La mission est terminée, je dois donner les détails concrets." + N
        + "ACTION: FINAL" + N + N
        + "Voici le bilan concret :" + N + N
        + "- **État** : terminée" + N
        + "- **Workers** : 3/3 ont fini leur boulot" + N
        + "- **Livrable** : publié dans `workspace/livrable_5c45e1cc`"
    )
    assert a.action_type == ActionType.FINAL_ANSWER
    assert len(a.answer.strip()) > 0, "le garde Z29 se rearmerait"
    assert "3/3" in a.answer and "livrable_5c45e1cc" in a.answer


def test_la_mise_en_forme_MARKDOWN_survit():
    """La réponse réelle est du Markdown : listes, gras, chemins entre accents
    graves. Rien ne doit être rogné."""
    corps = ("**Résumé :**" + N + N + "- 📄 `test.txt` (100 caractères)" + N
             + "- 📍 `C:" + chr(92) + "chemin" + chr(92) + "test.txt`")
    a = _rep("THOUGHT: t" + N + "ACTION: FINAL" + N + corps)
    assert a.answer == corps


# ══════════════════════════════════════════════════════════════════════════
#  5. LE CONSTAT D'AUDIT — un bouton qui porte le nom d'un parseur
# ══════════════════════════════════════════════════════════════════════════


def test_parser_severity_ne_touche_PAS_au_parseur():
    """CONSTAT, pas correctif.

    Les 41 profils du dépôt exposent `parser_severity` (strict / lenient /
    forgiving). Ce reglage ne change RIEN à l'extraction : son unique effet est
    de relever `max_final_repair_attempts`. C'est un budget de réparation qui
    porte le nom d'un parseur — et c'est pourquoi quatre réparations d'affilée
    ont échoué sur le run : redemander plus souvent à un parseur inchangé donne
    le même échec, en plus lent.

    Ce test FIGE le constat pour qu'il ne se perde pas. Le jour où
    `parser_severity` agira vraiment sur l'extraction, il rougira, et ce sera
    une bonne nouvelle qu'il faudra alors documenter."""
    import pathlib
    import re
    src = pathlib.Path(__file__).parents[2] / "src" / "reasoning" / "response_parser.py"
    txt = re.sub(r'"""[\s\S]*?"""', "", src.read_text(encoding="utf-8"))
    txt = re.sub(r"^\s*#.*$", "", txt, flags=re.M)
    assert "parser_severity" not in txt, (
        "parser_severity atteint enfin le parseur — mettre a jour ce constat"
    )


def test_le_correctif_est_INCONDITIONNEL():
    """Pas un 42e profil : l'omission d'etiquette varie d'un modele a l'autre
    ET d'une version a l'autre. Le repli ne doit dependre d'aucun modele."""
    import pathlib
    import re
    src = pathlib.Path(__file__).parents[2] / "src" / "reasoning" / "response_parser.py"
    txt = src.read_text(encoding="utf-8")
    i = txt.index('if action_name.upper() == "FINAL":' )
    bloc = txt[i:i + 900]
    for mot in ("deepseek", "kimi", "moonshot", "provider", "model_profile"):
        assert mot not in bloc.lower(), f"le repli depend du modele : {mot}"
