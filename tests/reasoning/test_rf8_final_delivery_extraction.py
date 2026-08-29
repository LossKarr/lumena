"""RF-8 — sortie finale et truth-lock vers `final_delivery_runtime.py`.

Perimetre mesure sur le `react.py` du jour, APRES RF-8-FIX-1 :

    methodes final/truth_lock/finalize hors `_run_internal`   16
      - deja deleguees (RF-1, RF-5, RF-7)                      6
    = RF-8                                                    11   270 l.

--- L'ANGLE MORT QUI A CORRIGE LE PERIMETRE ---

Le detecteur de mutations utilise depuis RF-6a cherche `self.X = ...`
(`ast.Attribute` en `Store`). **Il ne voit pas trois formes d'effet** :

| forme | exemple | methode concernee |
|---|---|---|
| mutation de conteneur | `self._run_meta[k] = v` | `_note_truth_lock_outcome` |
| effet par appel | `self._mark_task_failed(...)` | `_empty_final_fallback` |
| effet par appel + TEMPS | `_mark_task_done(...)`, `sleep(...)` | `_stream_and_return_final` |

Les trois etaient classees « lectrices pures ». **La methode vedette de RF-8
porte le STREAMING** — et le §14 exige que « le streaming et la latence voix ne
regressent pas ».

Elles suivent donc le motif RF-6b : **la decision sort, l'effet reste.**

--- Les invariants propres a RF-8 (§14) ---

- toute sortie produite par une mission passe au point de production par le
  verrou pertinent — **c'est RF-8-FIX-1 qui l'a rendu vrai** ;
- un relais de resultat deja certifie n'est pas rejuge avec un ledger vide
  (`skip_mission_truth_lock`) ;
- aucune fuite THOUGHT n'est livree apres epuisement d'un retry ;
- un final incomplet reste honnete et actionnable ;
- le streaming et la latence voix ne regressent pas.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest


RACINE = pathlib.Path(__file__).resolve().parents[2]
MODULE = RACINE / "src" / "reasoning" / "final_delivery_runtime.py"
REACT = RACINE / "src" / "reasoning" / "react.py"

#: Deplacees en entier — lectrices, ou effet uniquement par dispatch d'instance.
DEPLACEES = [
    "_truth_lock_mission_message",
    "_truth_lock_web_flag",
    "_truth_lock_game_flag",
    "_truth_lock_interaction_flag",
]

#: NON extractibles — mesure, pas renoncement.
#:
#: `_final_repair_attempts`, `_premature_final_retries` et
#: `_ledger_final_guard_used` sont des paires **property + setter** : le getter
#: appelle `self._ensure_exec_state()` (qui CREE de l'etat) et le setter MUTE
#: `self.exec_state.repairs`. Mon detecteur les avait classees « pures » parce
#: qu'il ne regardait que le GETTER — deuxieme angle mort du meme outil, apres
#: celui des mutations de conteneur.
#:
#: `_looks_incomplete_final_answer` est deja une delegation d'UNE ligne vers
#: `looks_incomplete_final_answer` : il n'y a rien a extraire.
NON_EXTRACTIBLES = {
    "_final_repair_attempts": "property + setter sur exec_state.repairs",
    "_premature_final_retries": "property + setter sur exec_state.repairs",
    "_ledger_final_guard_used": "property + setter sur exec_state.repairs",
    "_looks_incomplete_final_answer": "deja une delegation d'une ligne",
}

#: Decoupees — la decision sort, l'effet reste (invariant 5 + §14 streaming).
DECOUPEES = {
    "_note_truth_lock_outcome": "self._run_meta[...] = ...",
    "_empty_final_fallback": "self._mark_task_failed(...)",
    "_stream_and_return_final": "self._mark_task_done(...) + streaming",
}


def _classe():
    arbre = ast.parse(REACT.read_text(encoding="utf-8"))
    return next(n for n in arbre.body
                if isinstance(n, ast.ClassDef) and n.name == "ReActLoop")


def _methodes():
    return {n.name: n for n in _classe().body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _effets(noeud) -> set:
    """Tous les effets, y compris ceux que `self.X = ...` ne montre pas."""
    out = set()
    for x in ast.walk(noeud):
        if (isinstance(x, ast.Attribute) and isinstance(x.ctx, ast.Store)
                and isinstance(x.value, ast.Name) and x.value.id == "self"):
            out.add("self." + x.attr)
        if isinstance(x, ast.Subscript) and isinstance(x.ctx, ast.Store):
            b = x.value
            if isinstance(b, ast.Attribute) and isinstance(b.value, ast.Name) \
                    and b.value.id == "self":
                out.add("self.%s[...]" % b.attr)
        if isinstance(x, ast.Call):
            nom = getattr(x.func, "attr", "")
            if nom in ("_mark_task_done", "_mark_task_failed",
                       "_mark_task_waiting_io", "sleep"):
                out.add(nom)
    return out


# ══════════════════════════════════════════════════════════════════════════
#  1. Le module cible
# ══════════════════════════════════════════════════════════════════════════


def test_le_module_existe():
    assert MODULE.is_file(), f"{MODULE} n'a pas ete cree"


def test_le_module_n_importe_JAMAIS_react():
    arbre = ast.parse(MODULE.read_text(encoding="utf-8"))
    fautifs = []
    for n in ast.walk(arbre):
        if isinstance(n, ast.ImportFrom) and (n.module or "").endswith("react"):
            fautifs.append(n.module)
        if isinstance(n, ast.Import):
            fautifs += [x.name for x in n.names if x.name.endswith("react")]
    assert not fautifs, f"le module importe react.py : {fautifs}"


def test_aucune_fonction_extraite_ne_garde_self():
    arbre = ast.parse(MODULE.read_text(encoding="utf-8"))
    fautives = [n.name for n in arbre.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.args.args and n.args.args[0].arg == "self"]
    assert not fautives, f"fonctions non transformees : {fautives}"


def test_le_module_ne_STREAME_PAS():
    """§14 — « le streaming et la latence voix ne regressent pas ».

    L'animation de frappe (`sleep` entre chunks) et son exemption voix restent
    dans `react.py`. Les deplacer changerait la latence percue sur un canal
    temps reel, ce qu'aucune matrice de valeurs ne verrait.
    """
    texte = MODULE.read_text(encoding="utf-8")
    arbre = ast.parse(texte)
    dort = [n.lineno for n in ast.walk(arbre)
            if isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "sleep"]
    assert not dort, f"le module dort (streaming deplace) aux lignes {dort}"
    assert "FINAL_TOKEN" not in texte, (
        "l'emission des chunks de streaming a ete deplacee hors de react.py"
    )


# ══════════════════════════════════════════════════════════════════════════
#  2. Les 8 methodes deplacees
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", DEPLACEES)
def test_la_methode_est_une_coquille(nom):
    m = _methodes()[nom]
    corps = [n for n in m.body if not (isinstance(n, ast.Expr)
                                       and isinstance(n.value, ast.Constant))]
    assert len(corps) <= 2, (
        f"{nom} garde {len(corps)} instructions apres la docstring"
    )


@pytest.mark.parametrize("nom", DEPLACEES)
def test_la_methode_existe_toujours(nom):
    from src.reasoning.react import ReActLoop
    assert hasattr(ReActLoop, nom), f"{nom} a disparu de ReActLoop"


@pytest.mark.parametrize("nom", DEPLACEES)
def test_la_methode_deplacee_n_a_AUCUN_effet_propre(nom):
    """Le garde qui a corrige le perimetre : `self.X = ...` ne suffit pas a
    prouver qu'une methode est pure."""
    effets = _effets(_methodes()[nom])
    # la coquille appelle le module ; aucun effet ne doit rester ni apparaitre
    assert not effets, f"{nom} porte des effets : {sorted(effets)}"


