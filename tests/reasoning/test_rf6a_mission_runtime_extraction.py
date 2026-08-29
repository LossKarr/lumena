"""RF-6a — extraction des lectrices pures du runtime missions.

Perimetre mesure par AST sur le `react.py` du jour (10 357 lignes) :

    methodes mission/worker/lead/publish hors `_run_internal`   21   710 l.
      - coquilles DEJA extraites (RF-5a, RF-5d2, RF-7a)          3    17 l.
      - mutatrices de `self` (invariant 5) -> RF-6b              3   125 l.
    = RF-6a, lectrices pures                                    15   568 l.

Contrat d'etat : **4 attributs seulement** — `task_id`, `task_orchestrator`,
`execution_ledger`, `_task_plan`. Bien plus etroit que les 25 champs de RF-7a.

--- Les deux lecons deja payees, appliquees ici ---

**RF-4** : les entrees sont PARESSEUSES. Les tests du depot construisent
`object.__new__(ReActLoop)`, ou ces attributs sont ABSENTS ; precalculer une
valeur avait fait tomber 54 tests par `AttributeError` avant tout garde.

**RF-7a** : une forme `self.X(...)` **doit redescendre sur l'instance**. Le
rebindage en appel direct de module fait perdre les monkeypatchs d'instance,
**en silence** — 17 tests etaient tombes. Les 7 appels sortants de
`_mission_completion_evidence` passent donc par des `Callable`.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest


RACINE = pathlib.Path(__file__).resolve().parents[2]
MODULE = RACINE / "src" / "reasoning" / "mission_runtime.py"
REACT = RACINE / "src" / "reasoning" / "react.py"


#: Les 15 methodes du lot, dans l'ordre du fichier.
PERIMETRE = [
    "_is_mission_run",
    "_mission_workspace_meta",
    "_mission_unpublished_writes",
    "_mission_routing_objective",
    "_mission_tests_present_for_gate",
    "_mission_web_present_for_gate",
    "_mission_js_present_for_gate",
    "_worker_codeagent_first_gate",
    "_mission_completion_evidence",
    "_mission_allowed_files_meta",
    "_mission_worker_delivered",
    "_mission_lead_delivered",
    "_mission_expects_file_deliverables",
    "_is_worker_run",
    "_is_delegated_worker",
]

#: Coquilles deja extraites par d'AUTRES lots — RF-6a n'y touche pas.
COQUILLES_AUTRES_LOTS = {
    "_force_mission_proactive_document_tools": "RF-5d2",
    "_merge_mission_document_evidence": "RF-5a",
    "_mission_browser_verify_pending": "RF-7a",
}

#: Mutatrices de `self` — invariant 5, elles restent dans `ReActLoop` (RF-6b).
MUTATRICES_RESTEES = {
    "_nudge_unpublished_writes",
    "_mission_overwrite_gate",
    "_chat_mission_intent_gate",
}


def _arbre(p: pathlib.Path) -> ast.Module:
    return ast.parse(p.read_text(encoding="utf-8"))


def _classe_react() -> ast.ClassDef:
    return next(n for n in _arbre(REACT).body
                if isinstance(n, ast.ClassDef) and n.name == "ReActLoop")


def _methodes_react() -> dict:
    return {n.name: n for n in _classe_react().body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


# ══════════════════════════════════════════════════════════════════════════
#  1. Le module cible existe et reste une feuille
# ══════════════════════════════════════════════════════════════════════════


def test_le_module_existe():
    assert MODULE.is_file(), f"{MODULE} n'a pas ete cree"


def test_le_module_n_importe_JAMAIS_react():
    """Invariant 2. Un cycle ici casserait l'import du paquet entier."""
    a = _arbre(MODULE)
    fautifs = []
    for n in ast.walk(a):
        if isinstance(n, ast.ImportFrom) and (n.module or "").endswith("react"):
            fautifs.append(n.module)
        if isinstance(n, ast.Import):
            fautifs += [x.name for x in n.names if x.name.endswith("react")]
    assert not fautifs, f"le module importe react.py : {fautifs}"


