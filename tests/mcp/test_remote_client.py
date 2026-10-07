from __future__ import annotations

import json

import pytest

from src.mcp.client import MCPClientError, MCPProtocolError
from src.mcp.connection_spec import RemoteSpec
from src.mcp.remote_client import RemoteMCPClient


class _Server:
    def __init__(self, *, sse=False):
        self.calls = []
        self.sse = sse

    def __call__(self, method, url, headers, body, timeout):
        request = json.loads(body) if body else None
        self.calls.append((method, url, dict(headers), request))
        if method == "DELETE":
            return 204, {}, b""
        rpc_method = request["method"]
        if "id" not in request:
            return 202, {}, b""
        if rpc_method == "initialize":
            result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}
            response_headers = {"Content-Type": "application/json", "Mcp-Session-Id": "session-123"}
        elif rpc_method == "tools/list":
            result = {"tools": [{"name": "search", "description": "Search", "inputSchema": {"type": "object"}}]}
            response_headers = {"Content-Type": "application/json"}
        elif rpc_method == "tools/call":
            result = {"content": [{"type": "text", "text": "ok"}], "isError": False}
            response_headers = {"Content-Type": "application/json"}
        else:
            result = {}
            response_headers = {"Content-Type": "application/json"}
        response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        if self.sse and rpc_method == "tools/list":
            wire = "event: message\ndata: " + json.dumps(response) + "\n\n"
            return 200, {"Content-Type": "text/event-stream"}, wire.encode()
        return 200, response_headers, json.dumps(response).encode()


def _client(server, **kwargs):
    return RemoteMCPClient(
        server_name="remote",
        remote=RemoteSpec(url="https://mcp.example.test/v1", header_names=("Authorization",)),
        auth_headers={"Authorization": "Bearer in-memory"},
        http=server,
        **kwargs,
    )


def test_initialize_session_tools_and_call():
    server = _Server()
    client = _client(server)
    client.initialize()
    tools = client.list_tools()
    result = client.call_tool("search", {"q": "lumena"})
    assert tools[0].name == "search"
    assert result.content[0]["text"] == "ok"
    assert server.calls[2][2]["Mcp-Session-Id"] == "session-123"
    assert server.calls[0][2]["MCP-Protocol-Version"] == "2025-06-18"


def test_request_scoped_sse_response_is_supported():
    server = _Server(sse=True)
    client = _client(server)
    client.initialize()
    assert client.list_tools()[0].name == "search"


def test_undeclared_auth_header_is_rejected():
    with pytest.raises(MCPClientError, match="not declared"):
        RemoteMCPClient(
            server_name="remote",
            remote=RemoteSpec(url="https://mcp.example.test/v1"),
            auth_headers={"Authorization": "secret"},
            http=_Server(),
        )


def test_session_expiry_does_not_replay_request():
    calls = []

    def expired(method, url, headers, body, timeout):
        calls.append(method)
        request = json.loads(body)
        if request["method"] == "initialize":
            response = {"jsonrpc": "2.0", "id": request["id"], "result": {"capabilities": {}}}
            return 200, {"Content-Type": "application/json", "Mcp-Session-Id": "gone"}, json.dumps(response).encode()
        if "id" not in request:
            return 202, {}, b""
        return 404, {}, b""

    client = _client(expired)
    client.initialize()
    with pytest.raises(MCPProtocolError, match="reinitialization"):
        client.list_tools()
    assert len(calls) == 3


def test_close_terminates_legacy_session():
    server = _Server()
    client = _client(server)
    client.initialize()
    client.close()
    assert server.calls[-1][0] == "DELETE"


def test_response_id_and_sse_json_are_strict():
    def mismatch(method, url, headers, body, timeout):
        request = json.loads(body)
        return 200, {"Content-Type": "application/json"}, json.dumps({
            "jsonrpc": "2.0", "id": request["id"] + 1, "result": {}
        }).encode()

    with pytest.raises(MCPProtocolError, match="id mismatch"):
        _client(mismatch).initialize()


def test_2026_stateless_wire_uses_discover_metadata_and_routing_headers():
    calls = []

    def modern(method, url, headers, body, timeout):
        request = json.loads(body)
        calls.append((dict(headers), request))
        rpc_method = request["method"]
        if rpc_method == "server/discover":
            result = {
                "supportedProtocolVersions": ["2026-07-28"],
                "capabilities": {"tools": {}},
                "resultType": "complete",
            }
        elif rpc_method == "tools/list":
            result = {"tools": [], "resultType": "complete"}
        else:
            result = {"resultType": "complete"}
        payload = {
            "jsonrpc": "2.0", "id": request["id"], "result": result,
        }
        return 200, {"Content-Type": "application/json"}, json.dumps(payload).encode()

    client = _client(modern, protocol_version="2026-07-28")
    client.initialize()
    client.list_tools()
    assert calls[0][1]["method"] == "server/discover"
    assert calls[0][0]["Mcp-Method"] == "server/discover"
    meta = calls[1][1]["params"]["_meta"]
    assert meta["io.modelcontextprotocol/protocolVersion"] == "2026-07-28"
    assert "io.modelcontextprotocol/clientCapabilities" in meta
    assert all("Mcp-Session-Id" not in headers for headers, _ in calls)


def test_2026_mcp_name_is_mirrored_and_non_ascii_is_base64_encoded():
    calls = []

    def modern(method, url, headers, body, timeout):
        request = json.loads(body)
        calls.append(dict(headers))
        result = (
            {"supportedProtocolVersions": ["2026-07-28"], "resultType": "complete"}
            if request["method"] == "server/discover"
            else {"contents": [], "resultType": "complete"}
        )
        response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
        return 200, {"Content-Type": "application/json"}, json.dumps(response).encode()

    client = _client(modern, protocol_version="2026-07-28")
    client.initialize()
    client.read_resource("file:///école.txt")
    assert calls[-1]["Mcp-Method"] == "resources/read"
    assert calls[-1]["Mcp-Name"].startswith(":base64:")


def test_2026_rejects_legacy_session_header():
    def bad_server(method, url, headers, body, timeout):
        request = json.loads(body)
        response = {"jsonrpc": "2.0", "id": request["id"], "result": {}}
        return 200, {
            "Content-Type": "application/json", "Mcp-Session-Id": "legacy",
        }, json.dumps(response).encode()

    with pytest.raises(MCPProtocolError, match="attempted to create a session"):
        _client(bad_server, protocol_version="2026-07-28").initialize()