# ══════════════════════════════════════════════════════════════════════════
#  3. Les 3 methodes DECOUPEES — l'effet reste (invariant 5, §14)
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom,effet", sorted(DECOUPEES.items()))
def test_l_effet_reste_dans_ReActLoop(nom, effet):
    """LE garde du lot. Sortir un de ces effets, c'est soit dedoubler l'etat,
    soit deplacer la latence percue d'un canal temps reel."""
    effets = _effets(_methodes()[nom])
    assert effets, (
        f"{nom} n'a plus AUCUN effet — « {effet} » a quitte ReActLoop"
    )


def test_le_streaming_reste_dans_react():
    """Explicite : l'animation de frappe et l'exemption voix ne bougent pas."""
    src = inspect.getsource
    from src.reasoning.react import ReActLoop

    corps = src(ReActLoop._stream_and_return_final)
    assert "FINAL_TOKEN" in corps, "l'emission des chunks a quitte react.py"
    assert "_voice_delivery" in corps, "l'exemption voix a quitte react.py"
    assert "_mark_task_done" in corps, "le marquage de tache a quitte react.py"


def test_le_goulot_reste_le_goulot():
    """Non-regression RF-8-FIX-1 : `_stream_and_return_final` applique toujours
    le verrou, et garde son exemption."""
    from src.reasoning.react import ReActLoop

    corps = inspect.getsource(ReActLoop._stream_and_return_final)
    assert "_truth_lock_mission_message" in corps
    assert "skip_mission_truth_lock" in corps


