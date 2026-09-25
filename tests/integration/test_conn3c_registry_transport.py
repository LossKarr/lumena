"""Registry -> authenticated socket, across an actual foreign agent event loop."""
import asyncio
import json
import threading
from unittest.mock import Mock

import pytest

from src.llm.codex_mcp_bridge import LumenaCodexToolBridge
from src.reasoning.external_tool_scope import external_tool_run
from src.reasoning.react_config import Observation
from src.reasoning.react import ReActLoop
from src.llm.execution_router import _record_tool_observation
from src.reasoning.tool_registry import ToolRegistry
from src.runtime.context import RuntimeContext, pop_runtime_context, push_runtime_context
from tests.tools.test_conn2a_ide_bridge_auth import result_for
from tests.tools.test_conn2c_ide_transport import connection as connection
from tests.tools.test_conn3c_ide_capabilities import service as service


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["registry", "codex", "codex_recording"])
async def test_real_registry_dispatch_from_agent_thread_preserves_binding(connection, service, consumer):
    bridge, ws = connection
    service.bridge = bridge
    registry = ToolRegistry(lumena_root=service.discovery.lumena_root)
    registry._ide_tools._service = service
    ctx = RuntimeContext(channel="ide", client="canary", request_id="request", conversation_id="conversation",
                         message_id="message", user_role="owner")
    owner_thread = threading.get_ident()
    token = push_runtime_context(ctx)
    observed = []
    loop = ReActLoop(llm_chat_func=None, tools=registry)

    def after_call(name, args, result, duration):
        observed.append(result)
        if consumer == "codex_recording":
            _record_tool_observation(loop, name, args, result, duration)

    codex = LumenaCodexToolBridge(registry, allowed_tools={"ide__get_status"}, agent_id="canary",
                                after_call=after_call)

    async def agent():
        assert threading.get_ident() != owner_thread
        if consumer == "registry":
            return await registry.execute("ide__get_status", {})
        return await codex._dispatch({"op": "call", "name": "ide__get_status", "arguments": {}})

    caller = None
    try:
        with external_tool_run():
            catalog = registry.get_external_tool_catalog()
            await codex.start()
            caller = asyncio.create_task(asyncio.to_thread(asyncio.run, agent()))
            command = json.loads(await asyncio.wait_for(ws.recv(), 3))
            assert command["action"] == "get_status" and command["params"] == {}
            assert command["catalogue_revision"] == catalog.providers[0].binding.session.catalogue_hash
            await ws.send(json.dumps(result_for(command, success=True, connected=True, execution={
                "schema_version": 1, "status": "succeeded", "started_at": "2026-09-13T12:00:00Z",
                "completed_at": "2026-09-13T12:00:01Z",
            })))
            result = await asyncio.wait_for(caller, 3)
            if consumer == "registry":
                assert result.success and json.loads(result.content)["connected"] is True
                observed.append(result)
            else:
                assert result["ok"] and not result["result"]["isError"]
            assert len(observed) == 1
            assert observed[0].execution.operation_id == command["operation_id"]
            assert observed[0].execution.status.value == "succeeded"
            assert observed[0].execution.effect.value == "READ_ONLY"
            if consumer == "codex_recording":
                assert loop.execution_ledger.size == 1
                assert loop.execution_ledger.recent(1)[0].execution is observed[0].execution
                assert not loop.execution_ledger.has_any_mutation()
            assert bridge._pending == {}
    finally:
        if caller is not None and not caller.done():
            caller.cancel()
            await asyncio.gather(caller, return_exceptions=True)
        await codex.stop()
        pop_runtime_context(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("guard", [
    "_policy_check", "_skill_edit_guard", "_category_contract_check",
    "_peer_raw_network_refusal", "_ionos_db_context_refusal", "_mcp_policy_check",
])
async def test_ide_dispatch_still_passes_each_existing_preflight(service, monkeypatch, guard):
    registry = ToolRegistry(lumena_root=service.discovery.lumena_root)
    registry._ide_tools._service = service
    refusal = Observation(content="preflight refusal", success=False)
    checked = Mock(return_value=refusal)
    monkeypatch.setattr(registry, guard, checked)
    monkeypatch.setattr(registry._ide_tools, "execute", Mock(side_effect=AssertionError("bypass")))
    assert await registry.execute("ide__get_status", {}) is refusal
    checked.assert_called_once()
