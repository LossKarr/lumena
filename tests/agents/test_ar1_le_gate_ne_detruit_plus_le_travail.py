r"""Lot AR-1 - le gate de tests post-edit ne detruit plus un travail correct.

--- Le defaut, mesure au code le 25/09/2026 ---

`CodeAgent._find_related_tests` (`sub_agent.py` l.5924-5942) :

    root = Path(__file__).parent.parent.parent     # la racine de LUMENA, TOUJOURS
    tests_dir = root / "tests"
    mod_name = Path(modified_file).stem            # ex. « config »
    for tf in tests_dir.glob("test_*.py"):         # non recursif : 7 fichiers
        if mod_name in content or mod_import in content:
            results.append(tf.name)                # nom NU, pas un chemin

Trois defauts qui se combinent :

1. **L'ancre est le depot Lumena**, quel que soit le projet ou elle travaille.
2. **Le match est une SOUS-CHAINE du CONTENU** du test, pas un import.
3. **Les resultats sont des noms nus**, resolus ensuite contre le cwd du projet.

Puis, l.5422 : `_tests_imputables = (not _perim_bonus) or any(...)`. Hors mission,
`_allowed_files` est vide, donc l'echec est TOUJOURS juge imputable, donc
`_rollback_session()` **annule son travail**.

--- Mesure de l'ampleur ---

Sur 23 noms de module courants, **20 declenchent le gate a tort** contre les 7 tests du
depot Lumena :

    app      -> 6 tests      main    -> 2      config -> 1
    log      -> 5 tests      server  -> 2      utils  -> 1
    service  -> 5 tests      run     -> 2      game   -> 1
    image    -> 4 tests      compose -> 2      world  -> 1
    handlers -> 4 tests      models  -> 3      state  -> 1
    api      -> 3 tests      core    -> 1      auth   -> 1

Autrement dit : presque chaque fichier Python au nom banal.

--- Le choix de conception ---

Le correctif est dans l'ANCRE, pas dans la condition d'imputabilite.

Si la recherche part du projet ou elle travaille (`_task_workspace_root`, sinon le
dossier du fichier modifie), alors dans un projet tiers sans dossier `tests/` **aucun
test n'est trouve** : pas de gate, donc pas de revert. Et quand elle travaille SUR
Lumena, l'ancre redevient le depot Lumena et le comportement d'origine est preserve -
ce qui etait le cas legitime que le code visait.

**La condition `(not _perim_bonus) or ...` reste donc INTACTE**, et les quatre gels AST
de `test_perimetre_et_revert_imputable.py` restent verts. Verifie.

Les chemins rendus sont en outre RESOLVABLES depuis la racine utilisee : un nom nu
introuvable produisait un echec de pytest qui n'apprenait rien et detruisait tout.
"""
from __future__ import annotations

import pathlib

import pytest

from src.agents.sub_agent import CodeAgent

LUMENA = pathlib.Path(__file__).resolve().parent.parent.parent


class _FauxAgent:
    """Le strict necessaire : la methode ne lit que cette ancre.

    ⚠️ Les DEUX methodes sont reprises : un double en retard sur le contrat ne teste pas
    le contrat. Piege deja rencontre le 24/09 en elargissant `ensure_ready`.
    """

    def __init__(self, racine=None):
        self._task_workspace_root = racine

    trouver = CodeAgent._find_related_tests
    _dossier_de_tests_du_projet = CodeAgent._dossier_de_tests_du_projet


# -- 1. Le fait qui fonde le lot --------------------------------------------

def test_un_projet_TIERS_ne_recolte_aucun_test_de_lumena(tmp_path):
    """Le coeur du defaut : elle edite `config.py` dans SON projet, et le gate lui
    rendait des tests du depot Lumena qui parlent d'autre chose."""
    projet = tmp_path / "mon-projet"
    projet.mkdir()
    (projet / "config.py").write_text("PORT = 8085\n", encoding="utf-8")

    agent = _FauxAgent(projet)
    trouves = agent.trouver(str(projet / "config.py"))

    assert trouves == [], f"tests etrangers recoltes : {trouves}"


@pytest.mark.parametrize("nom", ["config", "app", "main", "utils", "server",
                                 "game", "world", "state", "auth", "core"])
def test_les_noms_banals_ne_declenchent_plus_rien(tmp_path, nom):
    """Les 10 noms les plus courants parmi les 20 mesures."""
    projet = tmp_path / "projet"
    projet.mkdir()
    cible = projet / f"{nom}.py"
    cible.write_text("x = 1\n", encoding="utf-8")

    assert _FauxAgent(projet).trouver(str(cible)) == [], nom


