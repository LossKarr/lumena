"""CONN-5D-2 - rail CodeAgent pour les outils IDE.

Audit du 15 septembre 2026 : CodeAgent execute ses outils par le singleton
`get_tool_system()`, lie au registre PARTAGE du chat, et SANS appelant. Delegue par
un worker de mission, un appel `ide__*` etait donc juge hors mission et sans
identite. Les registres de mission ne sont jamais lies au singleton (par conception).

Correctif, limite aux outils IDE : le registre qui execute un outil est expose
pendant cette execution ; `ToolSystem.execute_tool` y envoie les `ide__*` s'il
existe, avec l'appelant fourni ; CodeAgent fournit `codeagent`. Les outils natifs de
CodeAgent ne changent pas de registre.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from src.reasoning.caller_context import CODEAGENT, REACT, CallerContext
from src.reasoning.react_config import Observation
from src.reasoning.tool_call_scope import current_tool_registry, tool_registry_scope
from src.subagents.mission_identity import mission_runtime_scope
from src.tools.tool_system import LumenaToolSystem, ToolCall
from tests.reasoning.test_conn3c_live_catalogue import external_tool_run, registry as registry
from tests.reasoning.test_conn5a_ide_mission_gate import _envoi, _ide_ouverte_sur, _mission
from tests.tools.test_conn3c_ide_capabilities import service as service

PROPRIETAIRE = {"user_role": "owner", "user_id": "local:owner", "owner_user_id": "local:owner", "channel": "web"}


def _faux_registre(*outils):
    faux = MagicMock()
    faux.execute = AsyncMock(return_value="ok")
    faux.tools = {nom: MagicMock() for nom in outils}
    return faux


# ══════════════════════════════════════════════════════════════════════════
#  1. Registre courant
# ══════════════════════════════════════════════════════════════════════════


def test_hors_execution_aucun_registre_courant():
    assert current_tool_registry() is None


def test_portee_explicite_restauree():
    premier, second = object(), object()
    with tool_registry_scope(premier):
        assert current_tool_registry() is premier
        with tool_registry_scope(second):
            assert current_tool_registry() is second
        assert current_tool_registry() is premier
    assert current_tool_registry() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("outil", ["ide__get_status", "get_time"])
async def test_registre_courant_pendant_un_outil(registry, monkeypatch, outil):
    vu = []

    async def inner(name, args, *, caller=None):
        vu.append(current_tool_registry())
        return Observation(content="ok", success=True)

    monkeypatch.setattr(registry, "_execute_inner", inner)
    await registry.execute(outil, {})
    assert vu == [registry]
    assert current_tool_registry() is None


# ══════════════════════════════════════════════════════════════════════════
#  2. ToolSystem : natif inchange, IDE sur le registre courant
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_outil_natif_forme_d_appel_inchangee_sans_appelant():
    chat = _faux_registre("get_time")
    shim = LumenaToolSystem()
    shim.bind_tool_registry(chat)
    assert (await shim.execute_tool(ToolCall(name="get_time", arguments={}))).success
    chat.execute.assert_awaited_once_with("get_time", {})


@pytest.mark.asyncio
async def test_outil_natif_recoit_l_appelant_fourni():
    chat = _faux_registre("get_time")
    shim = LumenaToolSystem()
    shim.bind_tool_registry(chat)
    await shim.execute_tool(ToolCall(name="get_time", arguments={}), caller=CODEAGENT)
    chat.execute.assert_awaited_once_with("get_time", {}, caller=CODEAGENT)


@pytest.mark.asyncio
async def test_outil_natif_reste_sur_le_registre_lie_meme_en_mission():
    chat, mission = _faux_registre("get_time"), _faux_registre("get_time")
    shim = LumenaToolSystem()
    shim.bind_tool_registry(chat)
    with tool_registry_scope(mission):
        await shim.execute_tool(ToolCall(name="get_time", arguments={}), caller=CODEAGENT)
    chat.execute.assert_awaited_once()
    mission.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_outil_ide_execute_sur_le_registre_courant():
    chat, mission = _faux_registre("ide__read_file"), _faux_registre("ide__read_file")
    shim = LumenaToolSystem()
    shim.bind_tool_registry(chat)
    with tool_registry_scope(mission):
        await shim.execute_tool(ToolCall(name="ide__read_file", arguments={"path": "a"}), caller=CODEAGENT)
    mission.execute.assert_awaited_once_with("ide__read_file", {"path": "a"}, caller=CODEAGENT)
    chat.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_outil_ide_sans_registre_courant_reste_sur_le_registre_lie():
    chat = _faux_registre("ide__read_file")
    shim = LumenaToolSystem()
    shim.bind_tool_registry(chat)
    await shim.execute_tool(ToolCall(name="ide__read_file", arguments={"path": "a"}), caller=CODEAGENT)
    chat.execute.assert_awaited_once_with("ide__read_file", {"path": "a"}, caller=CODEAGENT)


@pytest.mark.asyncio
async def test_execute_tool_by_name_transmet_l_appelant():
    chat = _faux_registre("get_time")
    shim = LumenaToolSystem()
    shim.bind_tool_registry(chat)
    await shim.execute_tool_by_name("get_time", {}, caller=CODEAGENT)
    chat.execute.assert_awaited_once_with("get_time", {}, caller=CODEAGENT)


# ══════════════════════════════════════════════════════════════════════════
#  3. Chaine reelle : CodeAgent delegue par un worker de mission
# ══════════════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
@pytest.mark.parametrize("appelant,admis", [
    (CallerContext(kind="codeagent", agent_id="code_agent"), True),
    (None, False),
])
async def test_codeagent_delegue_emprunte_le_rail_ide_de_la_mission(registry, service, monkeypatch, appelant, admis):
    orch, root = _mission(registry)
    orch.set_task_metadata("task_worker", requester=dict(PROPRIETAIRE))
    _ide_ouverte_sur(service, root)
    send = _envoi(service, content="x")
    chat = _faux_registre("ide__read_file")
    shim = LumenaToolSystem()
    shim.bind_tool_registry(chat)
    original = registry._execute_inner

    async def inner(name, args, *, caller=None):
        if name == "delegate_task":
            # Ce que fait CodeAgent pendant `delegate_task` : le singleton, pas le registre.
            resultat = await shim.execute_tool(
                ToolCall(name="ide__read_file", arguments={"path": "README.md"}), caller=appelant)
            return Observation(content=resultat.output or resultat.error or "", success=resultat.success)
        return await original(name, args, caller=caller)

    monkeypatch.setattr(registry, "_execute_inner", inner)
    with external_tool_run(), mission_runtime_scope(orch, "task_worker"):
        observation = await registry.execute("delegate_task", {"description": "lis README"}, caller=REACT)
    chat.execute.assert_not_awaited()
    if admis:
        assert observation.success is True, observation.content
        send.assert_awaited_once()
    else:
        assert "ide_mission_caller_unknown" in observation.content
        send.assert_not_awaited()


@pytest.mark.asyncio
async def test_codeagent_transmet_son_identite(monkeypatch):
    from src.agents.sub_agent import CodeAgent

    faux = MagicMock()
    faux.execute_tool_by_name = AsyncMock(return_value="contenu")
    monkeypatch.setattr("src.tools.tool_system.get_tool_system", lambda: faux)
    agent = CodeAgent()
    await agent._call_tool("read_file", {"file_path": "a.txt"})
    appelant = faux.execute_tool_by_name.await_args.kwargs["caller"]
    assert appelant.kind == "codeagent"
