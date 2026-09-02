"""RF-9c — deux feuilles de relance : indice d'outils et rappel de listage.

Choisies au critere du §15 — l'etat partage, pas les lignes :

    ligne  taille  locales qui disparaissent
    4694      21     5   <- indice d'outils sur stagnation
    8459      28     4   <- rappel de listage repete
    5729      93     3   <- la plus GROSSE, toujours pas rentable

--- Les deux decisions ---

    stagnation_tool_hint(requete, outils_disponibles) -> str
    repeated_listing_reminder(deja_cree, requete)     -> str

Toutes deux rendent le TEXTE a poser. Les mutations restent dans la boucle :
la concatenation `_stagnation_warning` et `observation.content += ...`.

--- Ce que ces feuilles portent ---

**4694** — quand le modele piétine, on ne lui dit pas seulement « tu piétines » :
on lui NOMME les outils pertinents pour sa requete, parmi ceux reellement
disponibles. Un outil absent du registre ne doit jamais etre suggere.

**8459** — un `list_directory` repete n'a pas la meme reponse selon ce que
l'utilisateur demande. S'il demande de CREER, le rappel doit ordonner d'arreter
d'explorer ; s'il cherche, il doit ordonner de dire honnetement que le fichier
n'est pas la — et surtout **de ne pas inventer de fichier**.
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
#  1. Les deux decisions existent et sont PURES
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom,params", [
    ("stagnation_tool_hint", ["original_query", "available_tools"]),
    ("repeated_listing_reminder", ["already_created", "original_query"]),
])
def test_la_decision_existe_et_est_typee(nom, params):
    import src.reasoning.observation_synthesis as m

    fn = getattr(m, nom)
    assert list(inspect.signature(fn).parameters) == params
    assert inspect.signature(fn).return_annotation in (str, "str")


@pytest.mark.parametrize("nom", ["stagnation_tool_hint", "repeated_listing_reminder"])
def test_la_decision_est_PURE(nom):
    arbre = ast.parse(MODULE.read_text(encoding="utf-8"))
    fn = next(n for n in arbre.body
              if isinstance(n, ast.FunctionDef) and n.name == nom)
    interdits = []
    for x in ast.walk(fn):
        if isinstance(x, (ast.Await, ast.Global, ast.Nonlocal)):
            interdits.append(type(x).__name__)
        if isinstance(x, ast.Attribute) and isinstance(x.ctx, ast.Store):
            interdits.append("mutation ." + x.attr)
        if isinstance(x, ast.Call) and getattr(x.func, "attr", "") in (
                "debug", "info", "warning", "error"):
            interdits.append("journalisation")
    assert not interdits, f"{nom} n'est pas pure : {sorted(set(interdits))}"


# ══════════════════════════════════════════════════════════════════════════
#  2. L'indice d'outils — ne suggerer QUE ce qui existe
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("requete,attendu", [
    ("fais-moi un rapport pdf", "generate_studio_document"),
    ("cree une facture", "create_invoice_pdf"),
    ("construis un site web", "create_project"),
    ("genere une image", "generate_image"),
    ("envoie un mail au client", "send_email"),
])
def test_l_outil_pertinent_est_suggere(requete, attendu):
    from src.reasoning.observation_synthesis import stagnation_tool_hint

    hint = stagnation_tool_hint(requete, {attendu: object()})
    assert attendu in hint
    assert "Utilise-les directement" in hint


def test_un_outil_ABSENT_du_registre_n_est_jamais_suggere():
    """Le garde de la feuille : suggerer un outil que la boucle n'a pas
    enverrait le modele contre un mur."""
    from src.reasoning.observation_synthesis import stagnation_tool_hint

    assert stagnation_tool_hint("fais-moi un rapport pdf", {}) == ""
    hint = stagnation_tool_hint("fais-moi un rapport pdf", {"create_pdf": object()})
    assert "create_pdf" in hint
    assert "generate_studio_document" not in hint


def test_une_requete_hors_sujet_ne_suggere_rien():
    from src.reasoning.observation_synthesis import stagnation_tool_hint

    assert stagnation_tool_hint(
        "explique-moi la theorie des jeux", {"create_pdf": object()}
    ) == ""


def test_l_indice_est_plafonne_a_cinq_outils():
    from src.reasoning.observation_synthesis import stagnation_tool_hint

    tous = {t: object() for t in (
        "generate_studio_document", "generate_studio_documents",
        "list_document_models", "create_pdf", "create_docx",
        "create_invoice_pdf", "create_from_template",
    )}
    hint = stagnation_tool_hint("fais-moi un rapport pdf", tous)
    assert hint.count("`") == 10, "le plafond de 5 outils a saute"


def test_la_casse_de_la_requete_est_ignoree():
    from src.reasoning.observation_synthesis import stagnation_tool_hint

    assert "create_pdf" in stagnation_tool_hint(
        "FAIS-MOI UN RAPPORT PDF", {"create_pdf": object()}
    )


# ══════════════════════════════════════════════════════════════════════════
#  3. Le rappel de listage repete — trois issues distinctes
# ══════════════════════════════════════════════════════════════════════════


def test_creation_deja_faite_le_listage_est_de_la_navigation():
    from src.reasoning.observation_synthesis import repeated_listing_reminder

    msg = repeated_listing_reminder(True, "cree-moi trois fichiers")
    # Le message d'origine porte ses accents : « tu as déjà exploré ce chemin ».
    assert "déjà exploré ce chemin" in msg
    assert "STOP EXPLORATION" not in msg


def test_l_utilisateur_demande_de_CREER_on_ordonne_d_arreter():
    from src.reasoning.observation_synthesis import repeated_listing_reminder

    msg = repeated_listing_reminder(False, "crée-moi un rapport et écris les fichiers")
    assert "STOP EXPLORATION" in msg
    assert "write_file" in msg
    assert "PAS parallel_tools" in msg


def test_l_utilisateur_CHERCHE_on_ordonne_l_honnetete():
    """La branche qui compte : ne JAMAIS inventer un fichier absent."""
    from src.reasoning.observation_synthesis import repeated_listing_reminder

    msg = repeated_listing_reminder(False, "ou se trouve le fichier de config ?")
    assert "HONNETEMENT" in msg
    assert "NE CREE PAS de fichier invente" in msg
    assert "STOP EXPLORATION" not in msg


@pytest.mark.parametrize("verbe", [
    "créer", "creer", "crée", "génère", "genere", "rédige", "écris", "ecris",
    "prépare", "produis", "create", "write", "generate", "make", "build",
])
def test_chaque_verbe_de_creation_est_reconnu(verbe):
    from src.reasoning.observation_synthesis import repeated_listing_reminder

    msg = repeated_listing_reminder(False, f"peux-tu {verbe} le document")
    assert "STOP EXPLORATION" in msg, f"le verbe « {verbe} » n'est plus reconnu"


# ══════════════════════════════════════════════════════════════════════════
#  4. Les MUTATIONS restent dans la boucle — §15
# ══════════════════════════════════════════════════════════════════════════


def test_la_concatenation_de_l_observation_reste_dans_la_boucle():
    src = REACT.read_text(encoding="utf-8")
    i = src.index("repeated_listing_reminder(")
    bloc = src[i - 200:i + 400]
    assert "observation.content +=" in bloc, (
        "la concatenation a quitte la boucle : ce module ne doit rien muter"
    )


def test_le_squelette_de_la_boucle_est_INTACT():
    ri = _run_internal()
    conts = sum(1 for x in ast.walk(ri) if isinstance(x, ast.Continue))
    rets = sum(1 for x in ast.walk(ri) if isinstance(x, ast.Return))
    trys = sum(1 for x in ast.walk(ri) if isinstance(x, ast.Try))
    # 77 -> 78 : lot panel missions 14. La pensee du LEAD est parsee dans
    # cette boucle depuis toujours et n'etait ecrite qu'au log `debug` : la
    # carte « Lead » du panneau Missions restait vide sur TOUTES les
    # missions. L'emission ajoutee est defensive (le bus de trace ne doit
    # jamais faire tomber la boucle), d'ou un `try` de plus. Elle n'ajoute
    # ni `continue`, ni `return`, ni import local.
    assert (conts, rets, trys) == (48, 33, 78), (
        f"le squelette a bouge : continue={conts} return={rets} try={trys}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  5. LE critere du §15
# ══════════════════════════════════════════════════════════════════════════


def test_les_locales_des_feuilles_ont_DISPARU():
    ri = _run_internal()
    locales = {x.id for x in ast.walk(ri)
               if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}
    for mort in ("_STAG_KW_MAP", "_kws", "_q_low", "_stag_relevant", "_tools",
                 "_creation_keywords", "query_lower", "user_wants_creation"):
        assert mort not in locales, (
            f"`{mort}` vit encore dans la boucle : la feuille n'a pas emporte "
            f"son etat"
        )


def test_l_etat_partage_a_DIMINUE():
    ri = _run_internal()
    locales = {x.id for x in ast.walk(ri)
               if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Store)}
    assert len(locales) <= 645, (
        f"{len(locales)} locales — RF-9b en avait laisse 653, ces deux "
        f"feuilles doivent en emporter neuf"
    )
