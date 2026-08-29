"""RF-0 — contrat structurel du refactor `react.py`.

Ce fichier ne teste pas un comportement : il fige la SURFACE que le refactor
progressif de `src/reasoning/react.py` ne doit pas casser. Il est le socle du
plan `plans/REFACTOR_REACT_DEDICATED_PLAN_2026-08-27.md` et il tourne AVANT
chaque lot RF-1 a RF-9.

Pourquoi il existe, et pas seulement les 19 000 tests deja en place :

  * `StatusCode.FAILURE` (lot Z37) etait un nom qui n'a JAMAIS existe. Trois
    sites l'appelaient, tous sur le chemin du refus. Resultat mesure : la porte
    de verification du CodeAgent est nee desarmee le 2026-04-24 et l'est restee
    QUATRE MOIS, sous 19 159 tests verts. Ce qui l'a trouvee, c'est la lecture
    d'un log — pas un test.
  * Un refactor deplace du code entre modules. C'est exactement l'operation qui
    fabrique ce genre de nom mort : un symbole qui n'existe plus a l'endroit ou
    on croit l'appeler, avale par un `except` trop large.

Les mesures ci-dessous viennent de l'audit RF-0 du 2026-08-27. Quand un chiffre
change legitimement, on met a jour la constante ET le plan — jamais l'inverse.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

RACINE = Path(__file__).resolve().parents[2]
REACT = RACINE / "src" / "reasoning" / "react.py"

# ── Mesures figees par l'audit RF-0 (2026-08-27) ────────────────────────────
#
# ⚠️ Ces nombres ne bougent QUE sur decision explicite. Les lots d'extraction
# (RF-1 a RF-7) les laissent intacts : une coquille remplace un corps, la forme
# et le compte restent identiques.
#
# **RF-8-FIX-1 (2026-08-28) : 107 -> 108 methodes, +1 membre.** Ce lot n'est pas
# une extraction mais un CORRECTIF : deux voies de sortie mission (I3 l.7008,
# Z28 l.7029) rendaient la parole du modele sans passer par le verrou de verite.
# La methode ajoutee, `_truth_lock_mission_message`, porte les ~16 arguments du
# verrou a UNE SEULE place — les recopier a chaque site les aurait laisses
# deriver d'une voie a l'autre. Le compte est donc mis a jour DELIBEREMENT.
MEMBRES_REACTLOOP = 196
FORMES_ATTENDUES = {
    "methode": 108,
    "staticmethod": 29,
    "property": 28,
    "setter": 26,
    "async": 5,
}
MOTIFS_SYSTEMEXIT = {
    "user_cancelled_react",
    "task_orchestrator_cancel",
    "mission_deadline_grace_expired",
}
RAISES_SYSTEMEXIT = 5          # 5 `raise` pour 3 motifs distincts
EXCEPT_SYSTEMEXIT = 4          # 4 handlers ; le 5e raise s'echappe volontairement
SYSTEMEXIT_QUI_S_ECHAPPE = "user_cancelled_react"
HANDLERS_MUETS = 25            # `except Exception: pass` dans _run_internal
FICHIERS_INTROSPECTION_MIN = 53

# Modules deja extraits de react.py. Aucun ne doit le reimporter : le cycle
# casserait l'import au demarrage et forcerait des imports locaux partout.
MODULES_EXTRAITS = (
    "agent_execution_state",
    "delegate_strategy",
    "final_guards",
    "hallucination_guard",
    "ledger_guard",
    "plan_evidence",
    "plan_progress",
    "prompt_builder",
    "test_proof",
    "browser_reasoning",   # RF-1 (2026-08-27)
)

# ── RF-1 — 69 symboles deplaces vers browser_reasoning.py ──────────────────
# 36 helpers purs + 33 constantes. `react.py` les reexporte : 27 sont lus plus
# bas dans le fichier, 28 sont importes par d'autres modules ou tests.
RF1_MODULE = "browser_reasoning"
RF1_SYMBOLES = 69
RF1_CONTRATS_EXTERNES = (
    "_browser_progress_delta",
    "_detect_browser_impasse",
    "_classify_browser_surface",
)


@pytest.fixture(scope="module")
def arbre() -> ast.Module:
    return ast.parse(REACT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def run_internal(arbre: ast.Module):
    for noeud in ast.walk(arbre):
        if isinstance(noeud, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if noeud.name == "_run_internal":
                return noeud
    pytest.fail("_run_internal introuvable dans react.py")


def _forme(methode) -> str:
    noms = []
    for deco in methode.decorator_list:
        if isinstance(deco, ast.Name):
            noms.append(deco.id)
        elif isinstance(deco, ast.Attribute):
            noms.append(deco.attr)
    for cle in ("property", "setter", "staticmethod", "classmethod"):
        if cle in noms:
            return cle
    return "async" if isinstance(methode, ast.AsyncFunctionDef) else "methode"


# ══════════════════════════════════════════════════════════════════════════
#  1. Aucun cycle — un module extrait ne remonte jamais vers react.py
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("module", MODULES_EXTRAITS)
def test_aucun_module_extrait_n_importe_react(module: str):
    """Invariant 2 du plan. Un seul cycle suffit a rendre l'import fragile et a
    forcer des imports locaux dans tout le reste du paquet."""
    chemin = RACINE / "src" / "reasoning" / f"{module}.py"
    assert chemin.exists(), f"module extrait disparu : {module}.py"
    source = chemin.read_text(encoding="utf-8")
    fautes = [
        ligne.strip()
        for ligne in source.splitlines()
        if re.search(r"^\s*(from\s+\S*reasoning\.react\s+import|import\s+\S*reasoning\.react)", ligne)
        or re.search(r"^\s*from\s+\.react\s+import", ligne)
    ]
    assert fautes == [], f"{module}.py importe react.py : {fautes}"


# ══════════════════════════════════════════════════════════════════════════
#  2. Le motif Z37 — tout attribut statique reference doit exister
# ══════════════════════════════════════════════════════════════════════════


def test_tout_attribut_de_classe_locale_reference_existe(arbre: ast.Module):
    """LE garde du lot Z37, porte sur react.py.

    `StatusCode.FAILURE` n'a jamais existe et a desarme une porte pendant
    quatre mois. Ici : pour chaque classe definie DANS react.py, tout acces
    `Classe.membre` ecrit dans le fichier doit designer un membre reel.
    """
    classes = {n.name: n for n in arbre.body if isinstance(n, ast.ClassDef)}
    membres = {}
    for nom, noeud in classes.items():
        connus = set()
        for x in noeud.body:
            if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef)):
                connus.add(x.name)
            elif isinstance(x, ast.Assign):
                connus.update(t.id for t in x.targets if isinstance(t, ast.Name))
            elif isinstance(x, ast.AnnAssign) and isinstance(x.target, ast.Name):
                connus.add(x.target.id)
        membres[nom] = connus

    inconnus = []
    for x in ast.walk(arbre):
        if (
            isinstance(x, ast.Attribute)
            and isinstance(x.value, ast.Name)
            and x.value.id in membres
            and not isinstance(x.ctx, ast.Store)
            and x.attr not in membres[x.value.id]
            and not x.attr.startswith("__")
        ):
            inconnus.append(f"{x.value.id}.{x.attr} (l.{x.lineno})")

    assert inconnus == [], (
        "attribut(s) inexistant(s) reference(s) dans react.py — "
        f"la ligne levera AttributeError : {inconnus}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  3. Protocole d'annulation — SystemExit, et rien d'autre
# ══════════════════════════════════════════════════════════════════════════


def test_les_trois_motifs_systemexit_sont_intacts(run_internal):
    """Invariant 17. L'annulation interne circule par `SystemExit` ; ces trois
    motifs sont le contrat historique."""
    trouves = {
        m.group(1)
        for noeud in ast.walk(run_internal)
        if isinstance(noeud, ast.Raise) and noeud.exc is not None
        for m in [re.search(r"SystemExit\(['\"]([a-z_]+)['\"]\)", ast.unparse(noeud.exc))]
        if m
    }
    assert trouves == MOTIFS_SYSTEMEXIT, f"motifs d'annulation modifies : {trouves}"


def test_le_nombre_de_raise_et_de_except_systemexit_est_fige(run_internal):
    """Desapparier un `raise` de son `except` casse l'annulation en silence :
    aucun test comportemental ne le dirait."""
    raises = [
        n for n in ast.walk(run_internal)
        if isinstance(n, ast.Raise) and n.exc is not None and "SystemExit" in ast.unparse(n.exc)
    ]
    handlers = [
        h for t in ast.walk(run_internal) if isinstance(t, ast.Try)
        for h in t.handlers
        if h.type is not None and "SystemExit" in ast.unparse(h.type)
    ]
    assert len(raises) == RAISES_SYSTEMEXIT, f"{len(raises)} raise SystemExit au lieu de {RAISES_SYSTEMEXIT}"
    assert len(handlers) == EXCEPT_SYSTEMEXIT, f"{len(handlers)} except SystemExit au lieu de {EXCEPT_SYSTEMEXIT}"


def test_l_annulation_utilisateur_s_echappe_toujours(run_internal):
    """Mesure RF-0 : sur les cinq `raise`, QUATRE sont rattrapes en interne et
    un seul sort — `user_cancelled_react`. C'est lui le contrat avec
    `subagents/worker.py`, qui l'attrape via `except BaseException`. S'il
    devenait rattrape en interne, l'annulation utilisateur cesserait de
    remonter, sans qu'aucun test ne change de couleur."""
    tries = [n for n in ast.walk(run_internal) if isinstance(n, ast.Try)]

    def dans_body(cible, essai):
        return any(cible is x or cible in list(ast.walk(x)) for x in essai.body)

    echappes = []
    for noeud in ast.walk(run_internal):
        if not (isinstance(noeud, ast.Raise) and noeud.exc is not None):
            continue
        texte = ast.unparse(noeud.exc)
        if "SystemExit" not in texte:
            continue
        englobants = sorted(
            (t for t in tries if dans_body(noeud, t)),
            key=lambda t: t.end_lineno - t.lineno,
        )
        rattrape = any(
            h.type is None
            or "SystemExit" in ast.unparse(h.type)
            or "BaseException" in ast.unparse(h.type)
            for t in englobants for h in t.handlers
        )
        if not rattrape:
            echappes.append(texte)

    assert len(echappes) == 1, f"{len(echappes)} SystemExit s'echappent : {echappes}"
    assert SYSTEMEXIT_QUI_S_ECHAPPE in echappes[0]


