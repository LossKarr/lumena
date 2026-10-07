from __future__ import annotations

import json
import queue

import pytest

from src.mcp.connection_spec import RemoteSpec
from src.mcp.legacy_sse_client import LegacySSEMCPClient
from src.mcp.client import MCPProtocolError


class Stream:
    def __init__(self):
        self.lines = queue.Queue()
        self.closed = False

    def push(self, event, data):
        self.lines.put(f"event: {event}\n")
        self.lines.put(f"data: {data}\n")
        self.lines.put("\n")

    def __iter__(self):
        while not self.closed:
            item = self.lines.get(timeout=2)
            yield item

    def close(self):
        self.closed = True
        self.lines.put("")


def test_legacy_sse_initialize_and_tool_call():
    stream = Stream()
    stream.push("endpoint", "/messages?session=safe")
    posts = []

    def post(url, headers, body, timeout):
        request = json.loads(body)
        posts.append((url, request))
        if "id" in request:
            if request["method"] == "initialize":
                result = {"capabilities": {"tools": {}}}
            elif request["method"] == "tools/list":
                result = {"tools": [{"name": "build", "inputSchema": {"type": "object"}}]}
            else:
                result = {"content": [{"type": "text", "text": "done"}], "isError": False}
            stream.push("message", json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}))
        return 202, b""

    client = LegacySSEMCPClient(
        server_name="old",
        remote=RemoteSpec(
            url="https://old.example.test/sse",
            legacy_sse_url="https://old.example.test/sse",
        ),
        sse_connect=lambda *_: stream,
        post=post,
    )
    client.initialize()
    assert client.list_tools()[0].name == "build"
    assert client.call_tool("build", {}).content[0]["text"] == "done"
    assert posts[0][0] == "https://old.example.test/messages?session=safe"
    client.close()


def test_legacy_sse_refuses_cross_origin_message_endpoint():
    stream = Stream()
    stream.push("endpoint", "https://evil.example/messages")
    client = LegacySSEMCPClient(
        server_name="old",
        remote=RemoteSpec(url="https://old.example.test/sse", legacy_sse_url="https://old.example.test/sse"),
        sse_connect=lambda *_: stream,
        post=lambda *_: (202, b""),
    )
    with pytest.raises(MCPProtocolError, match="legacy_sse_stream_failed"):
        client.initialize(timeout_s=1)
