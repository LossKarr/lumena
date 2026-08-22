"""H4-b — un worker d'EFFETS est un worker, même sans posséder de fichier.

Trouvé par le TEST RÉEL `veille_python_313` (2026-08-13), invisible aux 42 tests
de H4. La mission de veille a livré son bilan coiffé de :

    ⚠️ **Navigateur NON vérifié** — livrable web sans action navigateur réussie

…sur une recherche documentaire. La bannière n'était pas posée par le lead mais
par **w_recherche**, un worker.

Cause : neuf gardes réservés au TOP-LEAD posaient la question « suis-je un
worker ? » sous la forme `if self._mission_allowed_files_meta():` — autrement dit
*« un worker, c'est quelqu'un qui possède des fichiers »*. H4 introduit des
porteurs d'EFFETS purs, qui n'en possèdent aucun (`allowed_files: []`) : ils
étaient donc pris pour le lead et recevaient BROWSER GATE, CONTRACT GATE,
verdicts runtime web, gate JS et flags jeu/interaction.

Le fait déterministe est `metadata.parent_id`, posé par `delegate_and_wait` pour
TOUT enfant, avec ou sans fichiers.

Sûreté par construction : un worker de code a déjà un périmètre (inchangé), un
lead n'a jamais de `parent_id` (inchangé). Seul le cas neuf bascule.
"""
from __future__ import annotations

import types

import pytest

from src.reasoning.react import ReActLoop


def _loop(meta):
    """ReActLoop nu — on n'exerce que les deux prédicats, sans construire un run."""
    loop = object.__new__(ReActLoop)
    loop.task_id = "task_x"
    loop.task_orchestrator = types.SimpleNamespace(
        get_task=lambda _id: {"metadata": dict(meta)}
    )
    return loop


# ── Le prédicat « suis-je un worker » ───────────────────────────────────────

def test_effect_worker_is_recognised_as_a_worker():
    """Le cas du run : aucun fichier, mais un parent."""
    assert _loop({"parent_id": "task_lead", "allowed_files": []})._is_worker_run()


def test_effect_worker_is_recognised_by_its_owner_alone():
    assert _loop({"delegation_owner": "w_notif"})._is_worker_run()


def test_code_worker_is_still_a_worker():
    """Comportement historique : le périmètre suffit, avec ou sans parent."""
    assert _loop({"allowed_files": ["app.py"]})._is_worker_run()
    assert _loop({"allowed_files": ["app.py"], "parent_id": "task_lead"})._is_worker_run()


def test_the_lead_is_never_a_worker():
    """Le lead n'a ni périmètre ni parent — c'est ce qui le définit."""
    assert not _loop({"mission_workspace": "missions/task_x"})._is_worker_run()
    assert not _loop({})._is_worker_run()


def test_broken_orchestrator_never_turns_a_lead_into_a_worker():
    loop = object.__new__(ReActLoop)
    loop.task_id = "task_x"
    loop.task_orchestrator = types.SimpleNamespace(
        get_task=lambda _id: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert not loop._is_worker_run()


def test_without_orchestrator_nothing_is_a_worker():
    loop = object.__new__(ReActLoop)
    loop.task_id = None
    loop.task_orchestrator = None
    assert not loop._is_worker_run()


# ── La conséquence : la policy navigateur ne vise plus les workers ──────────

def test_effect_worker_does_not_carry_the_browser_policy():
    """Le défaut exact du run : `w_recherche` recevait le flag du TOP-LEAD."""
    loop = _loop({"parent_id": "task_lead", "allowed_files": []})
    loop._mission_web_present_for_gate = lambda: "page web déclarée au contrat"
    assert loop._truth_lock_web_flag() is False


def test_code_worker_does_not_carry_it_either():
    loop = _loop({"allowed_files": ["static/index.html"]})
    loop._mission_web_present_for_gate = lambda: "page web écrite pendant ce run"
    assert loop._truth_lock_web_flag() is False


def test_the_lead_still_carries_the_browser_policy():
    """Le risque du correctif est d'éteindre la policy pour tout le monde."""
    loop = _loop({"mission_workspace": "missions/task_x"})
    loop._mission_web_present_for_gate = lambda: "page web déclarée au contrat"
    assert loop._truth_lock_web_flag() is True


def test_the_lead_without_web_deliverable_stays_false():
    loop = _loop({"mission_workspace": "missions/task_x"})
    loop._mission_web_present_for_gate = lambda: ""
    assert loop._truth_lock_web_flag() is False


@pytest.mark.parametrize("guard", [
    "_truth_lock_web_flag",
    "_browser_runtime_failed_for_truth_lock",
    "_browser_runtime_verified_for_truth_lock",
])
def test_top_lead_guards_are_inert_for_an_effect_worker(guard):
    """Les gardes « scope top-lead » doivent TOUS se taire pour un worker
    d'effets — sinon il croit détenir un verdict qui ne lui appartient pas."""
    loop = _loop({"parent_id": "task_lead", "allowed_files": []})
    loop._mission_web_present_for_gate = lambda: "page web déclarée au contrat"
    assert getattr(loop, guard)() is False
