"""Z41 — un chemin nomme par l'utilisateur ne se devine pas.

Cause racine du run Z40a : le CodeAgent s'etait retrouve enferme dans
`workspace/tests` avec `conf=0.80`, et y avait perdu 5 iterations sur 9.

--- Le mecanisme, MESURE (pas suppose) ---

    find_project("… Construis LogStat … dans workspace/logstat")
      etape 1  extrait 'workspace/logstat'
               `_abs.is_dir()` -> False (le dossier n'existe pas ENCORE)
               -> le chemin extrait est JETE
      etape 3  match flou : le slug 'tests' fait UN mot, la query contient
               « tests » -> score = 1/1 = 1.00 -> rend workspace/tests

**Le fait existe — l'utilisateur a ECRIT le chemin — il est extrait, puis jete
avant la decision.**

--- Mesure sur `data/sessions.sqlite` (34 928 messages, 906 requetes) ---

    nommant explicitement un workspace/X       84
    dont le dossier n'existe pas encore        79
    DETOURNEES vers un autre dossier      79 / 79   (100 %)

        projet-demo     31
        lumena-projet   24
        tests           19   <- le symptome de Z40a
        autres           5

Le defaut est **quatre fois plus large** que le symptome qui l'a revele.

--- La DEUXIEME face ---

Rendre `None` ne suffit pas : `resolve_workspace` etape 4 cree alors
`WORKSPACE_DIR / date / _generate_slug(query)` — un slug fabrique a partir de
la GRAMMAIRE de la requete :

    voulu 'starquest3d'        -> cree 'projet-faudrais-corriger-erreur'
    voulu 'lumena-landing'     -> cree 'projet-reprends-existant-users'
    voulu 'lumena-landing-new' -> cree 'projet-alaors-maintenant-corrige'

**79 sur 79 different.** Les deux moities vont ensemble : la premiere seule
remplacerait un detournement par un dossier au nom absurde.

--- Le piege verifie ---

Le workspace a 35 dossiers dates. Si `workspace/X` n'existe pas mais que
`workspace/2026-04-26/X` existe, refuser le repli creerait un DOUBLON — defaut
deja enregistre dans l'histoire du depot. Mesure : 0 des 79. Le mecanisme reste
reel, donc la recherche en dossier date se fait AVANT le refus.

--- Ce que ce lot NE fait PAS ---

Aucune liste de mots. Le correctif que j'avais annonce — « refuser les noms
generiques comme tests/src/build » — aurait ete du devinement de vocabulaire,
l'erreur exacte que la docstring de `browser_verify_task_blocks` (Z3b)
interdit : « on ne devine pas l'intention avec du vocabulaire ». La regle de
Z41 est structurelle : **un chemin explicitement nomme prime sur toute
devinette.**
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

import src.utils.project_registry as pr
from src.utils.project_registry import find_project, resolve_workspace


@pytest.fixture
def faux_workspace(tmp_path, monkeypatch):
    """Un workspace isole, avec le piege exact du run : un dossier au nom
    generique deja enregistre, et un projet reel sans rapport."""
    ws = tmp_path / "workspace"
    (ws / "tests").mkdir(parents=True)
    (ws / "tests" / "test_x.py").write_text("x = 1\n", encoding="utf-8")
    (ws / "projet-demo").mkdir()
    (ws / "projet-demo" / "app.py").write_text("y = 1\n", encoding="utf-8")

    reg = tmp_path / "project_registry.json"
    monkeypatch.setattr(pr, "WORKSPACE_DIR", ws)
    monkeypatch.setattr(pr, "ROOT_DIR", tmp_path)
    monkeypatch.setattr(pr, "_REGISTRY_PATH", reg)
    pr.save_registry([
        {"slug": "tests", "path": str(ws / "tests"),
         "description": "", "created": "2026-01-01T00:00:00",
         "last_accessed": "2026-01-01T00:00:00"},
        {"slug": "projet-demo", "path": str(ws / "projet-demo"),
         "description": "", "created": "2026-01-01T00:00:00",
         "last_accessed": "2026-01-01T00:00:00"},
    ])
    return ws


#: L'intitule du run reel, forme conservee.
DU_RUN = (
    "Lance ca comme une mission autonome en arriere-plan. Construis LogStat, "
    "echeance 30 min, dans workspace/logstat. Ecris des tests pytest et "
    "verifie que les tests passent."
)


# ══════════════════════════════════════════════════════════════════════════
#  1. Le defaut, reproduit tel quel
# ══════════════════════════════════════════════════════════════════════════


def test_le_chemin_nomme_ne_part_PAS_vers_un_autre_dossier(faux_workspace):
    """LE test du lot. C'est le detournement des 79/79 qui doit disparaitre."""
    trouve = find_project(DU_RUN)
    assert trouve is None or Path(trouve).name == "logstat", (
        f"la requete nomme 'workspace/logstat' et le resolveur rend "
        f"'{Path(trouve).name if trouve else None}'"
    )


