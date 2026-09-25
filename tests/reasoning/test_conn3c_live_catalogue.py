"""Use the real registry, ToolSystem and Codex bridge, not a substitute catalogue."""
from dataclasses import replace
import json
from unittest.mock import AsyncMock, Mock

import pytest

from src.llm.codex_mcp_bridge import INVOKE_TOOL_NAME, LumenaCodexToolBridge
from src.llm.output_normalizer import auto_fix_action_name, normalize_action_name
from src.reasoning.external_tool_registry import ExternalToolError
from src.reasoning.external_tool_scope import external_tool_run
from src.reasoning.tool_registry import ToolRegistry
from src.runtime.context import RuntimeContext, pop_runtime_context, push_runtime_context
from src.telemetry.trace_bus import TraceBus
from src.tools.tool_system import LumenaToolSystem, ToolCall
from src.utils.external_tool_names import is_ide_tool_name
from tests.tools.test_conn3c_ide_capabilities import service as service


@pytest.fixture
def registry(service):
    registry = ToolRegistry(lumena_root=service.discovery.lumena_root)
    registry._ide_tools._service = service
    return registry


@pytest.fixture
def owner():
    ctx = RuntimeContext(channel="ide", client="test", request_id="request", conversation_id="conversation",
                         message_id="message", user_role="owner")
    token = push_runtime_context(ctx)
    try:
        yield ctx
    finally:
        pop_runtime_context(token)


def ide_names(items):
    return {name for name in items if is_ide_tool_name(name)}


def all_surfaces(registry):
    shim = LumenaToolSystem()
    shim.bind_tool_registry(registry)
    schemas = registry.get_tools_schema()
    names = {item["function"]["name"] for item in schemas}
    bridge = LumenaCodexToolBridge(registry, allowed_tools=names, agent_id="test")
    return [
        ide_names(registry.tools), ide_names(registry._tool_modules), ide_names(names),
        ide_names(item["name"] for item in bridge.tools()),
        ide_names(item["function"]["name"] for item in shim.get_tools_for_provider("openai")),
        ide_names(item["name"] for item in shim.get_tools_for_provider("anthropic")),
        ide_names(item["name"] for item in shim.get_tools_for_provider("google")),
        ide_names(item["name"] for item in shim.get_tools_for_provider("ollama")),
        ide_names(name for name, _, _ in shim._iter_all_tools()),
    ]


@pytest.mark.parametrize("state", ["absent", "closed", "ready"])
def test_all_real_model_surfaces_agree_on_availability_and_never_expose_old_facades(registry, service, state):
    if state == "absent":
        service.discovery.discover().installation.executable.unlink()
    if state == "closed":
        service.bridge._connected = False
    expected = {tool.name for tool in service.capture().tools}
    with external_tool_run():
        surfaces = all_surfaces(registry)
        assert all(names == expected for names in surfaces)
        text = registry.get_tools_description()
        assert all(f"- {name}(" in text for name in expected)
        assert "- ide_status(" not in text
        assert "- ide_write_file(" not in text
    # CONN-5C-2 (15/09/2026) : `ide__command_run` expose au modele (contextual) -> 110 outils.
    assert len(expected) == {"absent": 0, "closed": 1, "ready": 110}[state]


def test_exact_schemas_survive_api_local_and_codex_conversion(registry):
    shim = LumenaToolSystem()
    shim.bind_tool_registry(registry)
    with external_tool_run():
        canonical = {x["function"]["name"]: x["function"]["parameters"]
                     for x in registry.get_external_tool_catalog().schemas()}
        api = {x["function"]["name"]: x["function"]["parameters"] for x in shim.get_tools_for_provider("openai")}
        anthropic = {x["name"]: x["input_schema"] for x in shim.get_tools_for_provider("anthropic")}
        local = {x["name"]: x["parameters"] for x in shim.get_tools_for_provider("ollama")}
        google = {x["name"]: x["parametersJsonSchema"] for x in shim.get_tools_for_provider("google")
                  if is_ide_tool_name(x["name"])}
        bridge = LumenaCodexToolBridge(registry, allowed_tools=canonical, agent_id="test")
        codex = {x["name"]: x["inputSchema"] for x in bridge.tools()}
        for name, schema in canonical.items():
            assert schema["additionalProperties"] is False
            assert api[name] == anthropic[name] == local[name] == codex[name] == google[name] == schema
        api["ide__get_status"]["properties"]["injected"] = {"type": "string"}
        assert "injected" not in registry.get_external_tool_catalog().resolve("ide__get_status")[1].schema_json
        encoded = json.dumps(canonical["ide__git_sync"], ensure_ascii=False, sort_keys=True)
        assert encoded in shim.get_tools_prompt_section()


