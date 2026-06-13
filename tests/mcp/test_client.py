"""
Tests pour MCPClient (Phase 7).

Garanties à prouver :
  - Refus de Popen avec stderr=None (incompatible stdio MCP)
  - initialize envoie protocolVersion, capabilities, clientInfo
  - initialize envoie notifications/initialized après succès
  - tools/call envoie params {name, arguments} (pas args)
  - Parse isError (camelCase) + structuredContent
  - Méthodes refusent avant initialize() / après close()
  - JSON-RPC IDs uniques et thread-safe
  - Timeout par appel
"""
from __future__ import annotations

import io
import json
import subprocess
import threading
import time
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock

import pytest

from src.mcp.client import (
    LUMENA_CLIENT_CAPABILITIES,
    LUMENA_CLIENT_INFO,
    MCP_PROTOCOL_VERSION,
    MCPCallResult,
    MCPClient,
    MCPClientError,
    MCPNotInitializedError,
    MCPProtocolError,
    MCPTimeoutError,
    MCPTool,
)


# ──────────────────────────────────────────────────────────────────────────────
# Fake Popen
# ──────────────────────────────────────────────────────────────────────────────


class _FakePipe:
    """Pipe falsifié : buffer en mémoire avec readline/write."""

    def __init__(self):
        self._lines: List[str] = []
        self._cond = threading.Condition()
        self._closed = False

    def push_response(self, line: str):
        """Le test pousse une ligne que readline pourra retourner."""
        with self._cond:
            self._lines.append(line if line.endswith("\n") else line + "\n")
            self._cond.notify_all()

    def readline(self) -> str:
        with self._cond:
            while not self._lines and not self._closed:
                self._cond.wait(timeout=1.0)
            if self._lines:
                return self._lines.pop(0)
            return ""

    def write(self, s: str) -> int:
        # Cas stdin (les écritures du client)
        if not hasattr(self, "writes"):
            self.writes = []  # type: ignore[attr-defined]
        self.writes.append(s)  # type: ignore[attr-defined]
        return len(s)

    def flush(self):
        pass

    def close_eof(self):
        with self._cond:
            self._closed = True
            self._cond.notify_all()


def _make_fake_popen(
    *,
    with_stderr: bool = True,
    with_stdin: bool = True,
    with_stdout: bool = True,
) -> MagicMock:
    """Construit un fake subprocess.Popen avec pipes contrôlables."""
    proc = MagicMock(spec=subprocess.Popen)
    proc.stdin = _FakePipe() if with_stdin else None
    proc.stdout = _FakePipe() if with_stdout else None
    proc.stderr = _FakePipe() if with_stderr else None
    proc.pid = 9999
    proc.poll = MagicMock(return_value=None)
    proc.terminate = MagicMock()
    proc.kill = MagicMock()
    return proc


def _make_response(id_: int, result: Any) -> str:
    return json.dumps({"jsonrpc": "2.0", "id": id_, "result": result})


def _make_error_response(id_: int, code: int, message: str) -> str:
    return json.dumps(
        {"jsonrpc": "2.0", "id": id_, "error": {"code": code, "message": message}}
    )


def _written_messages(proc) -> List[Dict[str, Any]]:
    """Récupère et parse les messages JSON-RPC envoyés via proc.stdin.write."""
    pipe = proc.stdin
    if not hasattr(pipe, "writes"):
        return []
    return [json.loads(s.strip()) for s in pipe.writes if s.strip()]


# ──────────────────────────────────────────────────────────────────────────────
# Refus Popen incompatible
# ──────────────────────────────────────────────────────────────────────────────


def test_client_refuses_popen_with_stderr_none():
    """Garde-fou critique : stderr=None signale stderr=STDOUT merge incompatible."""
    proc = _make_fake_popen(with_stderr=False)
    with pytest.raises(MCPClientError, match="stderr"):
        MCPClient(proc, server_name="test")


def test_client_refuses_popen_without_stdin():
    proc = _make_fake_popen(with_stdin=False)
    with pytest.raises(MCPClientError, match="stdin"):
        MCPClient(proc, server_name="test")


def test_client_refuses_popen_without_stdout():
    proc = _make_fake_popen(with_stdout=False)
    with pytest.raises(MCPClientError):
        MCPClient(proc, server_name="test")


def test_client_refuses_empty_server_name():
    proc = _make_fake_popen()
    with pytest.raises(MCPClientError, match="server_name"):
        MCPClient(proc, server_name="")
    with pytest.raises(MCPClientError, match="server_name"):
        MCPClient(proc, server_name="   ")


