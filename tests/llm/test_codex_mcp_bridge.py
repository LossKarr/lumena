from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from src.llm import codex_mcp_bridge as bridge_module
from src.llm.codex_mcp_bridge import (
    MAX_BRIDGE_MESSAGE_BYTES,
    LumenaCodexToolBridge,
    _mcp_result,
    _mcp_safe_description,
    _mcp_safe_schema_node,
    _mcp_tools_from_legacy_schemas,
)
from src.reasoning.react_config import Observation
from src.runtime.context import RuntimeContext, get_current_runtime_context, pop_runtime_context, push_runtime_context


class FakeRegistry:
    def __init__(self):
        self.calls = []
        self.runtime_seen = None

    def get_tools_schema(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read one file",
                    "parameters": {
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "write_file",
                    "description": "Write one file",
                    "parameters": {"type": "object", "properties": {}},
                },
            },
        ]

    async def execute(self, name, args, *, caller=None):
        self.runtime_seen = get_current_runtime_context()
        self.calls.append((name, args, caller))
        return Observation(content=f"observed:{name}:{args.get('path', '')}", success=True)


async def _bridge_rpc(endpoint, payload):
    reader, writer = await asyncio.open_connection(endpoint.host, endpoint.port)
    writer.write((json.dumps({"token": endpoint.token, **payload}) + "\n").encode())
    await writer.drain()
    response = json.loads((await reader.readline()).decode())
    writer.close()
    await writer.wait_closed()
    return response


def test_legacy_schemas_are_filtered_and_converted_to_mcp():
    tools = _mcp_tools_from_legacy_schemas(
        FakeRegistry().get_tools_schema(), {"read_file"}
    )
    assert [tool["name"] for tool in tools] == ["read_file"]
    assert tools[0]["inputSchema"]["required"] == ["path"]


def test_mcp_boundary_normalizes_legacy_shorthand_required_and_defaults():
    schema = _mcp_safe_schema_node(
        {
            "type": "object",
            "properties": {
                "text": "Texte libre avec {JSON}, </html> et # marqueur",
                "name": {"type": "string", "required": True, "default": "x"},
                "mode": {
                    "type": "string",
                    "enum": ["one", "two"],
                    "default": "one",
                },
            },
            "required": ["text"],
        },
        root=True,
    )

    assert schema["properties"]["text"]["type"] == "string"
    assert "JSON" in schema["properties"]["text"]["description"]
    assert "{" not in schema["properties"]["text"]["description"]
    assert schema["required"] == ["text", "name"]
    assert schema["properties"]["mode"]["enum"] == ["one", "two"]
    assert "default" not in json.dumps(schema)


def test_problematic_tool_descriptions_become_bounded_plain_text():
    source = (
        "Insère autour de </main>, # END IMPORTS, } // class et /* END */. "
        'Arguments: {"path":"x", "content":"y"}.'
    )
    safe = _mcp_safe_description(source, limit=120)
    assert safe
    assert len(safe) <= 120
    assert safe.isascii()
    assert not any(marker in safe for marker in ("</", "#", "/*", "}", "{"))


def test_known_real_legacy_shapes_are_codex_safe():
    schemas = [
        {
            "type": "function",
            "function": {
                "name": "multi_edit_file",
                "description": (
                    "Editions multiples: file_path (ou file/path), old_content "
                    "(ou old), new_content (ou new)."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "edits": (
                            'Liste [{"file_path":"x","old_content":"a",'
                            '"new_content":"b"}]'
                        )
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "twitter_post_tweet",
                "description": "Publie un tweet",
                "parameters": {
                    "type": "object",
                    "properties": {"text": "Texte du tweet (max 280 caractères)"},
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "stripe_create_product",
                "description": "Crée un produit",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "required": True}
                    },
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "create_invoice_pdf",
                "description": "Crée une facture PDF",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "currency": {"type": "string", "default": "€"}
                    },
                },
            },
        },
    ]
    tools = _mcp_tools_from_legacy_schemas(
        schemas, {item["function"]["name"] for item in schemas}
    )
    rendered = json.dumps(tools, ensure_ascii=False)

    assert len(tools) == 4
    assert tools[0]["inputSchema"]["properties"]["edits"]["type"] == "string"
    assert tools[1]["inputSchema"]["properties"]["text"]["type"] == "string"
    assert tools[2]["inputSchema"]["required"] == ["name"]
    assert "default" not in rendered
    assert len(rendered.encode("utf-8")) < MAX_BRIDGE_MESSAGE_BYTES


