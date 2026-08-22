"""Lot 5.4 — Teste la logique PURE de l'arbre missions (web/static/js/mission_tree.js)
en l'exécutant via node, + vérifie la syntaxe JS de panels.js / mission_tree.js.

L'UI elle-même se valide en runtime (Ctrl+F5, comme Lot 4.3) ; ici on verrouille le
CŒUR logique (groupement lead→workers, récursif, anti-cycle) qui doit être correct.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

_ROOT = Path(__file__).parents[2]
_JS_DIR = _ROOT / "web" / "static" / "js"
_TREE = _JS_DIR / "mission_tree.js"
_PANELS = _JS_DIR / "panels.js"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="node indisponible")


def _node(script: str) -> str:
    res = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, cwd=str(_ROOT), timeout=30,
    )
    assert res.returncode == 0, f"node a échoué: {res.stderr or res.stdout}"
    return res.stdout.strip()


def _build(missions: list) -> list:
    """Appelle buildMissionTree(missions) dans node, renvoie l'arbre simplifié
    [{id, children:[...]}] pour assertions."""
    payload = json.dumps(missions)
    script = (
        f"const {{buildMissionTree}}=require({json.dumps(str(_TREE))});"
        f"const simplify=(n)=>({{id:String(n.mission.task_id),children:n.children.map(simplify)}});"
        f"const roots=buildMissionTree({payload}).map(simplify);"
        f"process.stdout.write(JSON.stringify(roots));"
    )
    return json.loads(_node(script))


def _m(task_id, parent=None, state="running"):
    meta = {}
    if parent:
        meta["parent_id"] = parent
    return {"task_id": task_id, "state": state, "metadata": meta,
            "created_at": "2026-06-29T10:00:00", "updated_at": "2026-06-29T10:00:00"}


def test_lead_with_workers_nested():
    tree = _build([_m("lead"), _m("w1", "lead"), _m("w2", "lead")])
    assert len(tree) == 1
    assert tree[0]["id"] == "lead"
    assert sorted(c["id"] for c in tree[0]["children"]) == ["w1", "w2"]


def test_grandchildren_transitive():
    tree = _build([_m("lead"), _m("w1", "lead"), _m("gc", "w1")])
    assert tree[0]["id"] == "lead"
    assert tree[0]["children"][0]["id"] == "w1"
    assert tree[0]["children"][0]["children"][0]["id"] == "gc"


def test_orphan_worker_becomes_root():
    # parent absent de la liste → le worker remonte en racine (jamais perdu)
    tree = _build([_m("w1", "missing_lead")])
    assert [n["id"] for n in tree] == ["w1"]


def test_order_preserved_and_no_dup():
    tree = _build([_m("A"), _m("B"), _m("a1", "A")])
    assert [n["id"] for n in tree] == ["A", "B"]
    assert [c["id"] for c in tree[0]["children"]] == ["a1"]
    assert tree[1]["children"] == []


def test_self_parent_is_root_not_cycle():
    tree = _build([_m("x", "x")])  # auto-parent → racine, pas de boucle infinie
    assert [n["id"] for n in tree] == ["x"]


def test_worker_progress_and_elapsed():
    script = (
        f"const t=require({json.dumps(str(_TREE))});"
        "const node={children:[{mission:{state:'done'}},{mission:{state:'running'}},{mission:{state:'failed'}}]};"
        "const p=t.workerProgress(node);"
        "const e=t.missionElapsedMs({created_at:'2026-06-29T10:00:00',updated_at:'2026-06-29T10:00:30',state:'done'});"
        "process.stdout.write(JSON.stringify({p,e}));"
    )
    out = json.loads(_node(script))
    assert out["p"] == {"done": 2, "total": 3}   # done + failed = terminaux
    assert out["e"] == 30000                       # 30 s


def test_js_syntax_valid():
    for f in (_TREE, _PANELS):
        res = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True, timeout=30)
        assert res.returncode == 0, f"Syntaxe JS invalide dans {f.name}: {res.stderr}"
