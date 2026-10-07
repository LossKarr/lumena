"""Bounded MCP client for remote Streamable HTTP servers.

Supports JSON responses and request-scoped SSE responses.  The default
protocol revision uses the interoperable initialize/session flow; the selected
revision is explicit so newer stateless revisions can be added without silently
changing existing connections.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import threading
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple
from urllib.error import HTTPError
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


DEFAULT_REMOTE_PROTOCOL_VERSION = "2025-06-18"
STATELESS_REMOTE_PROTOCOL_VERSION = "2026-07-28"
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024
_MAX_TOOLS = 2000


@dataclass(frozen=True)
class MCPResource:
    uri: str
    name: str
    description: str = ""
    mime_type: Optional[str] = None


@dataclass(frozen=True)
class MCPPrompt:
    name: str
    description: str = ""
    arguments: Tuple[Dict[str, Any], ...] = ()


HTTPResult = Tuple[int, Mapping[str, str], bytes]
HTTPCallable = Callable[[str, str, Mapping[str, str], Optional[bytes], float], HTTPResult]


class RemoteMCPClient:
    def __init__(
        self,
        *,
        server_name: str,
        remote: RemoteSpec,
        auth_headers: Optional[Mapping[str, str]] = None,
        protocol_version: str = DEFAULT_REMOTE_PROTOCOL_VERSION,
        default_timeout_s: float = 30.0,
        http: Optional[HTTPCallable] = None,
    ):
        if not isinstance(server_name, str) or not server_name.strip():
            raise MCPClientError("Invalid remote MCP server name")
        self._server_name = server_name.strip()
        self._remote = remote
        self._protocol_version = protocol_version
        self._timeout_s = max(0.1, min(float(default_timeout_s), 300.0))
        self._http = http or self._default_http
        self._auth_headers = dict(auth_headers or {})
        undeclared = set(self._auth_headers) - set(remote.header_names)
        if undeclared:
            raise MCPClientError("Remote auth header was not declared by connection spec")
        if any(not isinstance(value, str) or "\r" in value or "\n" in value for value in self._auth_headers.values()):
            raise MCPClientError("Invalid remote auth header value")
        self._id_counter = 0
        self._lock = threading.RLock()
        self._session_id: Optional[str] = None
        self._initialized = False
        self._closed = False
        self._server_capabilities: Dict[str, Any] = {}

    @property
    def server_name(self) -> str:
        return self._server_name

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    @property
    def is_closed(self) -> bool:
        return self._closed

    @property
    def server_capabilities(self) -> Dict[str, Any]:
        return dict(self._server_capabilities)

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": self._protocol_version,
            "User-Agent": "Lumena-MCP/1",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        headers.update(self._auth_headers)
        return headers

    @property
    def _is_stateless_protocol(self) -> bool:
        return self._protocol_version >= STATELESS_REMOTE_PROTOCOL_VERSION

    def _request_headers(self, message: Mapping[str, Any]) -> Dict[str, str]:
        headers = self._headers()
        if self._is_stateless_protocol:
            method = message.get("method")
            if isinstance(method, str) and method:
                headers["Mcp-Method"] = method
            params = message.get("params")
            if isinstance(params, dict):
                name = params.get("name", params.get("uri"))
                if isinstance(name, str) and name:
                    try:
                        name.encode("ascii")
                    except UnicodeEncodeError:
                        import base64
                        encoded = base64.b64encode(name.encode("utf-8")).decode("ascii")
                        headers["Mcp-Name"] = f":base64:{encoded}:"
                    else:
                        if "\r" not in name and "\n" not in name:
                            headers["Mcp-Name"] = name
        return headers

    def _with_request_metadata(self, message: Dict[str, Any]) -> Dict[str, Any]:
        if not self._is_stateless_protocol or "method" not in message:
            return message
        wire = dict(message)
        params = dict(wire.get("params") or {})
        meta = dict(params.get("_meta") or {})
        meta["io.modelcontextprotocol/protocolVersion"] = self._protocol_version
        meta["io.modelcontextprotocol/clientCapabilities"] = dict(
            LUMENA_CLIENT_CAPABILITIES
        )
        meta["io.modelcontextprotocol/clientInfo"] = dict(LUMENA_CLIENT_INFO)
        params["_meta"] = meta
        wire["params"] = params
        return wire

    def _next_id(self) -> int:
        self._id_counter += 1
        return self._id_counter

    def _post(
        self,
        message: Dict[str, Any],
        *,
        timeout_s: Optional[float],
        notification: bool = False,
    ) -> Optional[Dict[str, Any]]:
        if self._closed:
            raise MCPClientError("Client is closed")
        timeout = self._timeout_s if timeout_s is None else max(0.1, float(timeout_s))
        message = self._with_request_metadata(message)
        body = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(body) > _MAX_RESPONSE_BYTES:
            raise MCPProtocolError("Remote MCP request exceeds max size")
        try:
            status, response_headers, response_body = self._http(
                "POST", self._remote.url, self._request_headers(message), body, timeout
            )
        except TimeoutError as exc:
            raise MCPTimeoutError("Remote MCP request timed out") from exc
        session = response_headers.get("Mcp-Session-Id") or response_headers.get("mcp-session-id")
        if session is not None and self._is_stateless_protocol:
            raise MCPProtocolError("Stateless MCP response attempted to create a session")
        if session is not None:
            if not isinstance(session, str) or not session or any(
                ord(char) < 0x21 or ord(char) > 0x7E for char in session
            ):
                raise MCPProtocolError("Invalid MCP session id")
            self._session_id = session
        if notification:
            if status not in {200, 202, 204}:
                raise MCPProtocolError(f"Remote MCP notification rejected ({status})")
            return None
        if status not in {200, 201}:
            if status == 404 and self._session_id:
                self._session_id = None
                self._initialized = False
                raise MCPProtocolError("Remote MCP session expired; reinitialization required")
            raise MCPProtocolError(f"Remote MCP HTTP status {status}")
        if len(response_body) > _MAX_RESPONSE_BYTES:
            raise MCPProtocolError("Remote MCP response exceeds max size")
        content_type = (
            response_headers.get("Content-Type")
            or response_headers.get("content-type")
            or ""
        ).split(";", 1)[0].strip().lower()
        if content_type == "text/event-stream":
            messages = self._parse_sse(response_body)
            expected = message.get("id")
            for candidate in messages:
                if candidate.get("id") == expected:
                    return candidate
            raise MCPProtocolError("SSE response missing final JSON-RPC response")
        if content_type not in {"application/json", "application/json-rpc", ""}:
            raise MCPProtocolError("Unsupported remote MCP content type")
        try:
            decoded = json.loads(response_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MCPProtocolError("Invalid remote MCP JSON") from exc
        if not isinstance(decoded, dict):
            raise MCPProtocolError("Remote MCP response is not an object")
        return decoded

    @staticmethod
    def _parse_sse(body: bytes) -> List[Dict[str, Any]]:
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MCPProtocolError("Invalid UTF-8 SSE response") from exc
        out: List[Dict[str, Any]] = []
        data_lines: List[str] = []
        for line in text.splitlines() + [""]:
            if line == "":
                if data_lines:
                    raw = "\n".join(data_lines)
                    data_lines = []
                    try:
                        value = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise MCPProtocolError("Invalid SSE JSON data") from exc
                    if isinstance(value, dict):
                        out.append(value)
                continue
            if line.startswith(":"):
                continue
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip(" "))
        return out

    def _call(
        self,
        method: str,
        params: Optional[Dict[str, Any]] = None,
        *,
        timeout_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        with self._lock:
            request_id = self._next_id()
            message: Dict[str, Any] = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
            }
            if params is not None:
                message["params"] = params
            response = self._post(message, timeout_s=timeout_s)
        if response is None or response.get("id") != request_id:
            raise MCPProtocolError("Remote MCP response id mismatch")
        if "error" in response:
            error = response["error"]
            code = error.get("code", -1) if isinstance(error, dict) else -1
            message_text = error.get("message", "error") if isinstance(error, dict) else "error"
            raise MCPClientError(f"MCP error {code}: {message_text}")
        result = response.get("result")
        if not isinstance(result, dict):
            raise MCPProtocolError("Remote MCP response missing result")
        return result

    def _notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        message: Dict[str, Any] = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            message["params"] = params
        with self._lock:
            self._post(message, timeout_s=None, notification=True)

    def initialize(self, *, timeout_s: Optional[float] = None) -> Dict[str, Any]:
        if self._initialized:
            raise MCPClientError("Remote MCP client already initialized")
        if self._is_stateless_protocol:
            result = self._call("server/discover", {}, timeout_s=timeout_s)
            versions = result.get("supportedProtocolVersions", [])
            if versions and (
                not isinstance(versions, list)
                or self._protocol_version not in versions
            ):
                raise MCPProtocolError("Remote MCP does not support requested protocol version")
            capabilities = result.get("capabilities")
            if isinstance(capabilities, dict):
                self._server_capabilities = dict(capabilities)
            self._initialized = True
            return result
        result = self._call("initialize", {
            "protocolVersion": self._protocol_version,
            "capabilities": dict(LUMENA_CLIENT_CAPABILITIES),
            "clientInfo": dict(LUMENA_CLIENT_INFO),
        }, timeout_s=timeout_s)
        negotiated = result.get("protocolVersion")
        if negotiated is not None:
            if not isinstance(negotiated, str) or len(negotiated) > 32:
                raise MCPProtocolError("Invalid negotiated protocol version")
            self._protocol_version = negotiated
        capabilities = result.get("capabilities")
        if isinstance(capabilities, dict):
            self._server_capabilities = dict(capabilities)
        self._notify("notifications/initialized", {})
        self._initialized = True
        return result

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            raise MCPNotInitializedError("Remote MCP client is not initialized")

    def list_tools(self, *, timeout_s: Optional[float] = None) -> List[MCPTool]:
        self._ensure_initialized()
        result = self._call("tools/list", {}, timeout_s=timeout_s)
        raw_tools = result.get("tools", [])
        if not isinstance(raw_tools, list) or len(raw_tools) > _MAX_TOOLS:
            raise MCPProtocolError("Invalid remote tools list")
        out: List[MCPTool] = []
        for raw in raw_tools:
            if not isinstance(raw, dict) or not isinstance(raw.get("name"), str):
                continue
            schema = raw.get("inputSchema", {})
            out.append(MCPTool(
                name=raw["name"],
                description=raw.get("description", "") if isinstance(raw.get("description", ""), str) else "",
                input_schema=schema if isinstance(schema, dict) else {},
            ))
        return out

    def call_tool(
        self,
        name: str,
        args: Dict[str, Any],
        *,
        timeout_s: Optional[float] = None,
    ) -> MCPCallResult:
        self._ensure_initialized()
        if not isinstance(name, str) or not name or not isinstance(args, dict):
            raise MCPClientError("Invalid remote tool call")
        result = self._call(
            "tools/call", {"name": name, "arguments": args}, timeout_s=timeout_s
        )
        return MCPClient._parse_call_result(result)

    def list_resources(self, *, timeout_s: Optional[float] = None) -> List[MCPResource]:
        self._ensure_initialized()
        result = self._call("resources/list", {}, timeout_s=timeout_s)
        raw_items = result.get("resources", [])
        if not isinstance(raw_items, list) or len(raw_items) > _MAX_TOOLS:
            raise MCPProtocolError("Invalid remote resources list")
        out: List[MCPResource] = []
        for item in raw_items:
            if not isinstance(item, dict) or not isinstance(item.get("uri"), str):
                continue
            out.append(MCPResource(
                uri=item["uri"],
                name=item.get("name", item["uri"]) if isinstance(item.get("name", item["uri"]), str) else item["uri"],
                description=item.get("description", "") if isinstance(item.get("description", ""), str) else "",
                mime_type=item.get("mimeType") if isinstance(item.get("mimeType"), str) else None,
            ))
        return out

    def read_resource(self, uri: str, *, timeout_s: Optional[float] = None) -> Dict[str, Any]:
        self._ensure_initialized()
        return self._call("resources/read", {"uri": uri}, timeout_s=timeout_s)

    def list_prompts(self, *, timeout_s: Optional[float] = None) -> List[MCPPrompt]:
        self._ensure_initialized()
        result = self._call("prompts/list", {}, timeout_s=timeout_s)
        raw_items = result.get("prompts", [])
        if not isinstance(raw_items, list) or len(raw_items) > _MAX_TOOLS:
            raise MCPProtocolError("Invalid remote prompts list")
        out: List[MCPPrompt] = []
        for item in raw_items:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            args = item.get("arguments", [])
            out.append(MCPPrompt(
                name=item["name"],
                description=item.get("description", "") if isinstance(item.get("description", ""), str) else "",
                arguments=tuple(arg for arg in args if isinstance(arg, dict)) if isinstance(args, list) else (),
            ))
        return out

    def get_prompt(
        self,
        name: str,
        arguments: Optional[Dict[str, str]] = None,
        *,
        timeout_s: Optional[float] = None,
    ) -> Dict[str, Any]:
        self._ensure_initialized()
        return self._call(
            "prompts/get", {"name": name, "arguments": arguments or {}}, timeout_s=timeout_s
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._session_id and not self._is_stateless_protocol:
                try:
                    self._http("DELETE", self._remote.url, self._headers(), None, self._timeout_s)
                except Exception:
                    pass
            self._session_id = None
            self._closed = True
            self._initialized = False

    @staticmethod
    def _default_http(
        method: str,
        url: str,
        headers: Mapping[str, str],
        body: Optional[bytes],
        timeout_s: float,
    ) -> HTTPResult:
        request = Request(url, data=body, headers=dict(headers), method=method)
        try:
            with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - URL validated by RemoteSpec
                payload = response.read(_MAX_RESPONSE_BYTES + 1)
                return int(response.status), dict(response.headers.items()), payload
        except HTTPError as exc:
            return int(exc.code), dict(exc.headers.items()), exc.read(_MAX_RESPONSE_BYTES + 1)