# ──────────────────────────────────────────────────────────────────────────────
# Initialize
# ──────────────────────────────────────────────────────────────────────────────


def test_initialize_sends_protocol_version_capabilities_client_info():
    proc = _make_fake_popen()
    client = MCPClient(proc, server_name="test")

    # Push la réponse à initialize
    proc.stdout.push_response(
        _make_response(1, {"capabilities": {"tools": {}}, "serverInfo": {"name": "fake"}})
    )

    client.initialize()
    messages = _written_messages(proc)

    # Premier message = initialize
    init_msg = messages[0]
    assert init_msg["jsonrpc"] == "2.0"
    assert init_msg["method"] == "initialize"
    assert init_msg["id"] == 1
    params = init_msg["params"]
    assert params["protocolVersion"] == MCP_PROTOCOL_VERSION
    assert params["capabilities"] == LUMENA_CLIENT_CAPABILITIES
    assert params["clientInfo"] == LUMENA_CLIENT_INFO


def test_initialize_sends_initialized_notification_after_init():
    proc = _make_fake_popen()
    client = MCPClient(proc, server_name="test")
    proc.stdout.push_response(_make_response(1, {"capabilities": {}}))

    client.initialize()
    messages = _written_messages(proc)

    # 2 messages : initialize (avec id) + notification (sans id)
    assert len(messages) >= 2
    notif = messages[1]
    assert notif["method"] == "notifications/initialized"
    assert "id" not in notif
    assert notif["jsonrpc"] == "2.0"


def test_initialize_captures_server_capabilities():
    proc = _make_fake_popen()
    client = MCPClient(proc, server_name="test")
    proc.stdout.push_response(
        _make_response(1, {"capabilities": {"tools": {"listChanged": True}}})
    )

    client.initialize()
    assert client.server_capabilities == {"tools": {"listChanged": True}}


def test_initialize_only_once():
    proc = _make_fake_popen()
    client = MCPClient(proc, server_name="test")
    proc.stdout.push_response(_make_response(1, {"capabilities": {}}))
    client.initialize()
    with pytest.raises(MCPClientError, match="already initialized"):
        client.initialize()


def test_initialize_sets_is_initialized():
    proc = _make_fake_popen()
    client = MCPClient(proc, server_name="test")
    assert client.is_initialized is False
    proc.stdout.push_response(_make_response(1, {"capabilities": {}}))
    client.initialize()
    assert client.is_initialized is True


# ──────────────────────────────────────────────────────────────────────────────
# Refus pre-initialize / post-close
# ──────────────────────────────────────────────────────────────────────────────


def test_list_tools_before_initialize_raises():
    proc = _make_fake_popen()
    client = MCPClient(proc, server_name="test")
    with pytest.raises(MCPNotInitializedError):
        client.list_tools()


def test_call_tool_before_initialize_raises():
    proc = _make_fake_popen()
    client = MCPClient(proc, server_name="test")
    with pytest.raises(MCPNotInitializedError):
        client.call_tool("any", {})


def test_close_marks_closed():
    proc = _make_fake_popen()
    client = MCPClient(proc, server_name="test")
    client.close()
    assert client.is_closed is True


def test_close_does_not_kill_subprocess():
    """Le client ne kill PAS le subprocess (ownership = caller)."""
    proc = _make_fake_popen()
    client = MCPClient(proc, server_name="test")
    client.close()
    proc.terminate.assert_not_called()
    proc.kill.assert_not_called()


def test_methods_after_close_raise():
    proc = _make_fake_popen()
    client = MCPClient(proc, server_name="test")
    proc.stdout.push_response(_make_response(1, {"capabilities": {}}))
    client.initialize()
    client.close()
    with pytest.raises(MCPClientError, match="closed"):
        client.list_tools()


# ──────────────────────────────────────────────────────────────────────────────
# tools/list
# ──────────────────────────────────────────────────────────────────────────────


def _init_and_get_client(server_name="test"):
    proc = _make_fake_popen()
    client = MCPClient(proc, server_name=server_name)
    proc.stdout.push_response(_make_response(1, {"capabilities": {}}))
    client.initialize()
    return proc, client


def test_list_tools_parses_entries():
    proc, client = _init_and_get_client()
    proc.stdout.push_response(
        _make_response(
            2,
            {
                "tools": [
                    {
                        "name": "echo",
                        "description": "Echoes input",
                        "inputSchema": {"type": "object", "properties": {"x": {}}},
                    },
                    {
                        "name": "add",
                        "description": "Adds two numbers",
                        "inputSchema": {"type": "object"},
                    },
                ]
            },
        )
    )

    tools = client.list_tools()
    assert len(tools) == 2
    assert tools[0].name == "echo"
    assert tools[0].description == "Echoes input"
    assert tools[0].input_schema["type"] == "object"
    assert tools[1].name == "add"


