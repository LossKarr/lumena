"""RF-1 — preuves dediees de l'extraction des helpers navigateur.

Lot RF-1 du plan `plans/REFACTOR_REACT_DEDICATED_PLAN_2026-08-27.md` :
69 symboles (36 helpers purs + 33 constantes) ont quitte `react.py` pour
`src/reasoning/browser_reasoning.py`, en deplacement quasi verbatim.

Ce que ces tests protegent, et pourquoi :

  * **L'identite des reexports.** 28 des 36 helpers sont importes hors de
    `react.py` — `_browser_progress_delta` 21 fois, `_detect_browser_impasse`
    11 fois. Si `react.py` exposait une COPIE au lieu du meme objet, les
    monkeypatchs des tests existants patcheraient l'un et le code appellerait
    l'autre, en silence.
  * **L'absence de cycle.** Le nouveau module ne doit jamais remonter vers
    `react.py` (invariant 2 du plan).
  * **Les cinq constantes restees.** Trois d'entre elles LISENT des symboles
    partis (`_BROWSER_IMPASSE_TOKEN_SET` lit `_BROWSER_IMPASSE_SIGNALS`,
    `_LP_UNPROVABLE_CLOSED_TOOLS` lit `BROWSER_VISUAL_TOOLS`). C'est la raison
    pour laquelle l'import de reexport vit en tete de `react.py` et non a
    l'emplacement des anciennes definitions.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]
REACT = RACINE / "src" / "reasoning" / "react.py"
NOUVEAU = RACINE / "src" / "reasoning" / "browser_reasoning.py"

SYMBOLES_ATTENDUS = 69
FONCTIONS_ATTENDUES = 36
CONSTANTES_ATTENDUES = 33

# Contrats externes explicitement exiges par le plan (section RF-1).
CONTRATS = ("_browser_progress_delta", "_detect_browser_impasse", "_classify_browser_surface")

# Constantes restees dans react.py qui LISENT des symboles deplaces.
CONSTANTES_RESTEES = (
    "_BROWSER_IMPASSE_TOKEN_SET",
    "_INTERACTION_PROOF_INVALIDATORS",
    "BROWSER_SELF_VISUAL_ACTION_TOOLS",
    "_LP_UNPROVABLE_CLOSED_TOOLS",
    "_BROWSER_DRIFT_TOOLS",
)


def _symboles_du_module() -> list[str]:
    arbre = ast.parse(NOUVEAU.read_text(encoding="utf-8"))
    noms: list[str] = []
    for n in arbre.body:
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            noms.append(n.name)
        elif isinstance(n, ast.Assign):
            noms.extend(t.id for t in n.targets if isinstance(t, ast.Name))
        elif isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name):
            noms.append(n.target.id)
    return noms


# ══════════════════════════════════════════════════════════════════════════
#  1. Le module existe et contient exactement ce qui a ete deplace
# ══════════════════════════════════════════════════════════════════════════


def test_le_module_extrait_existe():
    assert NOUVEAU.exists(), "browser_reasoning.py absent"


def test_le_module_contient_les_69_symboles():
    arbre = ast.parse(NOUVEAU.read_text(encoding="utf-8"))
    fn = sum(1 for n in arbre.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)))
    const = sum(
        1 for n in arbre.body
        if isinstance(n, ast.Assign) or (isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name))
    )
    assert fn == FONCTIONS_ATTENDUES, f"{fn} fonctions au lieu de {FONCTIONS_ATTENDUES}"
    assert const == CONSTANTES_ATTENDUES, f"{const} constantes au lieu de {CONSTANTES_ATTENDUES}"
    assert fn + const == SYMBOLES_ATTENDUS


def test_aucun_helper_ne_prend_self():
    """Regle du lot : seules les fonctions PURES partent. Une fonction prenant
    `self` serait une methode de `ReActLoop` deguisee."""
    arbre = ast.parse(NOUVEAU.read_text(encoding="utf-8"))
    fautes = [
        n.name for n in arbre.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.args.args and n.args.args[0].arg == "self"
    ]
    assert fautes == [], f"fonctions prenant self dans le module extrait : {fautes}"


# ══════════════════════════════════════════════════════════════════════════
#  2. Identite des reexports — le coeur du lot
# ══════════════════════════════════════════════════════════════════════════


def test_les_69_symboles_sont_reexportes_par_react():
    from src.reasoning import browser_reasoning, react

    manquants = [n for n in _symboles_du_module() if not hasattr(react, n)]
    assert manquants == [], f"symboles non reexportes par react.py : {manquants}"
    assert len(_symboles_du_module()) == SYMBOLES_ATTENDUS


def test_chaque_reexport_est_le_meme_objet_pas_une_copie():
    """Une COPIE casserait les monkeypatchs en silence : le test patcherait un
    objet et le code appellerait l'autre."""
    from src.reasoning import browser_reasoning, react

    divergents = [
        n for n in _symboles_du_module()
        if getattr(react, n) is not getattr(browser_reasoning, n)
    ]
    assert divergents == [], f"reexport divergent (copie ?) pour : {divergents}"