def test_les_deux_voies_de_FIX1_restent_verrouillees():
    """Non-regression : RF-8 ne defait pas le lot precedent."""
    arbre = ast.parse(REACT.read_text(encoding="utf-8"))
    cls = next(n for n in arbre.body
               if isinstance(n, ast.ClassDef) and n.name == "ReActLoop")
    ri = next(n for n in cls.body if getattr(n, "name", "") == "_run_internal")
    verrouilles = [n.lineno for n in ast.walk(ri)
                   if isinstance(n, ast.Return) and n.value is not None
                   and "_truth_lock_mission_message" in ast.unparse(n.value)]
    assert len(verrouilles) >= 2, (
        f"les voies I3/Z28 ne sont plus verrouillees : {verrouilles}"
    )


# ══════════════════════════════════════════════════════════════════════════
#  4. Les entrees — lecons RF-4 et RF-7a
# ══════════════════════════════════════════════════════════════════════════


def test_une_entree_se_construit_sur_un_etat_INCOMPLET():
    """RF-4 : precalculer une valeur avait fait tomber 54 tests."""
    from src.reasoning.react import ReActLoop, _entree_final

    nu = object.__new__(ReActLoop)
    assert _entree_final(nu) is not None


def test_la_fabrique_est_une_fonction_de_MODULE():
    """RF-5b : en methode, la fabrique cassait 80 tests."""
    from src.reasoning import react

    sig = inspect.signature(react._entree_final)
    assert list(sig.parameters)[0] != "self"


def test_un_monkeypatch_d_INSTANCE_est_toujours_vu():
    """RF-7a : un appel direct de module perd le patch d'instance, en silence.

    `_truth_lock_mission_message` appelle `self._note_truth_lock_outcome(...)`,
    qui MUTE `_run_meta`. Cet appel doit redescendre sur l'instance, sinon la
    mutation se perd.
    """
    from src.reasoning.react import ReActLoop

    vu = {"appele": False}

    class _Orch:
        def get_task(self, _):
            return {"metadata": {"kind": "mission"}}

    class _Led:
        def last_test_outcome(self):
            return None

        def has_any_mutation(self):
            return True

        def has_published(self):
            return False

    o = object.__new__(ReActLoop)
    o.task_id = "t1"
    o.task_orchestrator = _Orch()
    o.execution_ledger = _Led()
    for nom, val in (
        ("_current_green_test_proof", False), ("_current_browser_proof", False),
        ("_tests_present_but_not_run", True), ("_truth_lock_web_flag", False),
        ("_mission_expects_file_deliverables", True),
        ("_mission_unpublished_writes", []), ("_server_started_proof", False),
        ("_browser_content_seen", False), ("_truth_lock_interaction_proven", False),
        ("_truth_lock_interaction_flag", False), ("_truth_lock_game_flag", False),
        ("_browser_runtime_failed_for_truth_lock", False),
    ):
        setattr(o, nom, (lambda v: (lambda: v))(val))

    def _piege(info):
        vu["appele"] = True

    o._note_truth_lock_outcome = _piege
    o._truth_lock_mission_message("Les 8 tests pytest sont VERTS.")

    assert vu["appele"], (
        "le monkeypatch d'instance sur `_note_truth_lock_outcome` n'est pas vu "
        "— l'extraction a court-circuite le dispatch d'instance, et la mutation "
        "de `_run_meta` se perd"
    )