def test_un_projet_avec_SES_tests_les_trouve(tmp_path):
    """L'inverse doit marcher : un vrai projet teste garde son gate."""
    projet = tmp_path / "projet-teste"
    (projet / "tests").mkdir(parents=True)
    (projet / "calcul.py").write_text("def add(a, b): return a + b\n", encoding="utf-8")
    (projet / "tests" / "test_calcul.py").write_text(
        "from calcul import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )

    trouves = _FauxAgent(projet).trouver(str(projet / "calcul.py"))

    assert trouves, "le gate ne trouve plus les tests du projet lui-meme"


def test_les_chemins_rendus_sont_RESOLVABLES(tmp_path):
    """Un nom nu introuvable produisait un echec pytest qui n'apprenait rien - et
    detruisait le travail. Ce qui est rendu doit pouvoir etre execute."""
    projet = tmp_path / "projet"
    (projet / "tests").mkdir(parents=True)
    (projet / "moteur.py").write_text("V = 1\n", encoding="utf-8")
    (projet / "tests" / "test_moteur.py").write_text(
        "import moteur\n\ndef test_v():\n    assert moteur.V == 1\n", encoding="utf-8")

    trouves = _FauxAgent(projet).trouver(str(projet / "moteur.py"))

    assert trouves
    for chemin in trouves:
        candidat = pathlib.Path(chemin)
        if not candidat.is_absolute():
            candidat = projet / chemin
        assert candidat.exists(), f"chemin non resolvable : {chemin!r}"


# -- 2. Ce que le lot ne doit PAS casser ------------------------------------

def test_travailler_SUR_lumena_garde_son_gate():
    """Cas legitime que le code visait : le dev de Lumena elle-meme. L'ancre redevient
    le depot Lumena, et les tests de la racine sont bien retrouves."""
    agent = _FauxAgent(LUMENA)
    trouves = agent.trouver(str(LUMENA / "run_desktop.py"))

    assert trouves, "le gate ne marche plus quand elle developpe Lumena"
    assert any("run_desktop" in t or "test_" in t for t in trouves), trouves


def test_sans_ancre_le_dossier_du_fichier_sert_de_base(tmp_path):
    """Hors mission, `_task_workspace_root` peut etre None. On part alors du fichier
    modifie - jamais du depot Lumena, qui n'a aucun rapport avec lui."""
    projet = tmp_path / "isole"
    (projet / "tests").mkdir(parents=True)
    (projet / "widget.py").write_text("W = 2\n", encoding="utf-8")
    (projet / "tests" / "test_widget.py").write_text(
        "import widget\n\ndef test_w():\n    assert widget.W == 2\n", encoding="utf-8")

    trouves = _FauxAgent(None).trouver(str(projet / "widget.py"))

    assert trouves, "aucun test trouve alors que le projet en a"
    assert not any("image_gen" in t or "run_desktop" in t for t in trouves), trouves


def test_le_plafond_de_dix_est_conserve(tmp_path):
    """Caracterisation : le code rendait au plus 10 resultats."""
    projet = tmp_path / "gros"
    (projet / "tests").mkdir(parents=True)
    (projet / "noyau.py").write_text("N = 1\n", encoding="utf-8")
    for i in range(15):
        (projet / "tests" / f"test_noyau_{i}.py").write_text(
            "import noyau\n", encoding="utf-8")

    assert len(_FauxAgent(projet).trouver(str(projet / "noyau.py"))) <= 10


# -- 3. Le gel de l'imputabilite reste INTACT -------------------------------

def test_la_condition_d_imputabilite_n_est_PAS_touchee():
    """Ce lot corrige l'ANCRE, pas la condition. Les quatre gels AST de
    `test_perimetre_et_revert_imputable.py` doivent rester verts - c'est ce qui rend ce
    lot petit et sur."""
    import ast

    source = (LUMENA / "src" / "agents" / "sub_agent.py").read_text(encoding="utf-8")
    arbre = ast.parse(source)
    bloc = None
    for n in ast.walk(arbre):
        if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "_tests_imputables" for t in n.targets):
            bloc = n
            break

    assert bloc is not None, "la condition d'imputabilite a disparu"
    valeur = ast.dump(bloc.value)
    assert "BoolOp" in valeur and "Or" in valeur, valeur
    assert "_perim" in valeur, valeur
