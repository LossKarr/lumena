"""RF-8-FIX-1 — deux voies de sortie mission echappaient au verrou de verite.

Trouve par l'audit prealable de RF-8 (table des 33 retours de `_run_internal`).

--- L'affirmation qui n'etait pas vraie ---

La docstring de `_stream_and_return_final` dit :

    « LOT 2.7 — POINT D'ETRANGLEMENT du verrou de verite mission (run NoteFlash
      2026-07-02 : un FINAL fabrique "8/8 tests pytest verts" a ete emis sans
      passer par le verrou). TOUTE emission finale d'une mission passe ici. »

Trois sites appellent `apply_mission_truth_lock` dans `react.py` :

    l.3617   DANS `_stream_and_return_final`     <- le goulot
    l.7268   `if answer and self._is_mission_run:`
    l.9624   final deterministe

Deux retours sortent **AVANT** la l.7268 et **sans** passer par le goulot :

    l.7008   voie I3  (2026-08-13) : `if self._mission_worker_delivered():`
    l.7029   voie Z28 (2026-08-19) : apres `_mark_task_failed(...)`

Et ce qu'ils rendent vaut `answer if answer else "..."` — **la parole du
MODELE**, pas un constat fabrique par un garde.

Aucun rattrapage en aval : `run()` -> `_run_with_timeout()` -> `_run_internal()`,
aucun des deux n'applique le verrou.

--- La preuve mesuree, avant correctif ---

Meme phrase, sans aucune preuve au ledger :

    « J'ai termine la tache. Les 8 tests pytest sont VERTS et le module est
      publie dans workspace/comparatif. »

    avec verrou  -> 2 bannieres + « les 8 tests pytest sont VERTS » reecrit
                    en « les tests non prouves verts »
    voies I3/Z28 -> la revendication BRUTE, intacte

`overclaim=True`, `overclaim_tests=True`, `overclaim_published=True`.

--- Pourquoi I3 et Z28 avaient raison sur le fond ---

Un worker dont TOUS les fichiers assignes sont remplis a fait son travail : sa
conclusion tronquee ne rend pas son livrable inexistant. Le lot Z28 dit la meme
chose du lead. **Les deux ont raison sur l'ETAT.** Leur defaut est d'avoir
laisse passer la PAROLE avec.

--- Ce que ce lot NE fait PAS ---

Il ne reroute PAS ces sorties par `_stream_and_return_final`. Le goulot fait
aussi `_mark_task_done(message)` : sur la voie l.7029, qui vient d'appeler
`_mark_task_failed(...)`, cela CONTREDIRAIT l'etat. Il ne change ni le statut de
tache, ni le streaming, ni les metadonnees. **Il ajoute le verrou, et rien
d'autre.**
"""

from __future__ import annotations

import ast
import pathlib

import pytest


RACINE = pathlib.Path(__file__).resolve().parents[2]
REACT = RACINE / "src" / "reasoning" / "react.py"

#: La phrase du test, sans aucune preuve au ledger.
SURVENTE = (
    "J'ai termine la tache. Les 8 tests pytest sont VERTS et le module est "
    "publie dans workspace/comparatif."
)


def _classe():
    arbre = ast.parse(REACT.read_text(encoding="utf-8"))
    return next(n for n in arbre.body
                if isinstance(n, ast.ClassDef) and n.name == "ReActLoop")


def _run_internal():
    return next(n for n in _classe().body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == "_run_internal")


# ══════════════════════════════════════════════════════════════════════════
#  1. Le verrou a UNE SEULE source de verite pour ses arguments
# ══════════════════════════════════════════════════════════════════════════


def test_le_verrou_a_une_methode_dediee():
    """Ses ~20 arguments ne doivent pas etre recopies a chaque site : une
    derive d'argument entre deux copies rendrait le verrou incoherent selon la
    voie de sortie — exactement le defaut qu'on ferme."""
    from src.reasoning.react import ReActLoop

    assert hasattr(ReActLoop, "_truth_lock_mission_message"), (
        "aucune methode dediee : les arguments du verrou sont recopies"
    )