def test_aucun_except_baseexception_dans_react():
    """Invariant 17. `SystemExit` herite de `BaseException`, PAS de `Exception`.

    C'est precisement ce qui fait que l'annulation traverse aujourd'hui les 25
    handlers muets. Un seul `except BaseException` la tuerait a cet endroit.
    """
    source = REACT.read_text(encoding="utf-8")
    fautes = [
        f"l.{i}" for i, ligne in enumerate(source.splitlines(), 1)
        if "except BaseException" in ligne
    ]
    assert fautes == [], f"except BaseException introduit dans react.py : {fautes}"


def test_les_handlers_muets_restent_sur_exception_etroite(run_internal):
    """Les 25 `except ...: pass` de `_run_internal` sont tous sur `Exception`.
    Aucun n'est nu, aucun n'est sur `BaseException` — sinon l'annulation
    mourrait la. On fige le compte ET le type."""
    muets = []
    for essai in ast.walk(run_internal):
        if not isinstance(essai, ast.Try):
            continue
        for h in essai.handlers:
            if len(h.body) == 1 and isinstance(h.body[0], ast.Pass):
                muets.append((h.lineno, "BARE" if h.type is None else ast.unparse(h.type)))

    types = {t for _, t in muets}
    assert types <= {"Exception"}, f"handler muet sur type large : {sorted(types)}"
    assert len(muets) == HANDLERS_MUETS, (
        f"{len(muets)} handlers muets au lieu de {HANDLERS_MUETS} — "
        "un ajout doit etre justifie dans le plan RF-0"
    )


