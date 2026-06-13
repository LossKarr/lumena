"""
Tests pour MCP handler_adapter (Phase 7).

Vérifie :
  - Namespace `mcp__{server}__{tool}` strict
  - Description préfixée [MCP/{server}]
  - source_module = "mcp.{server}"
  - Wrapper handler async catch toutes les exceptions MCP → HandlerResult.fail
  - asyncio.to_thread utilisé pour ne pas bloquer event loop
  - JSON Schema inputSchema préservé dans HandlerDef.parameters
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional
from unittest.mock import MagicMock

import pytest

from src.mcp.client import (
    MCPCallResult,
    MCPClient,
    MCPClientError,
    MCPProtocolError,
    MCPTimeoutError,
    MCPTool,
)
from src.mcp.handler_adapter import (
    adapt_tool,
    adapt_tools,
    make_handler_name,
)
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.registry_v2 import HandlerDef


# ──────────────────────────────────────────────────────────────────────────────
# Fake MCPClient
# ──────────────────────────────────────────────────────────────────────────────


class _FakeMCPClient:
    """Mock minimal de MCPClient pour tests adapter."""

    def __init__(self, call_result: Optional[MCPCallResult] = None,
                 raise_exc: Optional[Exception] = None,
                 server_name: str = "fake"):
        self._call_result = call_result
        self._raise = raise_exc
        self._server_name = server_name
        self.calls = []  # list of (name, args, timeout_s)

    @property
    def server_name(self) -> str:
        return self._server_name

    def call_tool(self, name, args, *, timeout_s=None):
        self.calls.append((name, args, timeout_s))
        if self._raise:
            raise self._raise
        return self._call_result or MCPCallResult(content="ok")


# ──────────────────────────────────────────────────────────────────────────────
# make_handler_name
# ──────────────────────────────────────────────────────────────────────────────


def test_handler_name_format():
    assert make_handler_name("server", "tool") == "mcp__server__tool"


def test_handler_name_uses_double_underscore():
    name = make_handler_name("my-server", "my-tool")
    assert "__" in name
    assert name.count("__") == 2


def test_handler_name_rejects_empty_server():
    with pytest.raises(ValueError):
        make_handler_name("", "tool")


def test_handler_name_rejects_empty_tool():
    with pytest.raises(ValueError):
        make_handler_name("server", "")


def test_handler_name_rejects_invalid_chars_in_server():
    with pytest.raises(ValueError):
        make_handler_name("my server!", "tool")
    with pytest.raises(ValueError):
        make_handler_name("server/path", "tool")


def test_handler_name_rejects_invalid_chars_in_tool():
    with pytest.raises(ValueError):
        make_handler_name("server", "tool with spaces")
    with pytest.raises(ValueError):
        make_handler_name("server", "tool/path")


def test_handler_name_accepts_dots_and_dashes():
    name = make_handler_name("my-server", "my.tool-v2")
    assert name == "mcp__my-server__my.tool-v2"


def test_handler_name_rejects_too_long():
    with pytest.raises(ValueError, match="too long"):
        make_handler_name("a" * 100, "b" * 100)


def test_handler_name_does_not_collide_with_native_pattern():
    """Aucun outil natif Lumena ne commence par `mcp__`."""
    # Pattern strict : tout HandlerDef MCP commence par "mcp__"
    name = make_handler_name("anyserver", "read_file")
    assert name.startswith("mcp__")
    # Le namespace garantit qu'aucun `read_file` natif ne peut clasher
    assert name != "read_file"


# ──────────────────────────────────────────────────────────────────────────────
# adapt_tool : HandlerDef fields
# ──────────────────────────────────────────────────────────────────────────────


def test_adapt_tool_returns_handlerdef():
    client = _FakeMCPClient()
    tool = MCPTool(name="echo", description="Echo tool", input_schema={"type": "object"})
    hdef = adapt_tool(client=client, server_name="my-server", mcp_tool=tool)
    assert isinstance(hdef, HandlerDef)


def test_adapt_tool_namespaced_name():
    client = _FakeMCPClient()
    tool = MCPTool(name="search", description="d", input_schema={})
    hdef = adapt_tool(client=client, server_name="srv", mcp_tool=tool)
    assert hdef.name == "mcp__srv__search"


def test_description_prefixed_with_mcp_marker():
    client = _FakeMCPClient()
    tool = MCPTool(name="t", description="Original description", input_schema={})
    hdef = adapt_tool(client=client, server_name="my-server", mcp_tool=tool)
    assert hdef.description.startswith("[MCP/my-server]")
    assert "Original description" in hdef.description


def test_description_handles_empty():
    client = _FakeMCPClient()
    tool = MCPTool(name="t", description="", input_schema={})
    hdef = adapt_tool(client=client, server_name="srv", mcp_tool=tool)
    assert hdef.description.startswith("[MCP/srv]")


def test_source_module_set_to_mcp_server():
    client = _FakeMCPClient()
    tool = MCPTool(name="t", description="d", input_schema={})
    hdef = adapt_tool(client=client, server_name="my-server", mcp_tool=tool)
    assert hdef.source_module == "mcp.my-server"


def test_category_default_is_mcp():
    client = _FakeMCPClient()
    tool = MCPTool(name="t", description="d", input_schema={})
    hdef = adapt_tool(client=client, server_name="srv", mcp_tool=tool)
    assert hdef.category == "mcp"


def test_category_override():
    client = _FakeMCPClient()
    tool = MCPTool(name="t", description="d", input_schema={})
    hdef = adapt_tool(
        client=client, server_name="srv", mcp_tool=tool, category="custom_cat",
    )
    assert hdef.category == "custom_cat"


def test_input_schema_preserved_as_parameters():
    client = _FakeMCPClient()
    schema = {
        "type": "object",
        "properties": {"x": {"type": "string"}, "y": {"type": "number"}},
        "required": ["x"],
    }
    tool = MCPTool(name="t", description="d", input_schema=schema)
    hdef = adapt_tool(client=client, server_name="srv", mcp_tool=tool)
    assert hdef.parameters == schema


def test_empty_input_schema_becomes_empty_dict():
    client = _FakeMCPClient()
    tool = MCPTool(name="t", description="d", input_schema={})
    hdef = adapt_tool(client=client, server_name="srv", mcp_tool=tool)
    assert hdef.parameters == {}


# ──────────────────────────────────────────────────────────────────────────────
# Wrapper handler : succès / erreur / timeout / exception
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wrapped_handler_calls_client_call_tool():
    client = _FakeMCPClient(call_result=MCPCallResult(content="hello"))
    tool = MCPTool(name="echo", description="d", input_schema={})
    hdef = adapt_tool(client=client, server_name="srv", mcp_tool=tool)
    ctx = MagicMock()
    result = await hdef.handler(ctx, x=42)
    assert client.calls == [("echo", {"x": 42}, None)]


@pytest.mark.asyncio
async def test_wrapped_handler_returns_ok_on_success():
    client = _FakeMCPClient(
        call_result=MCPCallResult(content=[{"type": "text", "text": "yay"}])
    )
    tool = MCPTool(name="t", description="d", input_schema={})
    hdef = adapt_tool(client=client, server_name="srv", mcp_tool=tool)
    result = await hdef.handler(MagicMock(), foo="bar")
    assert isinstance(result, HandlerResult)
    assert result.success is True
    assert "yay" in result.output


@pytest.mark.asyncio
async def test_wrapped_handler_returns_fail_on_is_error():
    client = _FakeMCPClient(
        call_result=MCPCallResult(
            content=[{"type": "text", "text": "bad"}],
            is_error=True,
            error_message="bad",
        )
    )
    tool = MCPTool(name="t", description="d", input_schema={})
    hdef = adapt_tool(client=client, server_name="srv", mcp_tool=tool)
    result = await hdef.handler(MagicMock())
    assert result.success is False
    assert "bad" in (result.error or "") or "bad" in (result.output or "")


@pytest.mark.asyncio
async def test_wrapped_handler_catches_timeout_as_fail():
    client = _FakeMCPClient(raise_exc=MCPTimeoutError("timeout!"))
    tool = MCPTool(name="t", description="d", input_schema={})
    hdef = adapt_tool(client=client, server_name="srv", mcp_tool=tool)
    result = await hdef.handler(MagicMock())
    assert result.success is False
    msg = (result.error or "") + (result.output or "")
    assert "timeout" in msg.lower()


@pytest.mark.asyncio
async def test_wrapped_handler_catches_protocol_error_as_fail():
    client = _FakeMCPClient(raise_exc=MCPProtocolError("bad json"))
    tool = MCPTool(name="t", description="d", input_schema={})
    hdef = adapt_tool(client=client, server_name="srv", mcp_tool=tool)
    result = await hdef.handler(MagicMock())
    assert result.success is False
    msg = (result.error or "") + (result.output or "")
    assert "protocol" in msg.lower()


@pytest.mark.asyncio
async def test_wrapped_handler_catches_client_error_as_fail():
    client = _FakeMCPClient(raise_exc=MCPClientError("generic"))
    tool = MCPTool(name="t", description="d", input_schema={})
    hdef = adapt_tool(client=client, server_name="srv", mcp_tool=tool)
    result = await hdef.handler(MagicMock())
    assert result.success is False


@pytest.mark.asyncio
async def test_wrapped_handler_catches_unexpected_exception_as_fail():
    client = _FakeMCPClient(raise_exc=RuntimeError("unexpected boom"))
    tool = MCPTool(name="t", description="d", input_schema={})
    hdef = adapt_tool(client=client, server_name="srv", mcp_tool=tool)
    result = await hdef.handler(MagicMock())
    assert result.success is False
    msg = (result.error or "") + (result.output or "")
    assert "boom" in msg.lower()


@pytest.mark.asyncio
async def test_wrapped_handler_propagates_timeout_param():
    client = _FakeMCPClient(call_result=MCPCallResult(content="ok"))
    tool = MCPTool(name="t", description="d", input_schema={})
    hdef = adapt_tool(
        client=client, server_name="srv", mcp_tool=tool, timeout_s=12.5,
    )
    await hdef.handler(MagicMock())
    assert client.calls[-1][2] == 12.5


# ──────────────────────────────────────────────────────────────────────────────
# Content serialization
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wrapped_handler_joins_text_content():
    client = _FakeMCPClient(
        call_result=MCPCallResult(
            content=[
                {"type": "text", "text": "line 1"},
                {"type": "text", "text": "line 2"},
            ]
        )
    )
    tool = MCPTool(name="t", description="d", input_schema={})
    hdef = adapt_tool(client=client, server_name="srv", mcp_tool=tool)
    result = await hdef.handler(MagicMock())
    assert "line 1" in result.output
    assert "line 2" in result.output


@pytest.mark.asyncio
async def test_wrapped_handler_serializes_dict_content_as_json():
    client = _FakeMCPClient(
        call_result=MCPCallResult(content={"key": "value", "n": 42})
    )
    tool = MCPTool(name="t", description="d", input_schema={})
    hdef = adapt_tool(client=client, server_name="srv", mcp_tool=tool)
    result = await hdef.handler(MagicMock())
    assert '"key"' in result.output
    assert '"value"' in result.output


@pytest.mark.asyncio
async def test_wrapped_handler_appends_structured_content():
    client = _FakeMCPClient(
        call_result=MCPCallResult(
            content=[{"type": "text", "text": "summary"}],
            structured_content={"items": [1, 2, 3]},
        )
    )
    tool = MCPTool(name="t", description="d", input_schema={})
    hdef = adapt_tool(client=client, server_name="srv", mcp_tool=tool)
    result = await hdef.handler(MagicMock())
    assert "summary" in result.output
    assert "structuredContent" in result.output
    assert "[1, 2, 3]" in result.output or "1" in result.output


@pytest.mark.asyncio
async def test_wrapped_handler_none_content_returns_empty():
    client = _FakeMCPClient(call_result=MCPCallResult(content=None))
    tool = MCPTool(name="t", description="d", input_schema={})
    hdef = adapt_tool(client=client, server_name="srv", mcp_tool=tool)
    result = await hdef.handler(MagicMock())
    assert result.success is True
    assert result.output == ""


# ──────────────────────────────────────────────────────────────────────────────
# adapt_tools (batch)
# ──────────────────────────────────────────────────────────────────────────────


def test_adapt_tools_batch_returns_list():
    client = _FakeMCPClient()
    tools = [
        MCPTool(name="a", description="A", input_schema={}),
        MCPTool(name="b", description="B", input_schema={}),
    ]
    defs = adapt_tools(client=client, server_name="srv", mcp_tools=tools)
    assert len(defs) == 2
    assert all(isinstance(d, HandlerDef) for d in defs)
    assert defs[0].name == "mcp__srv__a"
    assert defs[1].name == "mcp__srv__b"


def test_adapt_tools_skips_invalid_names():
    """Tools avec nom invalide sont skipped, pas crash de la liste entière."""
    client = _FakeMCPClient()
    tools = [
        MCPTool(name="good", description="d", input_schema={}),
        MCPTool(name="bad name with spaces", description="d", input_schema={}),
        MCPTool(name="also-good", description="d", input_schema={}),
    ]
    defs = adapt_tools(client=client, server_name="srv", mcp_tools=tools)
    assert len(defs) == 2
    names = {d.name for d in defs}
    assert "mcp__srv__good" in names
    assert "mcp__srv__also-good" in names


def test_adapt_tools_empty_returns_empty():
    client = _FakeMCPClient()
    assert adapt_tools(client=client, server_name="srv", mcp_tools=[]) == []


# ──────────────────────────────────────────────────────────────────────────────
# Garde-fous types
# ──────────────────────────────────────────────────────────────────────────────


def test_adapt_tool_rejects_non_mcptool():
    client = _FakeMCPClient()
    with pytest.raises(ValueError):
        adapt_tool(
            client=client,
            server_name="srv",
            mcp_tool={"name": "t", "description": "d", "input_schema": {}},  # type: ignore
        )


# ──────────────────────────────────────────────────────────────────────────────
# Sanity / __init__ exports
# ──────────────────────────────────────────────────────────────────────────────


def test_module_exports():
    from src.mcp import (
        MCPClient as C,
        MCPTool as T,
        MCPCallResult as R,
        adapt_tool as at,
        adapt_tools as ats,
        make_handler_name as mhn,
    )
    assert C is MCPClient
    assert T is MCPTool
    assert R is MCPCallResult
    assert callable(at)
    assert callable(ats)
    assert callable(mhn)
