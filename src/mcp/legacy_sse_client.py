"""Compatibility client for the deprecated MCP HTTP+SSE transport.

The SSE endpoint is read on a bounded background channel while JSON-RPC
messages are POSTed to the server-advertised same-origin message endpoint.
New integrations should use Streamable HTTP; this exists for older host apps.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from src.mcp.client import (
    LUMENA_CLIENT_CAPABILITIES,
    LUMENA_CLIENT_INFO,
    MCPCallResult,
    MCPClient,
    MCPClientError,
    MCPNotInitializedError,
    MCPProtocolError,
    MCPTimeoutError,
    MCPTool,
)
from src.mcp.connection_spec import RemoteSpec
from src.mcp.remote_client import MCPPrompt, MCPResource


MAX_SSE_EVENT_BYTES = 4 * 1024 * 1024
SSEConnect = Callable[[str, Mapping[str, str], float], Iterable[Any]]
PostCallable = Callable[[str, Mapping[str, str], bytes, float], Tuple[int, bytes]]


class LegacySSEMCPClient:
    def __init__(
        self,
        *,
        server_name: str,
        remote: RemoteSpec,
        auth_headers: Optional[Mapping[str, str]] = None,
        default_timeout_s: float = 30.0,
        sse_connect: Optional[SSEConnect] = None,
        post: Optional[PostCallable] = None,
    ) -> None:
        self.server_name = server_name
        self._remote = remote
        self._headers = dict(auth_headers or {})
        if set(self._headers) - set(remote.header_names):
            raise MCPClientError("Remote auth header was not declared by connection spec")
        self._timeout = max(0.1, min(float(default_timeout_s), 300.0))
        self._connect = sse_connect or self._default_connect
        self._post_http = post or self._default_post
        self._condition = threading.Condition()
        self._endpoint: Optional[str] = None
        self._responses: Dict[Any, Dict[str, Any]] = {}
        self._listener_error: Optional[str] = None
        self._stream: Optional[Iterable[Any]] = None
        self._listener: Optional[threading.Thread] = None
        self._next_request_id = 0
        self._initialized = False
        self._closed = False
        self._server_capabilities: Dict[str, Any] = {}

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def server_capabilities(self) -> Dict[str, Any]:
        return dict(self._server_capabilities)

    def _start_listener(self) -> None:
        stream_url = self._remote.legacy_sse_url or self._remote.url
        self._stream = self._connect(
            stream_url,
            {**self._headers, "Accept": "text/event-stream", "Cache-Control": "no-cache"},
            self._timeout,
        )
        self._listener = threading.Thread(
            target=self._listen, name=f"mcp-sse-{self.server_name}", daemon=True
        )
        self._listener.start()
        deadline = time.monotonic() + self._timeout
        with self._condition:
            while self._endpoint is None and self._listener_error is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MCPTimeoutError("Legacy MCP SSE endpoint timed out")
                self._condition.wait(remaining)
            if self._listener_error:
                raise MCPProtocolError(self._listener_error)

    def _listen(self) -> None:
        event_name = "message"
        data_lines: List[str] = []
        event_bytes = 0
        try:
            assert self._stream is not None
            for raw_line in self._stream:
                if self._closed:
                    return
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else str(raw_line)
                line = line.rstrip("\r\n")
                if not line:
                    if data_lines:
                        self._dispatch_event(event_name, "\n".join(data_lines))
                    event_name, data_lines, event_bytes = "message", [], 0
                    continue
                event_bytes += len(line.encode("utf-8", errors="replace"))
                if event_bytes > MAX_SSE_EVENT_BYTES:
                    raise MCPProtocolError("Legacy MCP SSE event exceeds max size")
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event_name = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
        except Exception:
            with self._condition:
                if not self._closed:
                    self._listener_error = "legacy_sse_stream_failed"
                self._condition.notify_all()

    def _dispatch_event(self, event_name: str, data: str) -> None:
        if event_name == "endpoint":
            endpoint = urljoin(self._remote.url, data)
            base, candidate = urlsplit(self._remote.url), urlsplit(endpoint)
            if (
                candidate.scheme != base.scheme
                or candidate.hostname != base.hostname
                or candidate.port != base.port
                or candidate.username
                or candidate.password
            ):
                raise MCPProtocolError("Legacy MCP endpoint changed origin")
            with self._condition:
                self._endpoint = endpoint
                self._condition.notify_all()
            return
        try:
            message = json.loads(data)
        except json.JSONDecodeError as exc:
            raise MCPProtocolError("Invalid legacy SSE JSON") from exc
        if isinstance(message, dict) and "id" in message:
            with self._condition:
                self._responses[message["id"]] = message
                self._condition.notify_all()

    def _call(self, method: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self._closed or self._endpoint is None:
            raise MCPClientError("Legacy MCP client is not connected")
        with self._condition:
            self._next_request_id += 1
            request_id = self._next_request_id
        message: Dict[str, Any] = {
            "jsonrpc": "2.0", "id": request_id, "method": method,
        }
        if params is not None:
            message["params"] = params
        body = json.dumps(message, separators=(",", ":")).encode("utf-8")
        status, _ = self._post_http(
            self._endpoint,
            {**self._headers, "Content-Type": "application/json"},
            body,
            self._timeout,
        )
        if status not in {200, 202, 204}:
            raise MCPProtocolError(f"Legacy MCP POST status {status}")
        deadline = time.monotonic() + self._timeout
        with self._condition:
            while request_id not in self._responses and self._listener_error is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MCPTimeoutError("Legacy MCP response timed out")
                self._condition.wait(remaining)
            if self._listener_error:
                raise MCPProtocolError(self._listener_error)
            response = self._responses.pop(request_id)
        if "error" in response:
            error = response.get("error") or {}
            raise MCPClientError(f"MCP error {error.get('code', -1)}: {error.get('message', 'error')}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise MCPProtocolError("Legacy MCP response missing result")
        return result

    def _notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        assert self._endpoint is not None
        message: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        status, _ = self._post_http(
            self._endpoint,
            {**self._headers, "Content-Type": "application/json"},
            json.dumps(message, separators=(",", ":")).encode(),
            self._timeout,
        )
        if status not in {200, 202, 204}:
            raise MCPProtocolError(f"Legacy MCP notification status {status}")

    def initialize(self, *, timeout_s: Optional[float] = None) -> Dict[str, Any]:
        if self._initialized:
            raise MCPClientError("Legacy MCP client already initialized")
        if timeout_s is not None:
            self._timeout = max(0.1, min(float(timeout_s), 300.0))
        self._start_listener()
        result = self._call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": dict(LUMENA_CLIENT_CAPABILITIES),
            "clientInfo": dict(LUMENA_CLIENT_INFO),
        })
        capabilities = result.get("capabilities")
        if isinstance(capabilities, dict):
            self._server_capabilities = dict(capabilities)
        self._notify("notifications/initialized", {})
        self._initialized = True
        return result

    def _ensure(self) -> None:
        if not self._initialized:
            raise MCPNotInitializedError("Legacy MCP client is not initialized")

    def list_tools(self, *, timeout_s: Optional[float] = None) -> List[MCPTool]:
        self._ensure()
        raw = self._call("tools/list", {}).get("tools", [])
        if not isinstance(raw, list) or len(raw) > 2000:
            raise MCPProtocolError("Invalid legacy tools list")
        return [MCPTool(
            name=item["name"],
            description=item.get("description", "") if isinstance(item.get("description", ""), str) else "",
            input_schema=item.get("inputSchema", {}) if isinstance(item.get("inputSchema", {}), dict) else {},
        ) for item in raw if isinstance(item, dict) and isinstance(item.get("name"), str)]

    def call_tool(self, name: str, args: Dict[str, Any], *, timeout_s: Optional[float] = None) -> MCPCallResult:
        self._ensure()
        return MCPClient._parse_call_result(
            self._call("tools/call", {"name": name, "arguments": args})
        )

    def list_resources(self, *, timeout_s: Optional[float] = None) -> List[MCPResource]:
        self._ensure()
        raw = self._call("resources/list", {}).get("resources", [])
        return [MCPResource(uri=i["uri"], name=i.get("name", i["uri"])) for i in raw if isinstance(i, dict) and isinstance(i.get("uri"), str)]

    def read_resource(self, uri: str, *, timeout_s: Optional[float] = None) -> Dict[str, Any]:
        self._ensure()
        return self._call("resources/read", {"uri": uri})

    def list_prompts(self, *, timeout_s: Optional[float] = None) -> List[MCPPrompt]:
        self._ensure()
        raw = self._call("prompts/list", {}).get("prompts", [])
        return [MCPPrompt(name=i["name"], description=i.get("description", "")) for i in raw if isinstance(i, dict) and isinstance(i.get("name"), str)]

    def get_prompt(self, name: str, arguments=None, *, timeout_s=None) -> Dict[str, Any]:
        self._ensure()
        return self._call("prompts/get", {"name": name, "arguments": arguments or {}})

    def close(self) -> None:
        self._closed = True
        stream = self._stream
        close = getattr(stream, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                pass
        with self._condition:
            self._condition.notify_all()
        self._initialized = False

    @staticmethod
    def _default_connect(url, headers, timeout):
        request = Request(url, headers=dict(headers), method="GET")
        return urlopen(request, timeout=timeout)  # noqa: S310

    @staticmethod
    def _default_post(url, headers, body, timeout):
        request = Request(url, data=body, headers=dict(headers), method="POST")
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            return int(response.status), response.read(1024 * 1024)


__all__ = ["LegacySSEMCPClient"]