def test_le_registre_d_annulation_est_un_objet_unique():
    """Invariant 12. `_REACT_CANCEL_EVENTS` est indexe par `thread_id`. Copie ou
    recree dans un module extrait, il donnerait deux registres et l'annulation
    viserait le mauvais."""
    source = REACT.read_text(encoding="utf-8")
    creations = re.findall(r"^_REACT_CANCEL_EVENTS\s*(?::[^=]+)?=", source, re.M)
    assert len(creations) == 1, f"{len(creations)} creations du registre d'annulation"


# ══════════════════════════════════════════════════════════════════════════
#  4. Surface externe — 106 importeurs, 115 symboles, 96 prives
# ══════════════════════════════════════════════════════════════════════════


def test_toolregistry_reste_un_reexport_de_react(arbre: ast.Module):
    """Invariant 19. 33 fichiers importent `ToolRegistry` DEPUIS `react.py`.
    Ce n'est pas propre, c'est historique, et le refactor ReAct ne le migre
    pas : le retirer casserait 33 appelants pour une esthetique.

    On ne compare PAS par identite d'objet (`is`). Mesure RF-0 :
    `tests/reasoning/test_handler_crashproof.py::_fresh_registry` retire
    `src.reasoning.tool_registry` de `sys.modules` puis le reimporte, ce qui
    cree une SECONDE classe homonyme pour tout le reste de la session pytest.
    Une assertion `is` y testerait « aucun reload n'a eu lieu » — ce qui n'est
    pas l'invariant 19. Cette pollution est reelle et documentee dans la file
    de bugs separee ; elle ne se produit pas au runtime.

    Ce qui est verifie ici est exactement ce que l'invariant protege :
    le symbole existe, il vient bien de `tool_registry.py`, il n'est ni
    redefini ni copie dans `react.py`, et la ligne d'import est presente.
    """
    from src.reasoning import react as module_react

    assert hasattr(module_react, "ToolRegistry"), "le reexport a disparu de react.py"
    assert module_react.ToolRegistry.__module__ == "src.reasoning.tool_registry", (
        "ToolRegistry ne vient plus de tool_registry.py : "
        f"{module_react.ToolRegistry.__module__} — copie ou redefinition ?"
    )
    assert not any(
        isinstance(n, ast.ClassDef) and n.name == "ToolRegistry" for n in arbre.body
    ), "ToolRegistry est redefini dans react.py au lieu d'etre reexporte"

    imports = [
        n for n in arbre.body
        if isinstance(n, ast.ImportFrom)
        and (n.module or "").endswith("tool_registry")
        and any(a.name == "ToolRegistry" for a in n.names)
    ]
    assert len(imports) == 1, (
        f"{len(imports)} import(s) de ToolRegistry au niveau module de react.py — "
        "le reexport doit rester unique et explicite"
    )