@pytest.mark.asyncio
async def test_loopback_bridge_auth_scope_context_and_react_caller():
    registry = FakeRegistry()
    runtime = RuntimeContext.build(
        channel="web",
        client="desktop",
        request_id="req-1",
        conversation_id="conv-1",
        message_id="msg-1",
        workspace_policy="default",
        task_id="task-1",
        client_caps={},
        workspace_path=None,
        active_file_path=None,
        open_files=[],
        resolved_workspace=None,
        resolved_date=None,
        resolution_reason=None,
        user_id="owner-1",
        user_role="owner",
        mode="agent",
    )
    token = push_runtime_context(runtime)
    observations = []
    try:
        async with LumenaCodexToolBridge(
            registry,
            allowed_tools={"read_file"},
            agent_id="codex-mission",
            after_call=lambda *args: observations.append(args),
        ) as bridge:
            endpoint = bridge.endpoint
            listed = await _bridge_rpc(endpoint, {"op": "list"})
            # LOT Z34 — le pont declare le perimetre du run PLUS l'invocateur, et
            # RIEN d'autre : `invoke_tool` ouvre une porte, il n'abat pas le mur
            # (l'appel direct hors perimetre reste refuse, cf. test_z34_*).
            assert [tool["name"] for tool in listed["tools"]] == [
                "read_file", "invoke_tool",
            ]
            called = await _bridge_rpc(
                endpoint,
                {"op": "call", "name": "read_file", "arguments": {"path": "x.txt"}},
            )
            assert called["result"]["isError"] is False
            denied = await _bridge_rpc(
                endpoint,
                {"op": "call", "name": "write_file", "arguments": {}},
            )
            assert denied["ok"] is False
            assert "outside this run scope" in denied["error"]

            reader, writer = await asyncio.open_connection(endpoint.host, endpoint.port)
            writer.write((json.dumps({"token": "wrong", "op": "list"}) + "\n").encode())
            await writer.drain()
            rejected = json.loads((await reader.readline()).decode())
            writer.close()
            await writer.wait_closed()
            assert rejected["ok"] is False
            assert "authentication failed" in rejected["error"]
    finally:
        pop_runtime_context(token)

    assert registry.calls[0][2].kind == "react"
    assert registry.calls[0][2].agent_id == "codex-mission"
    assert registry.runtime_seen.user_id == "owner-1"
    assert len(observations) == 1


@pytest.mark.asyncio
async def test_cancelled_bridge_refuses_before_registry_execution():
    registry = FakeRegistry()
    async with LumenaCodexToolBridge(
        registry,
        allowed_tools={"read_file"},
        agent_id="codex-mission",
        cancel_requested=lambda: True,
    ) as bridge:
        response = await _bridge_rpc(
            bridge.endpoint,
            {"op": "call", "name": "read_file", "arguments": {"path": "x"}},
        )
    assert response["ok"] is False
    assert "cancellation requested" in response["error"]
    assert registry.calls == []


def test_stdio_mcp_protocol_forwards_tools_and_never_exposes_parent_token(monkeypatch):
    calls = []

    def fake_request(payload):
        calls.append(payload)
        if payload["op"] == "list":
            return {"ok": True, "tools": [{"name": "read_file", "inputSchema": {}}]}
        return {
            "ok": True,
            "result": {"content": [{"type": "text", "text": "ok"}], "isError": False},
        }

    monkeypatch.setattr(bridge_module, "_bridge_request", fake_request)
    initialized = _mcp_result(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    )
    assert initialized["result"]["capabilities"] == {"tools": {"listChanged": False}}
    assert "LUMENA_CODEX_BRIDGE_TOKEN" not in json.dumps(initialized)
    listed = _mcp_result({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert listed["result"]["tools"][0]["name"] == "read_file"
    called = _mcp_result(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "read_file", "arguments": {"path": "a"}},
        }
    )
    assert called["result"]["content"][0]["text"] == "ok"
    assert calls[-1]["arguments"] == {"path": "a"}
    assert _mcp_result({"method": "notifications/initialized"}) is None


@pytest.mark.asyncio
async def test_real_stdio_child_process_lists_parent_scoped_tools(tmp_path):
    registry = FakeRegistry()
    async with LumenaCodexToolBridge(
        registry,
        allowed_tools={"read_file"},
        agent_id="codex-agent",
    ) as bridge:
        endpoint = bridge.endpoint
        environment = dict(os.environ)
        environment.update(
            {
                "LUMENA_CODEX_BRIDGE_HOST": endpoint.host,
                "LUMENA_CODEX_BRIDGE_PORT": str(endpoint.port),
                "LUMENA_CODEX_BRIDGE_TOKEN": endpoint.token,
                # Reproduce the legacy Windows stream encoding inherited by a
                # Codex-launched MCP child. The protocol must remain UTF-8.
                "PYTHONIOENCODING": "cp1252",
            }
        )
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "src.llm.codex_mcp_bridge",
            cwd=str(Path(__file__).resolve().parents[2]),
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdin is not None and process.stdout is not None
        requests = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "read_file",
                    "arguments": {"path": "é-📄.txt"},
                },
            },
            {"jsonrpc": "2.0", "id": 4, "method": "ping", "params": {}},
        ]
        for request in requests:
            process.stdin.write((json.dumps(request) + "\n").encode())
        await process.stdin.drain()
        initialized = json.loads((await process.stdout.readline()).decode())
        listed = json.loads((await process.stdout.readline()).decode())
        called = json.loads((await process.stdout.readline()).decode())
        pinged = json.loads((await process.stdout.readline()).decode())
        assert initialized["id"] == 1
        # LOT Z34 — le pont declare le perimetre du run PLUS l'invocateur, et
        # RIEN d'autre : `invoke_tool` ouvre une porte, il n'abat pas le mur
        # (l'appel direct hors perimetre reste refuse, cf. test_z34_*).
        assert [tool["name"] for tool in listed["result"]["tools"]] == [
            "read_file", "invoke_tool",
        ]
        assert called["result"]["isError"] is False
        assert "é-📄.txt" in called["result"]["content"][0]["text"]
        assert pinged == {"jsonrpc": "2.0", "id": 4, "result": {}}
        process.stdin.close()
        await process.wait()
        assert process.returncode == 0
