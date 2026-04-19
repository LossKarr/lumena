"""Tests P3 — plan mode read-only."""
from __future__ import annotations

from pathlib import Path

import pytest


def test_plan_mode_hooks_present_in_sub_agent():
    src = Path(__file__).resolve().parents[2] / "src" / "agents" / "sub_agent.py"
    content = src.read_text(encoding="utf-8")
    assert "PLAN_MODE" in content
    assert "_plan_mode_read_only" in content
    assert "Plan mode read-only actif" in content


def test_flag_default_on():
    from src.config.codeagent_flags import PLAN_MODE
    assert PLAN_MODE is True


@pytest.mark.asyncio
async def test_plan_read_only_blocks_mutation(tmp_path, monkeypatch):
    """Un plan read_only=True suivi d'un edit_file doit être bloqué."""
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent.__new__(CodeAgent)
    agent.workspace_path = str(tmp_path)
    agent._files_snapshot_cache = {}
    agent._plan_mode_read_only = False

    # 1) Plan avec read_only=True
    r1 = await agent._execute_loop_action({
        "action": "plan",
        "steps": ["a", "b"],
        "read_only": True,
    })
    assert "lecture seule" in r1.summary or "read_only" in r1.summary.lower() or "Plan noté" in r1.summary
    assert agent._plan_mode_read_only is True

    # 2) Tentative d'edit_file → bloquée
    f = tmp_path / "test.py"
    f.write_text("x=1\n", encoding="utf-8")
    r2 = await agent._execute_loop_action({
        "action": "write_file",
        "path": "test.py",
        "content": "x=2\n",
    })
    assert "read-only" in r2.summary.lower() or "🔒" in r2.summary


@pytest.mark.asyncio
async def test_plan_read_only_false_clears_mode(tmp_path):
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent.__new__(CodeAgent)
    agent.workspace_path = str(tmp_path)
    agent._files_snapshot_cache = {}
    agent._plan_mode_read_only = True

    # Nouveau plan avec read_only=False → clear mode
    r = await agent._execute_loop_action({
        "action": "plan",
        "steps": ["x"],
        "read_only": False,
    })
    assert agent._plan_mode_read_only is False
    assert "Plan noté" in r.summary


@pytest.mark.asyncio
async def test_plan_mode_allows_read_actions(tmp_path):
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent.__new__(CodeAgent)
    agent.workspace_path = str(tmp_path)
    agent._files_snapshot_cache = {}
    agent._plan_mode_read_only = True

    # read_file ne doit PAS être bloqué
    f = tmp_path / "x.txt"
    f.write_text("hello\n", encoding="utf-8")
    r = await agent._execute_loop_action({
        "action": "read_file",
        "path": "x.txt",
    })
    # Pas le message de blocage
    assert "read-only" not in r.summary.lower()
    assert "🔒" not in r.summary


@pytest.mark.asyncio
async def test_plan_mode_disabled_by_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_PLAN_MODE", "false")
    import importlib
    import src.config.codeagent_flags as cf
    importlib.reload(cf)

    from src.agents.sub_agent import CodeAgent
    agent = CodeAgent.__new__(CodeAgent)
    agent.workspace_path = str(tmp_path)
    agent._files_snapshot_cache = {}
    agent._plan_mode_read_only = True  # simulate stuck state

    f = tmp_path / "y.py"
    f.write_text("a=1\n", encoding="utf-8")
    r = await agent._execute_loop_action({
        "action": "write_file",
        "path": "y.py",
        "content": "a=2\n",
    })
    # Avec flag off, l'action ne doit PAS être bloquée par read-only
    assert "read-only" not in r.summary.lower()
    importlib.reload(cf)  # restore


def test_mutating_set_includes_critical_actions():
    src = Path(__file__).resolve().parents[2] / "src" / "agents" / "sub_agent.py"
    content = src.read_text(encoding="utf-8")
    # Assure que les actions destructives sont dans _MUTATING
    for act in ("edit_file", "write_file", "apply_patch", "str_replace", "run_command"):
        assert f'"{act}"' in content
