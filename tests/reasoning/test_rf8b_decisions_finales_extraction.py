"""RF-8b — la decision sort, l'effet reste (fin de RF-8).

RF-8a a deplace les 4 methodes sans effet. Restent celles que l'invariant 5 et
le §14 retiennent :

| methode | ce qui la retient |
|---|---|
| `_note_truth_lock_outcome` | ecrit `self._run_meta[...]` |
| `_empty_final_fallback` | appelle `self._mark_task_failed(...)` |
| `_stream_and_return_final` | `_mark_task_done(...)` **et le STREAMING** |

--- `_stream_and_return_final` NE DONNE RIEN, et c'est mesure ---

    code hors docstring : 39 lignes
    effets et boucles   : 6  (sleep, 2 boucles de chunks, 2 logs, mark_task_done)

Ses 39 lignes de code SONT le streaming. Apres RF-8a et FIX-1, tout ce qui
pouvait etre delegue l'est deja : le verrou (`_truth_lock_mission_message`) et
le nettoyage DSML (`strip_dsml_markup`). **Il n'y a plus de decision a en
sortir**, et le §14 interdit d'y toucher : « le streaming et la latence voix ne
regressent pas ».

Le declarer « fait » sans rien deplacer serait malhonnete ; le decouper de force
deplacerait la latence percue d'un canal temps reel. Un test verrouille la
mesure.

--- Ce que RF-8b extrait vraiment ---

Deux decisions, ~50 lignes :

  `rf8b_verdict_a_memoriser(info, deja_vu)` -> dict des cles a poser
  `rf8b_decision_final_vide(etat)`          -> (message, marquer_echec, ecrits, publie)

L'ORDRE observable est preserve (invariant 16) : la fonction ne journalise pas
et ne construit rien qui date. `react.py` garde mutation, log et retour dans
l'ordre d'origine.
"""

from __future__ import annotations

import ast
import inspect
import pathlib

import pytest


RACINE = pathlib.Path(__file__).resolve().parents[2]
MODULE = RACINE / "src" / "reasoning" / "final_delivery_runtime.py"
REACT = RACINE / "src" / "reasoning" / "react.py"

DECOUPEES = {
    "_note_truth_lock_outcome": ("self._run_meta[...]",),
    "_empty_final_fallback": ("_mark_task_failed",),
}