def test_le_mot_du_TRAVAIL_n_ecrase_pas_le_dossier_DESIGNE(faux_workspace):
    """Le mecanisme exact : le slug 'tests' fait un seul mot, donc un seul mot
    de la query lui donne le score maximum 1.00 — devant le dossier que
    l'utilisateur a pourtant designe."""
    trouve = find_project(DU_RUN)
    assert not (trouve and Path(trouve).name == "tests"), (
        "le mot qui decrit le TRAVAIL (« tests ») a battu le dossier DESIGNE"
    )


def test_sans_chemin_nomme_le_match_flou_garde_son_travail(faux_workspace):
    """Le garde-fou du lot : Z41 ne desactive pas la recherche floue, il lui
    interdit seulement de passer DEVANT un chemin explicite."""
    trouve = find_project("continue le projet demo, il faut corriger app.py")
    assert trouve is not None and Path(trouve).name == "projet-demo", (
        f"la recherche floue ne fonctionne plus du tout : {trouve}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  2. Le chemin nomme QUI EXISTE continue de gagner (aucune regression)
# ══════════════════════════════════════════════════════════════════════════


def test_un_chemin_nomme_existant_est_rendu(faux_workspace):
    (faux_workspace / "logstat").mkdir()
    trouve = find_project(DU_RUN)
    assert trouve is not None and Path(trouve).name == "logstat"


def test_le_projet_est_retrouve_dans_un_dossier_DATE(faux_workspace):
    """Le piege verifie a l'audit : refuser le repli sans chercher dans les
    dossiers dates creerait un DOUBLON — defaut deja vu dans l'histoire du
    depot (« HuffPack v1 publie ecrase 2x en 1 h »)."""
    date_dir = faux_workspace / "2026-04-26" / "logstat"
    date_dir.mkdir(parents=True)
    (date_dir / "main.py").write_text("z = 1\n", encoding="utf-8")

    trouve = find_project(DU_RUN)
    assert trouve is not None, "le projet existant en dossier date n'est pas retrouve"
    assert Path(trouve).resolve() == date_dir.resolve(), (
        f"un DOUBLON serait cree : {trouve}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  3. La DEUXIEME face — la creation respecte le nom donne
# ══════════════════════════════════════════════════════════════════════════


def test_la_creation_porte_le_NOM_DONNE_pas_un_slug_de_grammaire(faux_workspace):
    """79 sur 79 des slugs generes differaient du nom voulu, et prenaient la
    forme « projet-faudrais-corriger-erreur »."""
    r = resolve_workspace(DU_RUN, allow_create=True)

    assert r.path is not None, "rien n'a ete resolu ni cree"
    assert Path(r.path).name == "logstat", (
        f"le dossier cree ne porte pas le nom demande : '{Path(r.path).name}' "
        f"(source={r.source})"
    )


def test_la_creation_sans_nom_donne_garde_le_slug_historique(faux_workspace):
    """Le garde-fou de la deuxieme moitie : quand l'utilisateur ne nomme
    AUCUN chemin, `_generate_slug` garde son travail."""
    r = resolve_workspace("cree un petit outil de conversion de temperatures",
                          allow_create=True)
    assert r.path is not None
    assert Path(r.path).name.startswith("projet-"), (
        f"le slug historique a disparu : {Path(r.path).name}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  4. Ce que le lot ne doit pas casser
# ══════════════════════════════════════════════════════════════════════════


def test_les_signatures_sont_inchangees():
    """`find_project` a des appelants dans react.py, agents.py et
    sub_agent.py ; `resolve_workspace` est le point d'entree UNIQUE documente."""
    import inspect

    assert list(inspect.signature(find_project).parameters) == ["query"]
    p = list(inspect.signature(resolve_workspace).parameters)
    assert p == ["query", "context", "allow_create"]


def test_find_project_rend_toujours_un_dossier_EXISTANT_ou_None(faux_workspace):
    """Contrat historique : le retour est un projet reel, jamais un chemin
    imaginaire. Le casser toucherait tous les appelants."""
    for q in (DU_RUN, "continue le projet demo", "bonjour ca va ?"):
        t = find_project(q)
        assert t is None or Path(t).is_dir(), f"{q!r} -> {t!r}"


def test_le_contexte_pre_resolu_gagne_toujours(faux_workspace):
    """Etape 1 de `resolve_workspace` : un `project_dir` deja resolu prime sur
    tout, y compris sur un chemin nomme dans la requete."""
    r = resolve_workspace(
        DU_RUN,
        context={"project_dir": str(faux_workspace / "projet-demo")},
        allow_create=False,
    )
    assert r.path is not None and Path(r.path).name == "projet-demo"
    assert r.confidence == 1.0


@pytest.mark.parametrize("q", [
    "ca va ?",
    "bah alors",
    "merci beaucoup",
])
def test_les_messages_conversationnels_ne_creent_rien(faux_workspace, q):
    """Un message sans intention de projet ne doit pas se mettre a nommer un
    dossier."""
    t = find_project(q)
    assert t is None or Path(t).is_dir()
