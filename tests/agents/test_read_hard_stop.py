"""
Tests pour le HARD STOP anti-relecture dans CodeAgent._execute_loop_action.

Seuil = 3 : bloque à la 4ème lecture identique (sans modif entre-temps).

Vérifie :
  • 3 lectures identiques tolérées, 4ème bloquée (même path + même plage).
  • Nouvelles plages (args différents) toujours autorisées = gros fichier OK.
  • Compteur par signature (path::start::end), pas par path seul.
  • Reset auto du compteur si le fichier a été modifié entre-temps (mtime).
"""

import os
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


def test_architect_reread_budget_allows_two_then_stops():
    """Le micro-budget Architect autorise 2 relectures ciblées, pas plus."""
    from src.agents.sub_agent import _consume_architect_reread_budget

    budget = {}
    ok1, remaining1 = _consume_architect_reread_budget(budget, "js/game.js")
    ok2, remaining2 = _consume_architect_reread_budget(budget, "js/game.js")
    ok3, remaining3 = _consume_architect_reread_budget(budget, "js/game.js")

    assert ok1 is True and remaining1 == 1
    assert ok2 is True and remaining2 == 0
    assert ok3 is False and remaining3 == 0


def test_architect_reread_budget_respects_custom_default():
    """Le budget custom permet plus de relectures pour un bugfix local ciblé."""
    from src.agents.sub_agent import _consume_architect_reread_budget

    budget = {}
    allowed = []
    remaining = []
    for _ in range(6):
        ok, rem = _consume_architect_reread_budget(budget, "js/game.js", default_budget=5)
        allowed.append(ok)
        remaining.append(rem)

    assert allowed[:5] == [True, True, True, True, True]
    assert allowed[5] is False
    assert remaining[4] == 0


@pytest.mark.asyncio
async def test_hard_stop_blocks_6th_identical_read(tmp_path):
    """4ème lecture avec args EXACTEMENT identiques = refus 🛑 (seuil >= 3)."""
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent.__new__(CodeAgent)
    agent._task_workspace_root = tmp_path
    f = tmp_path / "big.py"
    f.write_text("\n".join(f"line {i}" for i in range(1, 201)))

    action = {"action": "read_file", "path": "big.py", "start_line": 1, "end_line": 50}

    for i in range(3):
        r = await agent._execute_loop_action(action)
        assert "🛑" not in str(r), f"Lecture #{i+1} bloquée à tort: {r}"

    r4 = await agent._execute_loop_action(action)
    assert "🛑" in str(r4), f"4ème lecture devrait être bloquée, got: {r4}"
    assert "REFUS" in str(r4)


@pytest.mark.asyncio
async def test_hard_stop_allows_different_ranges(tmp_path):
    """Gros fichier lu par plages distinctes = toutes autorisées."""
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent.__new__(CodeAgent)
    agent._task_workspace_root = tmp_path
    f = tmp_path / "huge.py"
    f.write_text("\n".join(f"line {i}" for i in range(1, 2001)))

    results = []
    for s, e in [(1, 500), (500, 1000), (1000, 1500), (1500, 2000), (1, 100), (100, 200)]:
        action = {"action": "read_file", "path": "huge.py", "start_line": s, "end_line": e}
        r = await agent._execute_loop_action(action)
        results.append(str(r))

    for r in results:
        assert "🛑" not in r, f"Plage distincte bloquée à tort: {r[:200]}"


@pytest.mark.asyncio
async def test_hard_stop_signature_is_path_plus_range(tmp_path):
    """La signature combine path+plage : même path avec plage différente = nouveau compteur."""
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent.__new__(CodeAgent)
    agent._task_workspace_root = tmp_path
    f = tmp_path / "mid.py"
    f.write_text("\n".join(f"line {i}" for i in range(1, 501)))

    action_a = {"action": "read_file", "path": "mid.py", "start_line": 1, "end_line": 100}
    for _ in range(3):
        r = await agent._execute_loop_action(action_a)
        assert "🛑" not in str(r)

    action_b = {"action": "read_file", "path": "mid.py", "start_line": 200, "end_line": 300}
    r_b = await agent._execute_loop_action(action_b)
    assert "🛑" not in str(r_b), f"Plage B devrait être autorisée: {r_b}"

    r_a4 = await agent._execute_loop_action(action_a)
    assert "🛑" in str(r_a4), f"4ème sur plage A devrait être bloquée: {r_a4}"


@pytest.mark.asyncio
async def test_hard_stop_resets_on_mtime_change(tmp_path):
    """Petit fichier modifié entre deux lectures → compteur reset via mtime."""
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent.__new__(CodeAgent)
    agent._task_workspace_root = tmp_path
    f = tmp_path / "small.py"
    f.write_text("\n".join(f"line {i}" for i in range(1, 51)))

    action = {"action": "read_file", "path": "small.py", "start_line": 1, "end_line": 50}

    for _ in range(3):
        r = await agent._execute_loop_action(action)
        assert "🛑" not in str(r)

    time.sleep(0.02)
    f.write_text("\n".join(f"MODIFIED {i}" for i in range(1, 51)))
    new_mtime = f.stat().st_mtime_ns + 10_000_000
    os.utime(f, ns=(new_mtime, new_mtime))

    r_after = await agent._execute_loop_action(action)
    assert "🛑" not in str(r_after), f"Lecture post-modif devrait être autorisée: {r_after}"

    for _ in range(2):
        r = await agent._execute_loop_action(action)
        assert "🛑" not in str(r)

    r4 = await agent._execute_loop_action(action)
    assert "🛑" in str(r4), f"4ème lecture sans modif devrait être bloquée: {r4}"