def test_la_methode_dediee_retrograde_une_survente():
    """Le comportement de reference : sans preuve, la parole est corrigee."""
    from src.reasoning.react import ReActLoop

    o = object.__new__(ReActLoop)

    # `_is_mission_run` est une PROPERTY : elle lit `task_id` et
    # `task_orchestrator`. Un stub qui pose un faux attribut ne la trompe pas.
    class _Orch:
        def get_task(self, _):
            return {"metadata": {"kind": "mission"}}

    o.task_orchestrator = _Orch()
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
    o._note_truth_lock_outcome = lambda info: None
    o.task_id = "t1"

    class _Led:
        def last_test_outcome(self):
            return None

        def has_any_mutation(self):
            return True

        def has_published(self):
            return False

    o.execution_ledger = _Led()

    verrouille = o._truth_lock_mission_message(SURVENTE)

    assert verrouille != SURVENTE, "la survente est livree telle quelle"
    assert "8 tests pytest sont VERTS" not in verrouille, (
        f"la revendication de tests verts survit : {verrouille[:200]!r}"
    )
    for banniere in ("Tests non", "Non publi"):
        assert banniere in verrouille, (
            f"la banniere « {banniere} » manque : {verrouille[:250]!r}"
        )


# ══════════════════════════════════════════════════════════════════════════
#  2. LES DEUX VOIES — le defaut du lot
# ══════════════════════════════════════════════════════════════════════════


def _retours_hors_verrou():
    """Retours de `_run_internal` situes entre la voie I3 et le verrou l.7268,
    et qui rendent une variable (donc la parole du modele)."""
    ri = _run_internal()
    src = REACT.read_text(encoding="utf-8").splitlines()
    dedans = []
    for n in ast.walk(ri):
        if not isinstance(n, ast.Return) or n.value is None:
            continue
        txt = ast.unparse(n.value)
        if "_stream_and_return_final" in txt or "_truth_lock_mission_message" in txt:
            continue
        ligne = src[n.lineno - 1].strip()
        dedans.append((n.lineno, txt[:60], ligne[:80]))
    return dedans


def _retours_de_run_internal():
    """Les `return` de `_run_internal`, par AST.

    ⚠️ Une premiere version de ces tests cherchait les motifs par `str.index`
    dans le fichier — et les trouvait dans la DOCSTRING du correctif, qui les
    cite. C'est le piege substring-vs-AST, deja rencontre trois fois dans ce
    chantier (`DEFAULT_IDENTITY`, `_mission_routing_objective`,
    `_emit_plan_state`). Un test doit juger le CODE, jamais sa documentation.
    """
    return [n for n in ast.walk(_run_internal()) if isinstance(n, ast.Return)]


def _retour_verrouille(fragment_condition: str) -> bool:
    """True si le `return` gouverne par cette condition applique le verrou.

    ⚠️ `_run_internal()` RE-PARSE le fichier a chaque appel : les noeuds de deux
    appels n'appartiennent pas au meme arbre, donc `cur in parents` ne matche
    jamais. Les retours et la table des parents sont construits ICI, sur UN
    SEUL arbre. (Defaut trouve dans ce test meme.)
    """
    ri = _run_internal()
    parents = {}
    for n in ast.walk(ri):
        for c in ast.iter_child_nodes(n):
            parents[c] = n
    for n in [x for x in ast.walk(ri) if isinstance(x, ast.Return)]:
        if n.value is None:
            continue
        cur = n
        conds = []
        while cur in parents:
            p = parents[cur]
            if isinstance(p, ast.If) and cur in p.body:
                conds.append(ast.unparse(p.test))
            cur = p
            if p is ri:
                break
        if any(fragment_condition in c for c in conds):
            if "_truth_lock_mission_message" in ast.unparse(n.value):
                return True
    return False


