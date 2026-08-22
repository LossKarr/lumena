"""H3 — le parent ne réécrit pas le fichier d'un worker encore vivant.

Run SuiviDepenses (2026-08-12) : croyant ses workers bloqués — à tort, cf. H2 —
le lead a « repris leur périmètre » :

    23:53:12  le lead édite app.py … pendant que le CodeAgent de w_backend l'écrit
    23:55:03  w_frontend termine
    00:02:53  w_tests termine, le parent est déjà « terminé »

Le lease de ressources (`resource_key_for` → `files:<chemin>`) existe depuis le
Lot 0.c, mais c'est un **verrou d'instant** : il sérialise deux écritures
simultanées et empêche la corruption. Il n'empêche pas le lead d'écrire *entre*
deux écritures du worker — donc pas la divergence.

Ce qui manquait est une **propriété dans la durée** : tant que `w_backend` n'est
pas terminal, `app.py` lui appartient. C'est la même règle que le Lot 5-C (« le
chat ne baby-sitte plus la mission »), appliquée un cran plus bas.
"""
from __future__ import annotations

import json
import types

import pytest

from src.subagents.mission_contract import live_owner_of_path

_CONTRACT = {
    "files": [
        {"path": "app.py", "owner": "w_backend"},
        {"path": "static/script.js", "owner": "w_frontend"},
        {"path": "tests/test_app.py", "owner": "w_tests"},
    ]
}


def _child(owner, state):
    return {"state": state, "metadata": {"delegation_owner": owner}}


# ── La règle de propriété (fonction pure) ────────────────────────────────────

def test_file_of_a_working_worker_is_protected():
    """Le cas exact du run : w_backend `checkpointed`, donc app.py est à lui."""
    kids = [_child("w_backend", "checkpointed")]
    assert live_owner_of_path("app.py", _CONTRACT, kids) == "w_backend"


def test_file_is_free_once_its_owner_is_done():
    """Le lead DOIT pouvoir intégrer quand le worker a fini — sinon on casse
    la fin normale de toutes les missions."""
    for final in ("done", "failed", "cancelled"):
        kids = [_child("w_backend", final)]
        assert live_owner_of_path("app.py", _CONTRACT, kids) == ""


def test_another_workers_file_is_not_protected_by_proxy():
    """w_frontend vivant ne verrouille pas les fichiers de w_backend."""
    kids = [_child("w_frontend", "running")]
    assert live_owner_of_path("app.py", _CONTRACT, kids) == ""
    assert live_owner_of_path("static/script.js", _CONTRACT, kids) == "w_frontend"


def test_unknown_file_is_never_locked():
    kids = [_child("w_backend", "running")]
    assert live_owner_of_path("README.md", _CONTRACT, kids) == ""


def test_no_contract_no_lock():
    kids = [_child("w_backend", "running")]
    assert live_owner_of_path("app.py", {}, kids) == ""
    assert live_owner_of_path("app.py", None, kids) == ""


def test_no_children_no_lock():
    assert live_owner_of_path("app.py", _CONTRACT, []) == ""
    assert live_owner_of_path("app.py", _CONTRACT, None) == ""


def test_child_without_owner_metadata_is_ignored():
    """Un enfant sans `delegation_owner` (mission simple) ne verrouille rien."""
    assert live_owner_of_path("app.py", _CONTRACT, [{"state": "running"}]) == ""


def test_garbage_never_raises():
    assert live_owner_of_path("app.py", _CONTRACT, ["pas un dict"]) == ""
    assert live_owner_of_path(None, _CONTRACT, [_child("w_backend", "running")]) == ""


# ── Le branchement : le lead est refusé, tout le reste passe ─────────────────

def _ctx(tmp_path, *, children, mission=True, sub="missions/task_x", with_contract=True):
    mission_dir = tmp_path / sub
    mission_dir.mkdir(parents=True, exist_ok=True)
    if with_contract:
        (mission_dir / "contract.json").write_text(
            json.dumps(_CONTRACT), encoding="utf-8"
        )
    orch = types.SimpleNamespace(get_children=lambda _id: children)
    core = types.SimpleNamespace(task_orchestrator=orch)
    return types.SimpleNamespace(
        is_mission_run=mission,
        runtime_task_id="task_x",
        lumena=core,
        mission_workspace_subdir=lambda: sub,
        file_guardrails=types.SimpleNamespace(_workspace_root=lambda: tmp_path),
        mission_allowed_files_set=lambda: None,   # le LEAD n'a pas de périmètre
    )


def _assert_guard(ctx, path):
    from src.reasoning.handlers.files import _assert_not_owned_by_live_worker
    _assert_not_owned_by_live_worker(path, ctx)


def test_lead_is_refused_while_the_owner_works(tmp_path):
    from src.tools.file_guardrails import PathSecurityError

    ctx = _ctx(tmp_path, children=[_child("w_backend", "checkpointed")])
    with pytest.raises(PathSecurityError) as e:
        _assert_guard(ctx, tmp_path / "missions/task_x/app.py")
    assert "w_backend" in str(e.value)
    assert "cancel_mission" in str(e.value), "il faut une sortie explicite"


def test_lead_may_integrate_once_workers_are_done(tmp_path):
    ctx = _ctx(tmp_path, children=[_child("w_backend", "done")])
    _assert_guard(ctx, tmp_path / "missions/task_x/app.py")  # ne lève pas


def test_outside_missions_the_guard_is_inert(tmp_path):
    ctx = _ctx(tmp_path, children=[_child("w_backend", "running")], mission=False)
    _assert_guard(ctx, tmp_path / "missions/task_x/app.py")


def test_without_contract_the_guard_is_inert(tmp_path):
    ctx = _ctx(
        tmp_path, children=[_child("w_backend", "running")], with_contract=False
    )
    _assert_guard(ctx, tmp_path / "missions/task_x/app.py")


def test_file_outside_the_mission_dir_is_not_our_business(tmp_path):
    ctx = _ctx(tmp_path, children=[_child("w_backend", "running")])
    _assert_guard(ctx, tmp_path / "ailleurs" / "app.py")


def test_directories_are_never_locked(tmp_path):
    from src.reasoning.handlers.files import _assert_not_owned_by_live_worker
    ctx = _ctx(tmp_path, children=[_child("w_backend", "running")])
    _assert_not_owned_by_live_worker(
        tmp_path / "missions/task_x/static", ctx, is_dir=True
    )


def test_broken_context_never_blocks(tmp_path):
    class _Boom:
        @property
        def is_mission_run(self):
            raise RuntimeError("contexte cassé")

    _assert_guard(_Boom(), tmp_path / "app.py")
