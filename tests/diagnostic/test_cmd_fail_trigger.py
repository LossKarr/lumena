"""
Phase 0.2 — Tests du trigger Reflexion pour run_command échoué.

Contexte : observé en prod (session 09:35-09:37), 5 tentatives consécutives
`node --check` avec chemins différents n'ont déclenché aucune leçon parce que
les triggers existants ne couvraient que grep et str_replace.

Ce test vérifie :
1. La normalisation des signatures groupe les variantes
2. Le compteur incrémente correctement
3. La 3e répétition déclenche `_maybe_generate_reflexion`
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_normalize_signature_groups_variants():
    """Les 5 variantes vues en prod doivent normaliser vers la même signature."""
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent()
    sig_fn = agent._normalize_cmd_fail_signature

    variants = [
        ('node --check C:/Users/.../main.js', '[cmd_done] exit:1'),
        ('cd /d "C:\\..." && node --check js/main.js', '[cmd_done] exit:1'),
        ('cd C:/... && node --check js/main.js', '[cmd_done] exit:1'),
        ('node --check "C:/.../main.js"', '[cmd_done] exit:1'),
        ('node --check workspace/openworld3d/js/main.js', '[cmd_done] exit:1'),
    ]
    sigs = {sig_fn(cmd, obs) for cmd, obs in variants}
    # Toutes les variantes du 'node --check' avec exit:1 doivent matcher
    assert len(sigs) == 1, f"5 variantes devraient grouper en 1 sig, on a : {sigs}"
    sig = next(iter(sigs))
    assert sig == "node|exit:1", f"signature attendue 'node|exit:1', obtenue: {sig!r}"


def test_normalize_signature_different_verbs_different_keys():
    """Verbes différents → clés différentes."""
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent()
    sig_fn = agent._normalize_cmd_fail_signature

    assert sig_fn("npm install", "exit:1") == "npm|exit:1"
    assert sig_fn("python -m pytest", "exit:2") == "python|exit:2"
    assert sig_fn("git status", "exit:128") == "git|exit:128"
    assert sig_fn("ls /nonexistent", "not found") == "ls|not_found"


def test_normalize_signature_strips_cd_prefix():
    """Le préfixe `cd ... && ` ne doit pas affecter la signature."""
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent()
    sig_fn = agent._normalize_cmd_fail_signature

    a = sig_fn("node --check x.js", "exit:1")
    b = sig_fn('cd /d "C:\\foo" && node --check x.js', "exit:1")
    c = sig_fn("cd /tmp/bar && node --check x.js", "exit:1")
    assert a == b == c, f"cd prefix doit être ignoré: a={a} b={b} c={c}"


def test_normalize_signature_detects_error_types():
    """Différents indices d'erreur → clés différentes."""
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent()
    sig_fn = agent._normalize_cmd_fail_signature

    sigs = {
        sig_fn("node x.js", "Process exited with exit:1"),
        sig_fn("node x.js", "Chemin introuvable"),
        sig_fn("node x.js", "permission denied"),
        sig_fn("node x.js", "Cmdlet PowerShell bloquee"),
    }
    assert len(sigs) == 4, f"4 types d'erreur → 4 sigs distinctes, on a : {sigs}"


@pytest.mark.asyncio
async def test_run_command_third_failure_triggers_reflexion(monkeypatch):
    """Phase 0.2 — La 3e tentative `run_command` même signature → reflexion lancée."""
    from src.agents.sub_agent import CodeAgent, AgentTask, AgentType

    agent = CodeAgent()
    reflexion_calls = []

    async def fake_reflexion(signal, context_tail, task_hint=""):
        reflexion_calls.append({"signal": signal, "task_hint": task_hint})

    monkeypatch.setattr(agent, "_maybe_generate_reflexion", fake_reflexion)

    task = AgentTask(
        task_id="test_cmd_trigger",
        description="Test trigger run_command",
        agent_type=AgentType.CODE,
    )
    agent.current_task = task
    messages = [{"role": "user", "content": "init"}]

    # Signature réelle de _post_action_hooks (sans iteration)
    for i in range(3):
        action = {"action": "run_command", "command": f"node --check path{i}.js"}
        observation = "[cmd_done] sandbox exit:1"
        await agent._post_action_hooks(
            action=action,
            action_type="run_command",
            observation=observation,
            messages=messages,
            task=task,
            session_snapshots={},
            target_files_seen=[],
            edits_since_last_test=0,
            reads_since_last_edit=0,
            context_cache={},
        )

    import asyncio
    await asyncio.sleep(0.1)

    assert len(reflexion_calls) >= 1, (
        f"3 échecs run_command identiques doivent déclencher au moins 1 reflexion. "
        f"Calls observés : {reflexion_calls}"
    )
    assert "run_command" in reflexion_calls[0]["signal"]
    assert "node|exit:1" in reflexion_calls[0]["signal"]


@pytest.mark.asyncio
async def test_run_command_success_does_not_trigger(monkeypatch):
    """Phase 0.2 — Pas de trigger reflexion sur run_command qui réussit."""
    from src.agents.sub_agent import CodeAgent, AgentTask, AgentType

    agent = CodeAgent()
    reflexion_calls = []

    async def fake_reflexion(signal, context_tail, task_hint=""):
        reflexion_calls.append(signal)

    monkeypatch.setattr(agent, "_maybe_generate_reflexion", fake_reflexion)

    task = AgentTask(
        task_id="test_cmd_no_trigger",
        description="Test no trigger",
        agent_type=AgentType.CODE,
    )
    agent.current_task = task
    messages = [{"role": "user", "content": "init"}]

    for i in range(3):
        action = {"action": "run_command", "command": "node --check x.js"}
        observation = "[cmd_done] sandbox exit:0"
        await agent._post_action_hooks(
            action=action,
            action_type="run_command",
            observation=observation,
            messages=messages,
            task=task,
            session_snapshots={},
            target_files_seen=[],
            edits_since_last_test=0,
            reads_since_last_edit=0,
            context_cache={},
        )

    import asyncio
    await asyncio.sleep(0.05)

    assert reflexion_calls == [], (
        f"Succès run_command ne doit JAMAIS déclencher de reflexion. "
        f"Calls observés : {reflexion_calls}"
    )


def test_cmd_fail_count_initialized():
    """`_cmd_fail_count` doit exister à l'init et être un dict vide."""
    from src.agents.sub_agent import CodeAgent

    agent = CodeAgent()
    assert hasattr(agent, "_cmd_fail_count")
    assert isinstance(agent._cmd_fail_count, dict)
    assert agent._cmd_fail_count == {}