def test_snapshot_stays_fixed_through_disconnect_reconnect_removal_and_refreshes_next_run(registry, service):
    with external_tool_run():
        before = registry.get_tools_schema()
        before_text = registry.get_tools_description()
        service.bridge._connected = False
        assert registry.get_tools_schema() == before
        assert registry.get_tools_description() == before_text
    with external_tool_run():
        assert ide_names(registry.tools) == {"ide_launch"}
        service.bridge._connected = True
        assert ide_names(registry.tools) == {"ide_launch"}
    with external_tool_run():
        assert "ide__get_status" in registry.tools
        service.discovery.discover().installation.executable.unlink()
        assert "ide__get_status" in registry.tools
    with external_tool_run():
        assert not ide_names(registry.tools)
        assert "- ide__get_status(" not in registry.get_tools_description()


def test_capture_error_fails_closed_instead_of_falling_back_to_legacy(registry, service, monkeypatch):
    monkeypatch.setattr(service, "capture", Mock(side_effect=RuntimeError("secret")))
    with external_tool_run():
        assert not ide_names(registry.tools)
        assert registry.get_external_tool_catalog().providers[0].state == "degraded"
    with pytest.raises(ExternalToolError):
        registry.tools["ide_status"] = {"handler": AsyncMock()}


@pytest.mark.asyncio
async def test_old_cached_discovery_cannot_resurrect_facades_or_absent_tools(registry, service):
    registry._tool_collection = Mock()
    registry._tool_collection.query.return_value = {
        "documents": [["old facade", "old command", "native read"]],
        "metadatas": [[{"name": "ide_status"}, {"name": "ide__get_status"}, {"name": "read_file"}]],
    }
    service.discovery.discover().installation.executable.unlink()
    with external_tool_run():
        result = await registry.tools["discover_tools"]["handler"](query="IDE")
    assert "ide_" not in result
    assert "read_file" in result


@pytest.mark.asyncio
async def test_live_external_discovery_does_not_rebuild_or_freeze_the_native_index(registry):
    registry._tool_collection = Mock()
    registry._tool_collection.query.return_value = {"documents": [[]], "metadatas": [[]]}
    with external_tool_run():
        result = await registry.tools["discover_tools"]["handler"](query="get_status")
    assert "ide__get_status" in result
    registry._tool_collection.query.assert_called_once()


@pytest.mark.asyncio
async def test_real_dispatch_pins_snapshot_and_returns_explicit_result(registry, service, owner, monkeypatch):
    send = AsyncMock(return_value={"success": True, "connected": True})
    monkeypatch.setattr(service.bridge, "send_command", send)
    with external_tool_run():
        expected = registry.get_external_tool_catalog().providers[0].binding.session
        result = await registry.execute("ide__get_status", {})
        assert result.success
        assert json.loads(result.content) == {"success": True, "connected": True}
        send.assert_awaited_once_with("get_status", {}, expected_snapshot=expected)