def test_aucune_fonction_extraite_ne_garde_un_parametre_self():
    """Garde ajoute en RF-5d2 apres trois extractions silencieusement sautees :
    une fonction qui garde `self` n'a pas ete transformee, et le test de
    comportement passe quand meme parce que la coquille appelle l'original."""
    a = _arbre(MODULE)
    fautives = [
        n.name for n in a.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.args.args and n.args.args[0].arg == "self"
    ]
    assert not fautives, f"fonctions non transformees : {fautives}"


# ══════════════════════════════════════════════════════════════════════════
#  2. Les 15 methodes sont devenues des coquilles
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", PERIMETRE)
def test_la_methode_est_une_coquille(nom):
    """Le corps doit tenir en une docstring + un `return`/`if` court. Une
    coquille qui garderait sa logique signifierait une extraction a moitie
    faite — et deux sources de verite."""
    m = _methodes_react()[nom]
    corps = [n for n in m.body if not (isinstance(n, ast.Expr)
                                       and isinstance(n.value, ast.Constant))]
    assert len(corps) <= 2, (
        f"{nom} garde {len(corps)} instructions apres la docstring — "
        f"l'extraction est incomplete"
    )


@pytest.mark.parametrize("nom", PERIMETRE)
def test_la_methode_existe_toujours_sur_la_classe(nom):
    """Invariant 4 : les symboles historiques restent disponibles."""
    from src.reasoning.react import ReActLoop
    assert hasattr(ReActLoop, nom), f"{nom} a disparu de ReActLoop"


# ══════════════════════════════════════════════════════════════════════════
#  3. Les formes de descripteur (invariant 13)
# ══════════════════════════════════════════════════════════════════════════


def test_is_mission_run_reste_une_property():
    """`_is_mission_run` est lue comme un ATTRIBUT dans tout le depot
    (`self._is_mission_run`, sans parentheses). La degrader en methode ferait
    rendre un objet-methode — toujours vrai — a chaque garde qui la lit."""
    from src.reasoning.react import ReActLoop
    assert isinstance(ReActLoop.__dict__["_is_mission_run"], property), (
        "la property est devenue une methode ordinaire"
    )


def test_le_staticmethod_d_un_autre_lot_n_est_pas_degrade():
    """`_merge_mission_document_evidence` appartient a RF-5a et a 283 sites
    d'appel en forme `ReActLoop._merge_...(...)`."""
    from src.reasoning.react import ReActLoop
    assert isinstance(
        ReActLoop.__dict__["_merge_mission_document_evidence"], staticmethod
    )