def _methodes():
    arbre = ast.parse(REACT.read_text(encoding="utf-8"))
    cls = next(n for n in arbre.body
               if isinstance(n, ast.ClassDef) and n.name == "ReActLoop")
    return {n.name: n for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _effets(noeud) -> set:
    out = set()
    for x in ast.walk(noeud):
        if isinstance(x, ast.Subscript) and isinstance(x.ctx, ast.Store):
            b = x.value
            if isinstance(b, ast.Attribute) and isinstance(b.value, ast.Name) \
                    and b.value.id == "self":
                out.add("self.%s[...]" % b.attr)
        if isinstance(x, ast.Call):
            nom = getattr(x.func, "attr", "")
            if nom in ("_mark_task_done", "_mark_task_failed", "sleep"):
                out.add(nom)
    return out


# ══════════════════════════════════════════════════════════════════════════
#  1. La decision est sortie
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", sorted(DECOUPEES))
def test_la_decision_est_deleguee(nom):
    src = REACT.read_text(encoding="utf-8").splitlines(keepends=True)
    m = _methodes()[nom]
    corps = "".join(src[m.lineno - 1:m.end_lineno])
    assert "_fd_" in corps, f"{nom} ne delegue pas au module"


@pytest.mark.parametrize("nom", sorted(DECOUPEES))
def test_la_coquille_ne_garde_que_l_effet(nom):
    m = _methodes()[nom]
    corps = [n for n in m.body if not (isinstance(n, ast.Expr)
                                       and isinstance(n.value, ast.Constant))]
    assert len(corps) <= 8, (
        f"{nom} garde {len(corps)} instructions — la decision n'est pas sortie"
    )


# ══════════════════════════════════════════════════════════════════════════
#  2. L'EFFET reste — invariant 5
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom,effets_attendus", sorted(DECOUPEES.items()))
def test_l_effet_reste_dans_ReActLoop(nom, effets_attendus):
    """LE garde du lot. Sortir la mutation dedoublerait l'etat."""
    trouves = _effets(_methodes()[nom])
    for e in effets_attendus:
        assert any(e.split("[")[0] in t for t in trouves), (
            f"{nom} : l'effet « {e} » a quitte ReActLoop. Trouves : {sorted(trouves)}"
        )


def test_le_module_ne_MUTE_rien():
    arbre = ast.parse(MODULE.read_text(encoding="utf-8"))
    fautives = []
    for n in arbre.body:
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for x in ast.walk(n):
            if (isinstance(x, ast.Attribute) and isinstance(x.ctx, ast.Store)
                    and isinstance(x.value, ast.Name) and x.value.id == "etat"):
                fautives.append((n.name, x.attr))
            if isinstance(x, ast.Call) and getattr(x.func, "attr", "") in (
                    "_mark_task_done", "_mark_task_failed"):
                fautives.append((n.name, x.func.attr))
    assert not fautives, f"le module porte des effets : {fautives}"


def test_le_module_ne_JOURNALISE_pas_les_deux_decisions():
    """Invariant 16 — l'ordre observable inclut les logs. Dans l'original, le
    log de `_empty_final_fallback` suit `_mark_task_failed`. Si la fonction
    extraite journalisait, l'ordre s'inverserait."""
    arbre = ast.parse(MODULE.read_text(encoding="utf-8"))
    for n in arbre.body:
        if not isinstance(n, ast.FunctionDef) or not n.name.startswith("rf8b_"):
            continue
        logs = [x.lineno for x in ast.walk(n)
                if isinstance(x, ast.Call)
                and getattr(getattr(x, "func", None), "value", None) is not None
                and getattr(x.func.value, "id", "") == "logger"]
        assert not logs, f"{n.name} journalise (lignes {logs}) : l'ordre change"


# ══════════════════════════════════════════════════════════════════════════
#  3. `_stream_and_return_final` — la MESURE, pas un renoncement
# ══════════════════════════════════════════════════════════════════════════


def test_le_streaming_n_a_rien_a_extraire():
    """Ses lignes de code SONT le streaming.

    Si un jour cette methode maigrit sous le seuil, c'est que quelqu'un aura
    deplace le streaming — et le §14 l'interdit. Le test le dira.
    """
    m = _methodes()["_stream_and_return_final"]
    effets = _effets(m)
    boucles = sum(1 for x in ast.walk(m) if isinstance(x, (ast.For, ast.While)))
    assert "sleep" in effets, "l'animation de frappe a quitte react.py"
    assert "_mark_task_done" in effets, "le marquage de tache a quitte react.py"
    assert boucles >= 2, f"les boucles de chunks ont disparu ({boucles})"


def test_le_streaming_delegue_DEJA_ce_qui_pouvait_l_etre():
    """Non-regression RF-8a et FIX-1 : le verrou et le nettoyage DSML sont
    deja sortis. C'est pour cela qu'il ne reste que l'effet."""
    from src.reasoning.react import ReActLoop

    corps = inspect.getsource(ReActLoop._stream_and_return_final)
    assert "_truth_lock_mission_message" in corps
    assert "strip_dsml_markup" in corps


# ══════════════════════════════════════════════════════════════════════════
#  4. Comportement — les deux decisions restent justes
# ══════════════════════════════════════════════════════════════════════════


def _etat(**kw):
    from src.reasoning.react import ReActLoop

    class _Orch:
        def get_task(self, _):
            return {"metadata": {"kind": kw.get("kind", "mission")}}

    class _Led:
        def written_basenames(self):
            return set(kw.get("basenames", set()))

        def has_published(self):
            return bool(kw.get("published", False))

        def has_any_mutation(self):
            return bool(kw.get("mutation", False))

    o = object.__new__(ReActLoop)
    o.task_id = "t1"
    o.task_orchestrator = _Orch()
    o.execution_ledger = _Led()
    o._echecs = []
    o._mark_task_failed = o._echecs.append
    o._current_green_test_proof = lambda: False
    o._tests_present_but_not_run = lambda: False
    return o


def test_hors_mission_la_formule_historique_est_intacte():
    o = _etat(kind="chat")
    assert o._empty_final_fallback() == "Je n'ai pas trouvé de réponse pertinente."
    assert o._echecs == []


def test_mission_sans_preuve_marque_l_echec():
    """Invariant 7 — fail-closed : un FINAL vide sans trace n'est pas une
    livraison."""
    o = _etat()
    message = o._empty_final_fallback()
    assert o._echecs == ["empty_final_without_evidence"], (
        "l'echec n'est plus marque : une politesse cloturerait la mission `done`"
    )
    assert "Rien n'a été livré" in message


def test_mission_avec_preuve_ne_marque_PAS_d_echec():
    o = _etat(basenames={"rapport.md"})
    message = o._empty_final_fallback()
    assert o._echecs == [], "un travail prouve est marque en echec"
    assert "rapport.md" in message


def test_le_verdict_reste_cumulatif():
    """Non-regression FIX-2."""
    from src.reasoning.react import ReActLoop

    o = object.__new__(ReActLoop)
    o.task_id = "t1"
    o._note_truth_lock_outcome({"overclaim": True})
    o._note_truth_lock_outcome({"overclaim": False})
    assert o._run_meta.get("mission_truth_lock_overclaim") is True


def test_un_verdict_non_dict_est_ignore():
    from src.reasoning.react import ReActLoop

    o = object.__new__(ReActLoop)
    o.task_id = "t1"
    o._note_truth_lock_outcome("pas un dict")
    assert o._run_meta.get("mission_truth_lock_overclaim") is False


# ══════════════════════════════════════════════════════════════════════════
#  5. Ce que le lot ne doit pas casser
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("nom", sorted(DECOUPEES))
def test_les_signatures_sont_identiques(nom):
    from src.reasoning.react import ReActLoop

    attendu = {
        "_note_truth_lock_outcome": ["self", "info"],
        "_empty_final_fallback": ["self"],
    }
    sig = inspect.signature(getattr(ReActLoop, nom))
    assert list(sig.parameters) == attendu[nom]


def test_run_internal_n_est_toujours_pas_touche():
    taille = (lambda m: m.end_lineno - m.lineno)(_methodes()["_run_internal"])
    assert taille > 5000, f"_run_internal fait {taille} lignes"


def test_les_lots_precedents_sont_intacts():
    from src.reasoning.react import ReActLoop

    assert isinstance(ReActLoop.__dict__["_is_mission_run"], property)
    for nom in ("_truth_lock_web_flag", "_mission_completion_evidence",
                "_pages_never_opened_reason", "_truth_lock_mission_message"):
        assert hasattr(ReActLoop, nom)
