"""Tests P8 — 4 sous-features: DID_YOU_MEAN, CRLF_NORMALIZE, ENV_CONTEXT, INVALID_TOOL_CATCH."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest


# ══════════════════════════════════════════════════════════
# P8.DID_YOU_MEAN
# ══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_did_you_mean_suggests_close_action(tmp_path):
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent.__new__(CodeAgent)
    agent.workspace_path = str(tmp_path)
    agent._files_snapshot_cache = {}

    r = await agent._execute_loop_action({"action": "readfiles"})
    assert "voulais-tu dire" in r.summary
    assert "read_file" in r.summary or "read_files_batch" in r.summary


@pytest.mark.asyncio
async def test_did_you_mean_no_false_positive_on_unrelated(tmp_path):
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent.__new__(CodeAgent)
    agent.workspace_path = str(tmp_path)
    agent._files_snapshot_cache = {}

    r = await agent._execute_loop_action({"action": "xyzzy_totally_random_123"})
    assert "Action inconnue" in r.summary
    # Pas de suggestion pour chose totalement hors-vocabulaire
    assert "voulais-tu dire" not in r.summary


@pytest.mark.asyncio
async def test_did_you_mean_disabled_by_flag(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_DID_YOU_MEAN", "false")
    import src.config.codeagent_flags as cf
    importlib.reload(cf)

    from src.agents.sub_agent import CodeAgent
    agent = CodeAgent.__new__(CodeAgent)
    agent.workspace_path = str(tmp_path)
    agent._files_snapshot_cache = {}

    r = await agent._execute_loop_action({"action": "readfiles"})
    assert "voulais-tu dire" not in r.summary
    importlib.reload(cf)


# ══════════════════════════════════════════════════════════
# P8.CRLF_NORMALIZE
# ══════════════════════════════════════════════════════════

@pytest.mark.asyncio
async def test_crlf_normalize_on_write(tmp_path):
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent.__new__(CodeAgent)
    agent.workspace_path = str(tmp_path)
    agent._task_workspace_root = tmp_path
    agent._files_snapshot_cache = {}

    # Écrire un fichier .py avec CRLF
    content = "def foo():\r\n    return 1\r\n"
    await agent._write_file_action("test.py", content)

    f = tmp_path / "test.py"
    raw = f.read_bytes()
    # Pas de \r\n résiduel
    assert b"\r\n" not in raw
    assert b"\n" in raw


@pytest.mark.asyncio
async def test_crlf_normalize_skips_binary_extensions(tmp_path):
    """CRLF normalize ne touche pas les extensions non-listées (ex: .bin)."""
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent.__new__(CodeAgent)
    agent.workspace_path = str(tmp_path)
    agent._task_workspace_root = tmp_path
    agent._files_snapshot_cache = {}

    content = "line1\r\nline2\r\n"
    await agent._write_file_action("data.bin", content)
    raw = (tmp_path / "data.bin").read_bytes()
    # .bin n'est pas dans la liste → pas de normalisation
    assert b"\r\n" in raw


@pytest.mark.asyncio
async def test_crlf_normalize_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_CRLF_NORMALIZE", "false")
    import src.config.codeagent_flags as cf
    importlib.reload(cf)

    from src.agents.sub_agent import CodeAgent
    agent = CodeAgent.__new__(CodeAgent)
    agent.workspace_path = str(tmp_path)
    agent._task_workspace_root = tmp_path
    agent._files_snapshot_cache = {}

    content = "a=1\r\nb=2\r\n"
    await agent._write_file_action("t.py", content)
    raw = (tmp_path / "t.py").read_bytes()
    # Flag off → CRLF préservés
    assert b"\r\n" in raw
    importlib.reload(cf)


# ══════════════════════════════════════════════════════════
# P8.ENV_CONTEXT
# ══════════════════════════════════════════════════════════

def test_env_context_block_contains_os_and_python():
    from src.utils.env_context import build_env_context_block
    block = build_env_context_block()
    assert "ENVIRONNEMENT" in block
    assert "OS:" in block
    assert "Python:" in block
    assert "CWD:" in block


def test_env_context_flag_off_returns_empty(monkeypatch):
    monkeypatch.setenv("LUMENA_ENV_CONTEXT", "false")
    import src.config.codeagent_flags as cf
    importlib.reload(cf)
    import src.utils.env_context as ec
    importlib.reload(ec)
    assert ec.build_env_context_block() == ""
    importlib.reload(cf)
    importlib.reload(ec)


def test_env_context_injected_in_system_prompt():
    from src.agents.sub_agent import _build_system_prompt
    p = _build_system_prompt("test")
    assert "ENVIRONNEMENT" in p


# ══════════════════════════════════════════════════════════
# P8.INVALID_TOOL_CATCH
# ══════════════════════════════════════════════════════════

def test_invalid_tool_catch_tool_alias():
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent.__new__(CodeAgent)
    messages = []
    report = []
    raw = '{"tool": "read_file", "path": "x.py"}'
    tag, action = agent._process_llm_response(raw, 1, messages, report)
    assert tag == "action"
    assert action["action"] == "read_file"


def test_invalid_tool_catch_name_alias():
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent.__new__(CodeAgent)
    messages = []
    report = []
    raw = '{"name": "grep", "pattern": "foo", "path": "."}'
    tag, action = agent._process_llm_response(raw, 1, messages, report)
    assert tag == "action"
    assert action["action"] == "grep"


def test_invalid_tool_catch_flag_off(monkeypatch):
    monkeypatch.setenv("LUMENA_INVALID_TOOL_CATCH", "false")
    import src.config.codeagent_flags as cf
    importlib.reload(cf)

    from src.agents.sub_agent import CodeAgent
    agent = CodeAgent.__new__(CodeAgent)
    messages = []
    report = []
    raw = '{"tool": "read_file", "path": "x.py"}'
    tag, action = agent._process_llm_response(raw, 1, messages, report)
    # Flag off → ne récupère pas l'alias → success_text
    assert tag == "success_text"
    importlib.reload(cf)