@pytest.mark.parametrize("nom", CONTRATS)
def test_les_trois_contrats_externes_exiges_par_le_plan(nom: str):
    """Assertions explicites demandees par la section RF-1 du plan.
    Ces trois-la totalisent 37 imports hors de `react.py`."""
    from src.reasoning import browser_reasoning, react

    assert hasattr(react, nom), f"{nom} n'est plus accessible depuis react.py"
    assert hasattr(browser_reasoning, nom), f"{nom} absent du module extrait"
    assert getattr(react, nom) is getattr(browser_reasoning, nom)
    assert callable(getattr(react, nom))
    assert getattr(react, nom).__module__ == "src.reasoning.browser_reasoning"


# ══════════════════════════════════════════════════════════════════════════
#  3. Aucun cycle, aucune exception elargie
# ══════════════════════════════════════════════════════════════════════════


def test_le_module_extrait_n_importe_pas_react():
    source = NOUVEAU.read_text(encoding="utf-8")
    fautes = [
        l.strip() for l in source.splitlines()
        if re.search(r"(from\s+\S*reasoning\.react\s+import|from\s+\.react\s+import|import\s+\S*reasoning\.react)", l)
    ]
    assert fautes == [], f"browser_reasoning.py importe react.py : {fautes}"


def test_aucun_except_baseexception_dans_le_module_extrait():
    """Invariant 17 : `SystemExit` ne doit jamais etre avale par un module
    extrait."""
    source = NOUVEAU.read_text(encoding="utf-8")
    fautes = [f"l.{i}" for i, l in enumerate(source.splitlines(), 1) if "except BaseException" in l]
    assert fautes == [], f"except BaseException dans le module extrait : {fautes}"


def test_les_imports_locaux_restent_locaux():
    """Invariant 15. Deux helpers importent `urllib.parse` et
    `utils.local_preview` DANS leur corps. Les remonter au niveau module
    changerait le cout d'import et pourrait creer un cycle."""
    def nom_module(noeud) -> str:
        # `ast.Import` n'a pas d'attribut `module` : seul `ImportFrom` en a un.
        if isinstance(noeud, ast.ImportFrom):
            return noeud.module or ""
        return noeud.names[0].name

    arbre = ast.parse(NOUVEAU.read_text(encoding="utf-8"))
    au_module = {
        nom_module(n) for n in arbre.body if isinstance(n, (ast.Import, ast.ImportFrom))
    }
    assert not any("local_preview" in m for m in au_module), (
        "utils.local_preview a ete remonte au niveau module"
    )
    assert not any("urllib" in m for m in au_module), (
        "urllib.parse a ete remonte au niveau module"
    )
    locaux = [
        nom_module(n)
        for f in arbre.body
        if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
        for n in ast.walk(f) if isinstance(n, (ast.Import, ast.ImportFrom))
    ]
    assert len(locaux) >= 3, f"{len(locaux)} imports locaux au lieu des 3 mesures"