def test_les_symboles_importes_par_le_depot_existent_tous():
    """Invariant 4. On collecte ce que le depot importe REELLEMENT depuis
    `react.py`, puis on verifie que chacun repond present. Un symbole deplace
    sans reexport casse ici, pas trois lots plus tard."""
    from src.reasoning import react as module_react

    attendus: set[str] = set()
    for dossier in ("src", "tests", "web"):
        for chemin in (RACINE / dossier).rglob("*.py"):
            if "__pycache__" in str(chemin) or chemin == REACT:
                continue
            try:
                arbre_local = ast.parse(chemin.read_text(encoding="utf-8", errors="replace"))
            except SyntaxError:
                continue
            for noeud in ast.walk(arbre_local):
                if isinstance(noeud, ast.ImportFrom) and (noeud.module or "").endswith("reasoning.react"):
                    attendus.update(alias.name for alias in noeud.names)

    manquants = sorted(n for n in attendus if not hasattr(module_react, n))
    assert manquants == [], f"symboles importes mais absents de react.py : {manquants}"
    assert len(attendus) >= 100, f"surface anormalement petite ({len(attendus)}) — scan casse ?"


# ══════════════════════════════════════════════════════════════════════════
#  5. Formes des descripteurs — property reste property
# ══════════════════════════════════════════════════════════════════════════