def test_list_tools_empty():
    proc, client = _init_and_get_client()
    proc.stdout.push_response(_make_response(2, {"tools": []}))
    assert client.list_tools() == []


def test_list_tools_skips_invalid_entries():
    proc, client = _init_and_get_client()
    proc.stdout.push_response(
        _make_response(
            2,
            {
                "tools": [
                    {"name": "valid", "description": "ok", "inputSchema": {}},
                    {"description": "no name"},
                    "not-a-dict",
                    {"name": "", "description": "empty name"},
                ]
            },
        )
    )
    tools = client.list_tools()
    assert len(tools) == 1
    assert tools[0].name == "valid"


def test_list_tools_supports_snake_case_input_schema():
    proc, client = _init_and_get_client()
    proc.stdout.push_response(
        _make_response(
            2,
            {"tools": [{"name": "t", "input_schema": {"type": "object"}}]},
        )
    )
    tools = client.list_tools()
    assert tools[0].input_schema == {"type": "object"}


# ──────────────────────────────────────────────────────────────────────────────
# tools/call
# ──────────────────────────────────────────────────────────────────────────────


def test_call_tool_uses_arguments_key_not_args():
    """La requête JSON-RPC envoie `arguments`, pas `args` (spec MCP)."""
    proc, client = _init_and_get_client()
    proc.stdout.push_response(
        _make_response(2, {"content": [{"type": "text", "text": "ok"}]})
    )

    client.call_tool("echo", {"x": 42})
    messages = _written_messages(proc)
    # initialize + notif + tools/call
    call_msg = messages[-1]
    assert call_msg["method"] == "tools/call"
    assert call_msg["params"]["name"] == "echo"
    assert call_msg["params"]["arguments"] == {"x": 42}
    assert "args" not in call_msg["params"]


def test_call_tool_returns_content():
    proc, client = _init_and_get_client()
    proc.stdout.push_response(
        _make_response(2, {"content": [{"type": "text", "text": "result"}]})
    )
    result = client.call_tool("t", {})
    assert isinstance(result, MCPCallResult)
    assert result.is_error is False
    assert result.content == [{"type": "text", "text": "result"}]


def test_call_tool_parses_isError_camelCase():
    """isError doit être lu en camelCase (pas snake_case)."""
    proc, client = _init_and_get_client()
    proc.stdout.push_response(
        _make_response(
            2,
            {
                "isError": True,
                "content": [{"type": "text", "text": "tool failed"}],
            },
        )
    )
    result = client.call_tool("t", {})
    assert result.is_error is True
    assert result.error_message == "tool failed"


def test_call_tool_parses_structured_content():
    """structuredContent doit être propagé."""
    proc, client = _init_and_get_client()
    proc.stdout.push_response(
        _make_response(
            2,
            {
                "content": [{"type": "text", "text": "see structured"}],
                "structuredContent": {"a": 1, "b": [2, 3]},
            },
        )
    )
    result = client.call_tool("t", {})
    assert result.structured_content == {"a": 1, "b": [2, 3]}


def test_call_tool_invalid_args_type_raises():
    proc, client = _init_and_get_client()
    with pytest.raises(MCPClientError, match="args"):
        client.call_tool("t", "not_a_dict")  # type: ignore


def test_call_tool_invalid_name_raises():
    proc, client = _init_and_get_client()
    with pytest.raises(MCPClientError, match="tool name"):
        client.call_tool("", {})


def test_call_tool_isError_no_text_returns_default_message():
    proc, client = _init_and_get_client()
    proc.stdout.push_response(
        _make_response(2, {"isError": True, "content": []})
    )
    result = client.call_tool("t", {})
    assert result.is_error is True
    # error_message peut être None puisque pas de text content
    assert result.error_message is None


def test_call_tool_with_top_level_error_field():
    """Si le serveur retourne `error` au niveau JSON-RPC → MCPClientError."""
    proc, client = _init_and_get_client()
    proc.stdout.push_response(_make_error_response(2, -32601, "Method not found"))
    with pytest.raises(MCPClientError, match="Method not found"):
        client.call_tool("t", {})


# ──────────────────────────────────────────────────────────────────────────────
# JSON-RPC IDs
# ──────────────────────────────────────────────────────────────────────────────