@pytest.mark.parametrize("nom", PERIMETRE)
def test_les_signatures_sont_identiques(nom):
    """Une signature qui bouge touche tous les sites d'appel — ce ne serait
    plus un deplacement quasi verbatim (invariant 3)."""
    from src.reasoning.react import ReActLoop
    attendu = {
        "_is_mission_run": None,  # property
        "_worker_codeagent_first_gate": ["self", "tool_name", "tool_args"],
    }
    if attendu.get(nom) is None and nom in attendu:
        return
    # `getattr(ReActLoop, nom)` rend la fonction NON LIEE : `self` en fait
    # partie. Le comparer a [] etait une erreur de ce test, pas du code.
    sig = inspect.signature(getattr(ReActLoop, nom))
    attendus = attendu.get(nom, ["self"])
    assert list(sig.parameters) == attendus, (
        f"{nom} : signature {list(sig.parameters)} au lieu de {attendus}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  4. Ce que RF-6a NE touche PAS
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom,lot", sorted(COQUILLES_AUTRES_LOTS.items()))
def test_les_coquilles_des_autres_lots_gardent_leur_proprietaire(nom, lot):
    """Trois methodes du perimetre mission sont DEJA extraites ailleurs. Les
    re-extraire changerait leur proprietaire et casserait les invariants 4
    et 19."""
    src = REACT.read_text(encoding="utf-8")
    m = _methodes_react()[nom]
    corps = "\n".join(src.splitlines()[m.lineno - 1:m.end_lineno])
    assert "_rt__" in corps, (
        f"{nom} appartient a {lot} et ne delegue plus a son module d'origine"
    )
    assert "mission_runtime" not in corps, (
        f"{nom} a ete reextraite par RF-6a alors qu'elle appartient a {lot}"
    )


@pytest.mark.parametrize("nom", sorted(MUTATRICES_RESTEES))
def test_les_mutatrices_restent_dans_ReActLoop(nom):
    """Invariant 5 : les mutations de `self` ne sortent pas tant qu'un contrat
    d'etat explicite n'est pas prouve. Ces trois-la sont le perimetre de RF-6b."""
    m = _methodes_react()[nom]
    mute = any(
        isinstance(x, ast.Attribute) and isinstance(x.ctx, ast.Store)
        and isinstance(x.value, ast.Name) and x.value.id == "self"
        for x in ast.walk(m)
    )
    assert mute, f"{nom} ne mute plus self — elle a ete deplacee par erreur"


def test_run_internal_n_est_pas_touche():
    """RF-9 n'est pas ouvert. `_run_internal` doit garder sa taille."""
    m = _methodes_react()["_run_internal"]
    taille = m.end_lineno - m.lineno
    assert taille > 5000, (
        f"_run_internal fait {taille} lignes — RF-6a n'avait pas a y toucher"
    )


# ══════════════════════════════════════════════════════════════════════════
#  5. Le dispatch d'instance — LA lecon de RF-7a
# ══════════════════════════════════════════════════════════════════════════


#: Les 7 appels sortants de `_mission_completion_evidence`.
SORTANTS = [
    "_current_browser_proof",
    "_current_green_test_proof",
    "_orchestrator_enabled",
    "_truth_lock_game_flag",
    "_truth_lock_interaction_flag",
    "_truth_lock_interaction_proven",
    "_truth_lock_web_flag",
]


@pytest.mark.parametrize("nom", SORTANTS)
def test_un_monkeypatch_d_INSTANCE_est_toujours_vu(nom):
    """LE test structurel du lot.

    Les tests du depot patchent l'INSTANCE :

        loop._truth_lock_web_flag = lambda: True

    Si l'extraction rebinde `self.X()` en appel direct du module, le patch
    n'est plus vu — **en silence**. 17 tests etaient tombes ainsi en RF-7a.
    """
    from src.reasoning.react import ReActLoop

    temoin = {"appele": False}

    class _Orch:
        def get_task(self, _):
            return {"metadata": {"kind": "mission", "mission_workspace": "m/x"}}

    class _Led:
        size = 0

        def last_test_outcome(self):
            return {"green": False}

        def has_source_mutation(self):
            return False

        def has_published(self):
            return False

        def written_basenames(self):
            return set()

        def successful_mutations(self):
            return []

        def writes_after_last_publish(self):
            return []

    # Chaque sortant n'est atteint que sous SA precondition : sans elle, la
    # methode sort avant et le test « prouverait » un dispatch jamais exerce.
    # C'est la meme discipline que la matrice — il ne suffit pas d'appeler.
    PRECONDITIONS = {
        # appele en tete, aucune precondition
        "_orchestrator_enabled": {},
        # branche lead : exige que le truth-lock reclame le web
        "_truth_lock_web_flag": {},
        "_truth_lock_interaction_flag": {"_truth_lock_web_flag": True},
        "_truth_lock_game_flag": {"_truth_lock_web_flag": True},
        "_truth_lock_interaction_proven": {
            "_truth_lock_web_flag": True, "_truth_lock_interaction_flag": True},
        "_current_browser_proof": {"_truth_lock_web_flag": True},
        # exige une mutation de source au ledger
        "_current_green_test_proof": {"__mutation_source__": True},
    }
    pre = PRECONDITIONS[nom]

    o = object.__new__(ReActLoop)
    o.task_id = "t1"
    o.task_orchestrator = _Orch()
    led = _Led()
    if pre.get("__mutation_source__"):
        led.has_source_mutation = lambda: True
    o.execution_ledger = led
    o._task_plan = []
    for autre in SORTANTS:
        # `_orchestrator_enabled` doit valoir True, sinon la methode sort en
        # tete et AUCUN des sept n'est jamais atteint.
        defaut = True if autre == "_orchestrator_enabled" else pre.get(autre, False)
        setattr(o, autre, (lambda v: (lambda: v))(defaut))

    def _piege():
        temoin["appele"] = True
        return False

    setattr(o, nom, _piege)
    o._mission_completion_evidence()

    assert temoin["appele"], (
        f"le monkeypatch d'instance sur `{nom}` n'est PAS vu : l'extraction a "
        f"rebinde l'appel en direct et court-circuite le dispatch d'instance"
    )


# ══════════════════════════════════════════════════════════════════════════
#  6. Les entrees sont PARESSEUSES — la lecon de RF-4
# ══════════════════════════════════════════════════════════════════════════


def test_une_entree_se_construit_sur_un_etat_INCOMPLET():
    """Les tests du depot font `object.__new__(ReActLoop)` : les 4 attributs
    du contrat sont ABSENTS. Precalculer une valeur dans la fabrique avait
    fait tomber 54 tests en RF-4, par `AttributeError` avant tout garde."""
    from src.reasoning.react import ReActLoop, _entree_mission

    nu = object.__new__(ReActLoop)
    entree = _entree_mission(nu)  # ne doit PAS lever
    assert entree is not None


def test_la_fabrique_est_une_fonction_de_MODULE_pas_une_methode():
    """Lecon de RF-5b : en methode, la fabrique cassait 80 tests qui appellent
    `ReActLoop._mission_x(state)` avec un etat arbitraire."""
    from src.reasoning import react

    assert callable(getattr(react, "_entree_mission", None))
    sig = inspect.signature(react._entree_mission)
    assert list(sig.parameters)[0] != "self"


# ══════════════════════════════════════════════════════════════════════════
#  7. Comportement — les gardes restent fail-closed (invariant 7)
# ══════════════════════════════════════════════════════════════════════════


def _etat_minimal(**kw):
    from src.reasoning.react import ReActLoop

    class _Orch:
        def __init__(self, meta):
            self._m = meta

        def get_task(self, _):
            return {"metadata": self._m}

    class _Led:
        size = 0

        def last_test_outcome(self):
            return None

        def has_source_mutation(self):
            return False

        def has_published(self):
            return False

        def written_basenames(self):
            return set()

        def successful_mutations(self):
            return []

        def writes_after_last_publish(self):
            return []

    o = object.__new__(ReActLoop)
    o.task_id = kw.get("task_id", "t1")
    o.task_orchestrator = _Orch(kw.get("meta", {"kind": "mission"}))
    o.execution_ledger = _Led()
    o._task_plan = []
    for n in SORTANTS:
        setattr(o, n, lambda: False)
    return o


def test_hors_mission_les_preuves_restent_vides():
    """Le chat n'a pas de task_id de mission : aucune preuve ne doit sortir."""
    o = _etat_minimal(meta={"kind": "chat"})
    assert o._is_mission_run is False
    faits = o._mission_completion_evidence()
    assert faits["complete"] is False
    assert faits["scope"] == ""


def test_une_mission_sans_preuve_n_est_PAS_complete():
    """Invariant 7 : fail-closed. Sans livrable ni preuve, `complete` reste
    False — c'est tout le truth-lock qui en depend."""
    o = _etat_minimal(meta={"kind": "mission", "mission_workspace": "m/absent"})
    assert o._mission_completion_evidence()["complete"] is False


def test_un_etat_casse_ne_devient_pas_une_autorisation():
    """Invariant 6 : une exception ne se transforme jamais en succes."""
    o = _etat_minimal(meta={"kind": "mission"})

    class _OrchCasse:
        def get_task(self, _):
            raise RuntimeError("boom")

    o.task_orchestrator = _OrchCasse()
    faits = o._mission_completion_evidence()
    assert faits["complete"] is False, (
        "un orchestrateur en panne rend la mission complete"
    )
    assert o._is_mission_run is False
    assert o._mission_workspace_meta() == ""
    assert o._mission_allowed_files_meta() == []