def test_les_formes_des_membres_de_reactloop_sont_figees(arbre: ast.Module):
    """Invariant 13. Une `property` transformee en methode casse tout appelant
    qui lit l'attribut ; un `staticmethod` devenu methode d'instance casse
    l'appel par la classe. Le refactor n'a aucune raison d'y toucher."""
    classe = next(
        (n for n in arbre.body if isinstance(n, ast.ClassDef) and n.name == "ReActLoop"),
        None,
    )
    assert classe is not None, "classe ReActLoop introuvable"

    formes: dict[str, int] = {}
    for membre in classe.body:
        if isinstance(membre, (ast.FunctionDef, ast.AsyncFunctionDef)):
            f = _forme(membre)
            formes[f] = formes.get(f, 0) + 1

    assert formes == FORMES_ATTENDUES, (
        f"repartition des formes modifiee : {formes} != {FORMES_ATTENDUES}"
    )
    assert sum(formes.values()) == MEMBRES_REACTLOOP


# ══════════════════════════════════════════════════════════════════════════
#  6. Les tests d'introspection source ne doivent pas etre affaiblis
# ══════════════════════════════════════════════════════════════════════════


def test_les_tests_d_introspection_source_ne_disparaissent_pas():
    """53 fichiers de tests lisent le TEXTE SOURCE de fonctions via
    `inspect.getsource`. Ils cassent mecaniquement des qu'une fonction change
    de fichier — c'est la contrainte la plus sous-estimee du chantier.

    Le plan l'autorise, mais seulement en ajoutant d'abord une preuve
    comportementale equivalente. Ce garde interdit la voie facile : les
    supprimer en silence.
    """
    dossier = RACINE / "tests"
    fichiers = [
        chemin for chemin in dossier.rglob("*.py")
        if "__pycache__" not in str(chemin)
        and re.search(r"inspect\.(getsource|getsourcelines)", chemin.read_text(encoding="utf-8", errors="replace"))
    ]
    assert len(fichiers) >= FICHIERS_INTROSPECTION_MIN, (
        f"{len(fichiers)} fichiers d'introspection au lieu de >= {FICHIERS_INTROSPECTION_MIN} : "
        "un test supprime doit etre remplace par une preuve comportementale, pas retire"
    )


# ══════════════════════════════════════════════════════════════════════════
#  7. Import sans effet de bord fatal
# ══════════════════════════════════════════════════════════════════════════


def test_react_s_importe_sans_effet_de_bord_fatal():
    """Un module extrait qui declenche un travail a l'import (client LLM,
    navigateur, fichier) rendrait le demarrage fragile et les tests lents."""
    import importlib

    module_react = importlib.import_module("src.reasoning.react")
    assert hasattr(module_react, "ReActLoop")
    assert hasattr(module_react, "_REACT_CANCEL_EVENTS")
    assert module_react._REACT_CANCEL_EVENTS == {} or isinstance(
        module_react._REACT_CANCEL_EVENTS, dict
    )


def test_aucun_nouvel_etat_global_mutable(arbre: ast.Module):
    """Invariant 12 elargi. Un dict/list/set cree au niveau module est un etat
    partage entre tous les runs. Il y en a aujourd'hui un nombre precis ; toute
    hausse doit etre justifiee, pas subie."""
    mutables = []
    for noeud in arbre.body:
        cibles, valeur = [], None
        if isinstance(noeud, ast.Assign):
            cibles = [t.id for t in noeud.targets if isinstance(t, ast.Name)]
            valeur = noeud.value
        elif isinstance(noeud, ast.AnnAssign) and isinstance(noeud.target, ast.Name):
            cibles, valeur = [noeud.target.id], noeud.value
        if valeur is not None and isinstance(valeur, (ast.Dict, ast.List, ast.Set)):
            mutables.extend(cibles)

    assert len(mutables) <= 12, (
        f"{len(mutables)} etats globaux mutables au niveau module : {sorted(mutables)}"
    )