def test_ids_increment():
    proc, client = _init_and_get_client()
    proc.stdout.push_response(_make_response(2, {"content": [], "tools": []}))
    proc.stdout.push_response(
        _make_response(3, {"content": [{"type": "text", "text": "ok"}]})
    )
    client.list_tools()
    client.call_tool("t", {})

    messages = _written_messages(proc)
    # Message 0 = initialize id=1, message 1 = notif (no id),
    # message 2 = tools/list id=2, message 3 = tools/call id=3
    ids = [m.get("id") for m in messages if "id" in m]
    assert ids == [1, 2, 3]


def test_response_with_unexpected_id_raises():
    proc, client = _init_and_get_client()
    # On pousse une réponse avec id=99 alors qu'on attend id=2
    proc.stdout.push_response(_make_response(99, {"tools": []}))
    with pytest.raises(MCPProtocolError, match="Unexpected response id"):
        client.list_tools()


def test_concurrent_calls_serialized_get_unique_ids():
    """Le call_lock sérialise les appels — chaque call obtient un id unique."""
    proc, client = _init_and_get_client()

    # 3 réponses préchargées
    proc.stdout.push_response(_make_response(2, {"tools": []}))
    proc.stdout.push_response(_make_response(3, {"tools": []}))
    proc.stdout.push_response(_make_response(4, {"tools": []}))

    def worker():
        client.list_tools()

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    messages = _written_messages(proc)
    list_ids = [m["id"] for m in messages if m.get("method") == "tools/list"]
    assert sorted(list_ids) == [2, 3, 4]
    assert len(set(list_ids)) == 3  # uniques


# ──────────────────────────────────────────────────────────────────────────────
# Notification ignored, malformed lines, EOF
# ──────────────────────────────────────────────────────────────────────────────


def test_notification_lines_ignored_while_waiting_response():
    proc, client = _init_and_get_client()
    # Server-pushed notification (no id) puis vraie réponse
    proc.stdout.push_response(
        json.dumps({"jsonrpc": "2.0", "method": "notifications/progress", "params": {}})
    )
    proc.stdout.push_response(_make_response(2, {"tools": []}))
    tools = client.list_tools()
    assert tools == []


def test_malformed_json_lines_ignored():
    proc, client = _init_and_get_client()
    proc.stdout.push_response("not json at all")
    proc.stdout.push_response(_make_response(2, {"tools": []}))
    tools = client.list_tools()
    assert tools == []


def test_eof_raises_channel_broken():
    """Fix X (Phase I-7) : un EOF n'est PLUS un timeout déguisé.

    Avant : EOF → MCPTimeoutError("after 30.0s") en quelques ms — message
    trompeur qui a masqué la vraie cause sur 2 sessions runtime.
    Maintenant : MCPProtocolError explicite (process died OU stdio channel
    broken selon poll()), et le client se marque closed pour déclencher
    le self-healing Fix S/W."""
    proc, client = _init_and_get_client()
    proc.stdout.close_eof()
    with pytest.raises(MCPProtocolError) as exc_info:
        client.list_tools(timeout_s=0.5)
    msg = str(exc_info.value)
    assert "read_cause=eof" in msg
    assert client.is_closed is True


# ──────────────────────────────────────────────────────────────────────────────
# Timeout
# ──────────────────────────────────────────────────────────────────────────────


def test_timeout_raises_mcp_timeout_error():
    proc, client = _init_and_get_client()
    # On ne push aucune réponse → call doit timeout
    with pytest.raises(MCPTimeoutError):
        client.list_tools(timeout_s=0.5)


def test_default_timeout_propagated():
    proc = _make_fake_popen()
    client = MCPClient(proc, server_name="test", default_timeout_s=0.5)
    proc.stdout.push_response(_make_response(1, {"capabilities": {}}))
    client.initialize()
    # Pas de réponse pushed → default_timeout kicks in
    with pytest.raises(MCPTimeoutError):
        client.list_tools()


# ──────────────────────────────────────────────────────────────────────────────
# Properties
# ──────────────────────────────────────────────────────────────────────────────


def test_server_name_property():
    proc = _make_fake_popen()
    client = MCPClient(proc, server_name="my-server")
    assert client.server_name == "my-server"


def test_server_capabilities_returns_copy():
    """Le getter doit retourner une copie pour éviter mutations externes."""
    proc = _make_fake_popen()
    client = MCPClient(proc, server_name="test")
    proc.stdout.push_response(_make_response(1, {"capabilities": {"x": 1}}))
    client.initialize()
    caps = client.server_capabilities
    caps["x"] = 999  # mutation
    assert client.server_capabilities == {"x": 1}  # inchangé