@pytest.mark.asyncio
@pytest.mark.parametrize("name,args,error", [
    ("ide_status", {}, "not_in_snapshot"),
    ("ide__get_statu", {}, "not_in_snapshot"),
    ("IDE__GET_STATUS", {}, "not_in_snapshot"),
    ("ide__get_status", {"unknown": "secret"}, "parameters_invalid"),
    ("ide__get_status", {"input": {}}, "parameters_invalid"),
    ("ide__get_status", None, "parameters_invalid"),
    ("ide__write_file", {"path": "x.py", "content": "secret"}, "authorization_not_connected"),
    ("ide__git_sync", {"operation": "push", "confirmed": True}, "authorization_not_connected"),
])
async def test_strict_dispatch_never_uses_fuzzy_coercion_or_unguarded_legacy_handlers(
    registry, service, owner, monkeypatch, name, args, error,
):
    send = AsyncMock()
    monkeypatch.setattr(service.bridge, "send_command", send)
    monkeypatch.setattr("src.llm.output_normalizer.auto_fix_action_name", Mock(side_effect=AssertionError("fuzzy")))
    with external_tool_run():
        result = await registry.execute(name, args)
    assert not result.success and error in result.content
    assert "secret" not in result.content
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("restriction", ["guest", "missing", "mission", "hard_filter", "stale"])
async def test_calls_recheck_identity_scope_and_liveness(registry, service, owner, monkeypatch, restriction):
    send = AsyncMock()
    monkeypatch.setattr(service.bridge, "send_command", send)
    if restriction == "guest":
        ctx = replace(owner, user_role="guest")
    elif restriction == "missing":
        ctx = None
    elif restriction == "mission":
        ctx = replace(owner, task_id="mission")
    else:
        ctx = owner
    token = push_runtime_context(ctx)
    try:
        with external_tool_run():
            registry.get_external_tool_catalog()
            if restriction == "stale":
                service.bridge._connected = False
            if restriction == "hard_filter":
                registry._allowed_tools_hard = True
                registry._allowed_tools = {"read_file"}
            assert not (await registry.execute("ide__get_status", {})).success
    finally:
        pop_runtime_context(token)
    send.assert_not_awaited()


@pytest.mark.asyncio
async def test_codex_pins_catalogue_and_generic_invoker_cannot_revive_stale_tools(
    registry, service, owner, monkeypatch,
):
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(service.bridge, "send_command", send)
    bridge = LumenaCodexToolBridge(registry, allowed_tools={"ide__get_status"}, agent_id="test")
    await bridge.start()
    try:
        declared = bridge.tools()
        service.bridge._connected = False
        assert bridge.tools() == declared
        result = await bridge._dispatch({"op": "call", "name": INVOKE_TOOL_NAME,
                                         "arguments": {"name": "ide__get_status", "arguments": {}}})
        assert result["ok"]
        assert result["result"]["isError"]
        assert "stale" in str(result)
        send.assert_not_awaited()
    finally:
        await bridge.stop()


@pytest.mark.parametrize("name", ["ide_status", "ide__get_statu", "IDE__get_status", "ide__get_status_tool"])
def test_normalizer_never_rewrites_ide_names(name):
    assert normalize_action_name(name) == name
    assert auto_fix_action_name(name, {"ide__get_status", "ide_status", "read_file"}) == name


def test_fuzzy_cannot_enter_ide_namespace_but_native_fuzzy_still_works():
    assert auto_fix_action_name("id_status", {"ide_status"}) == "id_status"
    assert auto_fix_action_name("read_fle", {"read_file", "ide_read_file"}) == "read_file"


@pytest.mark.asyncio
async def test_tool_system_text_parser_and_execution_use_same_registry(registry, service, owner, monkeypatch):
    shim = LumenaToolSystem()
    shim.bind_tool_registry(registry)
    send = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(service.bridge, "send_command", send)
    with external_tool_run():
        parsed = shim.parse_tool_calls_from_text('[TOOL:ide__get_status] {}')
        assert len(parsed) == 1
        assert (await shim.execute_tool(parsed[0])).success
    service.discovery.discover().installation.executable.unlink()
    with external_tool_run():
        assert not shim.parse_tool_calls_from_text('[TOOL:ide__get_status] {}')
        assert not (await shim.execute_tool(ToolCall(name="ide__get_status", arguments={}))).success
    assert send.await_count == 1


def test_trace_redaction_precedes_buffering_without_changing_native_events():
    bus = TraceBus()
    source = {"tool_name": "ide__write_file", "summary": "private-path", "error": "secret-content",
              "thought": "private-query", "stage": "tool_call"}
    event = bus.publish(source)
    assert "private" not in str(event) and "secret" not in str(event)
    assert source["summary"] == "private-path"
    native = bus.publish({**source, "tool_name": "read_file"})
    assert native["summary"] == "private-path"