def test_les_imports_intra_paquet_sont_relatifs():
    """DEFAUT REEL DU PREMIER JET DE RF-1, corrige et garde ici.

    Le module a d'abord ete ecrit avec `from documents.document_intent import
    normalize_document_query` — un import ABSOLU — alors que `react.py` utilise
    `from ..documents.document_intent import (...)`, relatif.

    Sous pytest ca marche : `src/` est sur `sys.path`. Mais le pont MCP lance
    un vrai processus fils par `python -m src.llm.codex_mcp_bridge` depuis la
    RACINE du depot, ou `src/` n'y est pas. Le fils mourait alors sur
    `ModuleNotFoundError: No module named 'documents'`, ne produisait aucun
    JSON, et `test_real_stdio_child_process_lists_parent_scoped_tools` echouait
    sur un `JSONDecodeError` — a 1 400 lignes de distance de la cause.

    La cause de fond : l'extraction avait REECRIT l'en-tete d'imports au lieu
    de DEPLACER les imports d'origine. C'est exactement ce que l'invariant 3
    interdit.
    """
    arbre = ast.parse(NOUVEAU.read_text(encoding="utf-8"))
    paquets_src = {"documents", "runtime", "utils", "tools", "agents", "memory",
                   "context", "llm", "subagents", "reasoning", "config", "prompts"}
    fautes = [
        f"l.{n.lineno}: from {n.module} import ..."
        for n in arbre.body
        if isinstance(n, ast.ImportFrom)
        and n.level == 0
        and (n.module or "").split(".")[0] in paquets_src
    ]
    assert fautes == [], (
        "import ABSOLU vers un paquet frere — il ne resoudra pas dans un "
        f"processus lance depuis la racine du depot : {fautes}"
    )


def test_le_module_s_importe_sans_src_sur_le_chemin():
    """Reproduction directe de la panne : on importe le module depuis la
    RACINE du depot, exactement comme le fait le pont MCP."""
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-c", "import src.reasoning.browser_reasoning as m; print(len(dir(m)))"],
        cwd=str(RACINE), capture_output=True, text=True, timeout=180,
    )
    assert r.returncode == 0, (
        "le module ne s'importe pas depuis la racine du depot :\n"
        + (r.stderr or "")[-1200:]
    )


# ══════════════════════════════════════════════════════════════════════════
#  4. Les cinq constantes restees resolvent encore
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", CONSTANTES_RESTEES)
def test_les_constantes_restees_dans_react_resolvent(nom: str):
    """Trois de ces cinq LISENT un symbole parti. Si l'import de reexport
    n'etait pas en tete de `react.py`, l'evaluation echouerait des le
    chargement du module — donc ce test est aussi le garde de placement."""
    from src.reasoning import react

    assert hasattr(react, nom), f"{nom} a disparu de react.py"
    valeur = getattr(react, nom)
    assert valeur is not None
    assert len(valeur) > 0, f"{nom} est vide — dependance non resolue ?"


def test_les_constantes_restees_ne_sont_pas_dans_le_module_extrait():
    """Elles servent du code reste dans `react.py` : les deplacer aurait
    elargi le lot au-dela de sa fermeture de dependances."""
    dans_extrait = set(_symboles_du_module())
    fautes = [n for n in CONSTANTES_RESTEES if n in dans_extrait]
    assert fautes == [], f"constantes deplacees a tort : {fautes}"


# ══════════════════════════════════════════════════════════════════════════
#  5. Comportement inchange — verification par echantillon
# ══════════════════════════════════════════════════════════════════════════


def test_detect_browser_impasse_repond_toujours_un_triplet():
    from src.reasoning import react

    resultat = react._detect_browser_impasse("")
    assert isinstance(resultat, tuple) and len(resultat) == 3
    bloque, raison, dismiss = resultat
    assert isinstance(bloque, bool) and isinstance(raison, str) and isinstance(dismiss, bool)
    assert bloque is False, "une observation vide ne doit pas etre une impasse"


def test_classify_browser_surface_est_appelable_avec_ses_defauts():
    from src.reasoning import react

    resultat = react._classify_browser_surface("")
    assert resultat is not None


def test_browser_progress_delta_sans_signature_precedente():
    from src.reasoning import react

    resultat = react._browser_progress_delta(None, ())
    assert resultat is not None