def test_la_voie_I3_applique_le_verrou():
    """LE test du lot, cote I3.

    `if self._mission_worker_delivered(): ... return message` — un worker dont
    les fichiers sont remplis rendait sa conclusion SANS verrou.
    """
    assert _retour_verrouille("_mission_worker_delivered"), (
        "la voie I3 rend la parole du modele sans verrou de verite"
    )


def test_la_voie_Z28_applique_le_verrou():
    """LE test du lot, cote Z28 — le retour qui suit `_mark_task_failed`."""
    ri = _run_internal()
    src = REACT.read_text(encoding="utf-8").splitlines()
    # Le retour Z28 suit immediatement un appel a `_mark_task_failed`.
    verrouille = False
    for n in _retours_de_run_internal():
        if n.value is None or "_truth_lock_mission_message" not in ast.unparse(n.value):
            continue
        amont = "\n".join(src[max(0, n.lineno - 8):n.lineno])
        if "_mark_task_failed" in amont:
            verrouille = True
    assert verrouille, (
        "la voie Z28 rend la parole du modele sans verrou de verite"
    )


def test_le_goulot_reste_le_goulot():
    """Non-regression : le verrou historique de `_stream_and_return_final` ne
    disparait pas au profit du nouveau chemin."""
    from src.reasoning.react import ReActLoop
    import inspect

    src = inspect.getsource(ReActLoop._stream_and_return_final)
    assert "_truth_lock_mission_message" in src or "apply_mission_truth_lock" in src


# ══════════════════════════════════════════════════════════════════════════
#  3. Ce que le lot ne doit pas casser
# ══════════════════════════════════════════════════════════════════════════


def test_le_statut_de_tache_n_est_PAS_touche():
    """Le goulot fait `_mark_task_done`. Sur la voie Z28, qui vient d'appeler
    `_mark_task_failed`, cela CONTREDIRAIT l'etat. Le correctif ajoute le
    verrou, il ne reroute pas."""
    # Juge par AST, pas par texte : la docstring du correctif cite
    # `_stream_and_return_final` pour expliquer pourquoi il NE l'utilise pas.
    ri = _run_internal()
    src = REACT.read_text(encoding="utf-8").splitlines()
    reroute = False
    marquage = False
    for n in _retours_de_run_internal():
        if n.value is None:
            continue
        amont = "\n".join(src[max(0, n.lineno - 8):n.lineno])
        if "_mark_task_failed" not in amont:
            continue
        marquage = True
        if "_stream_and_return_final" in ast.unparse(n.value):
            reroute = True
    assert marquage, "le marquage d'echec a disparu"
    assert not reroute, (
        "une voie qui vient de `_mark_task_failed` a ete reroutee par le "
        "goulot : `_mark_task_done` va contredire l'etat"
    )


def test_le_verrou_est_inerte_hors_mission():
    """Le chat ne porte pas de bannieres de mission."""
    from src.reasoning.react import ReActLoop

    o = object.__new__(ReActLoop)
    o.task_id = ""
    o.task_orchestrator = None
    rendu = o._truth_lock_mission_message("Voici la reponse.")
    assert rendu == "Voici la reponse."


def test_un_etat_casse_ne_change_pas_la_parole():
    """Invariant 6 : une exception ne devient ni une autorisation ni une
    mutilation du message. Le goulot historique fait deja ce choix
    (`except ... : logger.debug(...)`, message inchange)."""
    from src.reasoning.react import ReActLoop

    o = object.__new__(ReActLoop)
    o.task_id = "t1"

    class _OrchCasse:
        def get_task(self, _):
            raise RuntimeError("boom")

    o.task_orchestrator = _OrchCasse()
    rendu = o._truth_lock_mission_message(SURVENTE)
    assert rendu == SURVENTE


def test_un_message_vide_reste_vide():
    from src.reasoning.react import ReActLoop

    o = object.__new__(ReActLoop)
    o.task_id = ""
    o.task_orchestrator = None
    assert o._truth_lock_mission_message("") == ""