# ══════════════════════════════════════════════════════════════════════════
#  5. Ce que le lot ne doit pas casser
# ══════════════════════════════════════════════════════════════════════════


def test_les_proprietes_restent_des_proprietes():
    """Invariant 13 — `_final_repair_attempts` et consorts sont lues comme des
    ATTRIBUTS partout : les degrader en methodes rendrait un objet toujours
    vrai."""
    from src.reasoning.react import ReActLoop

    for nom in ("_final_repair_attempts", "_premature_final_retries",
                "_ledger_final_guard_used"):
        assert isinstance(ReActLoop.__dict__[nom], property), (
            f"{nom} n'est plus une property"
        )


@pytest.mark.parametrize("nom,raison", sorted(NON_EXTRACTIBLES.items()))
def test_les_non_extractibles_ne_sont_PAS_deplacees(nom, raison):
    """Le garde de la MESURE. Ces quatre-la restent dans `react.py`, et la
    raison est ecrite : trois portent un setter qui MUTE `exec_state.repairs`,
    la quatrieme est deja une delegation d'une ligne.

    Si un lot futur les deplace, il aura contourne cette mesure — pas trouve
    une meilleure idee.
    """
    src = REACT.read_text(encoding="utf-8").splitlines(keepends=True)
    m = _methodes()[nom]
    corps = "".join(src[m.lineno - 1:m.end_lineno])
    assert "final_delivery_runtime" not in corps and "_fd_" not in corps, (
        f"{nom} a ete extraite alors qu'elle ne le peut pas : {raison}"
    )


def test_les_setters_des_proprietes_mutent_toujours():
    """La preuve de la mesure : ce sont bien des SETTERS qui ecrivent."""
    arbre = ast.parse(REACT.read_text(encoding="utf-8"))
    cls = next(n for n in arbre.body
               if isinstance(n, ast.ClassDef) and n.name == "ReActLoop")
    setters = [n for n in cls.body
               if isinstance(n, ast.FunctionDef)
               and any(getattr(d, "attr", "") == "setter" for d in n.decorator_list)
               and n.name in NON_EXTRACTIBLES]
    assert len(setters) >= 3, f"setters trouves : {[s.name for s in setters]}"
    for s in setters:
        ecrit = any(isinstance(x, (ast.Attribute, ast.Name)) and isinstance(x.ctx, ast.Store)
                    for x in ast.walk(s))
        assert ecrit, f"{s.name}.setter n'ecrit rien"


def test_run_internal_n_est_pas_touche():
    taille = (lambda m: m.end_lineno - m.lineno)(_methodes()["_run_internal"])
    assert taille > 5000, f"_run_internal fait {taille} lignes"


def test_les_lots_precedents_sont_intacts():
    """Porte de passage : un lot ne defait pas les precedents."""
    from src.reasoning.react import ReActLoop

    assert isinstance(ReActLoop.__dict__["_is_mission_run"], property)
    for nom in ("_mission_completion_evidence", "_pages_never_opened_reason"):
        assert hasattr(ReActLoop, nom)
