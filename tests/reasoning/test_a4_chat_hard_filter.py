"""A4 Couche 0 — filtre `chat` DUR à l'exécution (anti-injection inter-Lumena).

Un pair niveau `chat` (lecture seule) ne doit RIEN pouvoir exécuter d'autre que
sa liste blanche, MÊME si une injection fait appeler un outil d'action.
Le soft-filter de l'agent LOCAL (hard=False) reste inchangé.
"""
from __future__ import annotations

import pytest

from src.reasoning.tool_registry import ToolRegistry

_A4_REFUS = "n'a que "  # fragment du message de refus A4 (« …n'a que le niveau 'chat' »)


def _reg(tmp_path, hard: bool):
    r = ToolRegistry(lumena=None, lumena_root=tmp_path)
    # Simule un niveau chat : liste blanche lecture seule.
    r._allowed_tools = {"memory_search", "web_search", "get_time"}
    r._caller_set_allowed = True
    r._allowed_tools_hard = hard
    return r


@pytest.mark.asyncio
async def test_chat_hard_refuse_action_tool(tmp_path):
    r = _reg(tmp_path, hard=True)
    obs = await r.execute("write_file", {"path": "x.txt", "content": "hack"})
    assert obs.success is False
    assert _A4_REFUS in obs.content
    # le fichier ne doit PAS avoir été écrit
    assert not (tmp_path / "x.txt").exists()


@pytest.mark.asyncio
async def test_chat_soft_does_not_refuse_with_a4_message(tmp_path):
    # hard=False (agent local) → PAS le refus A4 (comportement soft inchangé)
    r = _reg(tmp_path, hard=False)
    obs = await r.execute("write_file", {"path": "y.txt", "content": "ok"})
    assert _A4_REFUS not in obs.content  # jamais le message A4 en mode local


@pytest.mark.asyncio
async def test_chat_hard_allows_whitelisted_tool(tmp_path):
    # un outil de la liste blanche n'est jamais refusé par A4
    r = _reg(tmp_path, hard=True)
    obs = await r.execute("get_time", {})
    assert _A4_REFUS not in obs.content


@pytest.mark.asyncio
async def test_chat_hard_exempts_control_tools(tmp_path):
    # les outils de contrôle (plan_*) ne sont pas bloqués par A4 (l'agent doit
    # pouvoir conclure) — quel que soit le sort réel de l'appel.
    r = _reg(tmp_path, hard=True)
    obs = await r.execute("plan_create", {"tasks": []})
    assert _A4_REFUS not in obs.content


@pytest.mark.asyncio
async def test_hard_flag_default_false(tmp_path):
    r = ToolRegistry(lumena=None, lumena_root=tmp_path)
    assert r._allowed_tools_hard is False
