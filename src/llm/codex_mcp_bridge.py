"""Run-scoped MCP bridge exposing Lumena tools to Codex App Server.

The MCP subprocess is deliberately dumb: it owns no Lumena object and forwards
``tools/list`` / ``tools/call`` to an authenticated loopback server held by the
current ReAct run.  Policies, leases, mission scope and proof recording stay in
the parent process, where Lumena's live ``ToolRegistry`` remains authoritative.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import socket
import sys
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from src.reasoning.caller_context import CallerContext
from src.runtime.context import (
    RuntimeContext,
    get_current_runtime_context,
    pop_runtime_context,
    push_runtime_context,
)


MAX_BRIDGE_MESSAGE_BYTES = 2 * 1024 * 1024
MCP_PROTOCOL_VERSION = "2025-06-18"
MCP_SERVER_INSTRUCTIONS = (
    "Use only the tools exposed by this Lumena MCP server for every action. "
    "Do not use shell commands or direct file writes. Lumena enforces identity, "
    "workspace, mission scope, approvals and proof. Report only observed facts."
)
_MCP_SCHEMA_TYPES = frozenset(
    {"object", "array", "string", "number", "integer", "boolean", "null"}
)
_MCP_DESCRIPTION_RE = re.compile(r"[^A-Za-z0-9 .,\-_]+")


# LOT Z34 phase 2 — nom de l'invocateur et outils qui NE passent jamais par lui.
# `final_answer`/`ask_user` pilotent la boucle, pas le travail : les rendre
# appelables laisserait Codex court-circuiter le point d'etranglement du FINAL,
# donc le truth-lock.
INVOKE_TOOL_NAME = "invoke_tool"
_INVOKE_FORBIDDEN = frozenset({"final_answer", "ask_user", INVOKE_TOOL_NAME})


class CodexMCPBridgeError(RuntimeError):
    """Raised when the authenticated parent bridge cannot serve a request."""


@dataclass(frozen=True)
class CodexMCPBridgeEndpoint:
    host: str
    port: int
    token: str


def _mcp_safe_description(value: Any, *, fallback: str = "", limit: int = 600) -> str:
    """Return bounded plain ASCII prose accepted by Codex's MCP parser.

    ToolRegistry contains useful but historically free-form descriptions with
    JSON examples, source snippets and markup.  Some Codex App Server builds
    can stall while parsing those strings.  The MCP boundary keeps the prose
    but removes syntax-like punctuation; the authoritative registry is never
    modified.
    """

    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii")
    text = _MCP_DESCRIPTION_RE.sub(" ", text)
    text = " ".join(text.split()).strip(" .,-_")
    if not text:
        text = fallback
    return text[: max(1, int(limit))]


def _mcp_safe_schema_node(value: Any, *, root: bool = False) -> dict[str, Any]:
    """Normalize legacy ToolRegistry metadata to a conservative JSON Schema.

    Historical handlers include shorthand property descriptions, property
    level ``required: true`` flags and defaults intended for OpenAI function
    schemas.  Codex MCP validates more strictly.  Only representation metadata
    is adapted here; tool names, argument names and runtime handlers stay
    authoritative in Lumena.
    """

    if isinstance(value, str):
        return {
            "type": "string",
            "description": _mcp_safe_description(value, limit=280),
        }
    if not isinstance(value, Mapping):
        return {"type": "string"}

    output: dict[str, Any] = {}
    schema_type = value.get("type")
    if isinstance(schema_type, str) and schema_type in _MCP_SCHEMA_TYPES:
        output["type"] = schema_type
    elif root or isinstance(value.get("properties"), Mapping):
        output["type"] = "object"
    elif "items" in value:
        output["type"] = "array"
    else:
        output["type"] = "string"

    description = _mcp_safe_description(value.get("description"), limit=280)
    if description:
        output["description"] = description

    enum = value.get("enum")
    if isinstance(enum, list) and enum and all(
        item is None or isinstance(item, (str, int, float, bool)) for item in enum
    ):
        output["enum"] = list(enum)

    properties = value.get("properties")
    if isinstance(properties, Mapping):
        normalized_properties: dict[str, Any] = {}
        promoted_required: list[str] = []
        for raw_name, raw_schema in properties.items():
            name = str(raw_name)
            normalized_properties[name] = _mcp_safe_schema_node(raw_schema)
            if isinstance(raw_schema, Mapping) and raw_schema.get("required") is True:
                promoted_required.append(name)
        output["properties"] = normalized_properties

        requested = value.get("required")
        required_names = list(requested) if isinstance(requested, list) else []
        normalized_required: list[str] = []
        for raw_name in (*required_names, *promoted_required):
            name = str(raw_name)
            if name in normalized_properties and name not in normalized_required:
                normalized_required.append(name)
        if normalized_required:
            output["required"] = normalized_required

    if "items" in value:
        output["items"] = _mcp_safe_schema_node(value.get("items"))

    if output.get("type") == "object" and "properties" not in output:
        output["properties"] = {}
    return output


def _mcp_tools_from_legacy_schemas(
    schemas: Iterable[Mapping[str, Any]], allowed_names: Iterable[str]
) -> list[dict[str, Any]]:
    """Convert ToolRegistry's OpenAI function schemas to MCP tool schemas."""

    allowed = {str(name) for name in allowed_names}
    tools: list[dict[str, Any]] = []
    for schema in schemas:
        function = schema.get("function") if isinstance(schema, Mapping) else None
        if not isinstance(function, Mapping):
            continue
        name = str(function.get("name", "") or "").strip()
        if not name or name not in allowed:
            continue
        description = _mcp_safe_description(
            function.get("description"),
            fallback=f"Lumena tool {name.replace('_', ' ')}",
        )
        tools.append(
            {
                "name": name,
                "description": description,
                "inputSchema": _mcp_safe_schema_node(
                    function.get("parameters"), root=True
                ),
            }
        )
    return tools


def _observation_payload(observation: Any) -> dict[str, Any]:
    content = getattr(observation, "content", "")
    if not isinstance(content, str):
        try:
            content = json.dumps(content, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            content = str(content)
    return {
        "content": [{"type": "text", "text": content}],
        "isError": not bool(getattr(observation, "success", False)),
    }


class LumenaCodexToolBridge:
    """Authenticated loopback facade over one live Lumena ToolRegistry."""

    def __init__(
        self,
        registry: Any,
        *,
        allowed_tools: Iterable[str],
        agent_id: str,
        before_call: Callable[[], None] | None = None,
        after_call: Callable[[str, dict[str, Any], Any, float], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
        max_message_bytes: int = MAX_BRIDGE_MESSAGE_BYTES,
    ) -> None:
        self.registry = registry
        self.allowed_tools = frozenset(str(name) for name in allowed_tools if str(name))
        self.agent_id = str(agent_id or "codex-react")
        self.before_call = before_call
        self.after_call = after_call
        self.cancel_requested = cancel_requested
        self.max_message_bytes = max(4096, int(max_message_bytes))
        self._server: asyncio.AbstractServer | None = None
        self._token = ""
        self._runtime_context: RuntimeContext | None = None

    @property
    def endpoint(self) -> CodexMCPBridgeEndpoint:
        server = self._server
        if server is None or not server.sockets or not self._token:
            raise CodexMCPBridgeError("Lumena Codex tool bridge is not running")
        host, port = server.sockets[0].getsockname()[:2]
        return CodexMCPBridgeEndpoint(str(host), int(port), self._token)

    async def __aenter__(self) -> "LumenaCodexToolBridge":
        await self.start()
        return self

    async def __aexit__(self, *_args: Any) -> None:
        await self.stop()

    async def start(self) -> None:
        if self._server is not None:
            return
        self._runtime_context = get_current_runtime_context()
        self._token = secrets.token_urlsafe(32)
        self._server = await asyncio.start_server(
            self._handle_client,
            host="127.0.0.1",
            port=0,
            limit=self.max_message_bytes + 1,
        )

    async def stop(self) -> None:
        server, self._server = self._server, None
        self._token = ""
        if server is not None:
            server.close()
            await server.wait_closed()

    def _invoke_tool_schema(self) -> dict[str, Any]:
        """LOT Z34 phase 2 — la declaration de l'invocateur, en ASCII borne."""
        return {
            "name": INVOKE_TOOL_NAME,
            "description": (
                "Call ANY Lumena tool by name, including tools absent from this "
                "run's declared list. Use discover_tools first to find the exact "
                "name and its parameters, then call this with that name. Runs "
                "through the same execution path as declared tools."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Exact tool name, e.g. generate_website.",
                    },
                    "arguments": {
                        "type": "object",
                        "description": "Arguments for that tool.",
                    },
                },
                "required": ["name"],
            },
        }

    def tools(self) -> list[dict[str, Any]]:
        declared = _mcp_tools_from_legacy_schemas(
            self.registry.get_tools_schema(), self.allowed_tools
        )
        # LOT Z34 phase 2 — toujours en dernier : le jeu contextuel reste la
        # suggestion principale, l'invocateur n'est que la porte de sortie.
        declared.append(self._invoke_tool_schema())
        return declared

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        response: dict[str, Any]
        try:
            raw = await reader.readline()
            if not raw or len(raw) > self.max_message_bytes:
                raise CodexMCPBridgeError("invalid bridge message size")
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict):
                raise CodexMCPBridgeError("bridge request must be an object")
            supplied = str(request.get("token", "") or "")
            if not hmac.compare_digest(supplied, self._token):
                raise CodexMCPBridgeError("bridge authentication failed")
            response = await self._dispatch(request)
        except Exception as exc:
            response = {"ok": False, "error": str(exc)[:1000]}
        try:
            payload = (json.dumps(response, ensure_ascii=False, default=str) + "\n").encode(
                "utf-8"
            )
            writer.write(payload[: self.max_message_bytes])
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _dispatch(self, request: Mapping[str, Any]) -> dict[str, Any]:
        operation = str(request.get("op", "") or "")
        if operation == "list":
            return {"ok": True, "tools": self.tools()}
        if operation != "call":
            raise CodexMCPBridgeError(f"unsupported bridge operation: {operation}")
        name = str(request.get("name", "") or "").strip()
        arguments = request.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise CodexMCPBridgeError("tool arguments must be an object")
        # LOT Z34 phase 2 — l'invocateur que Codex a cherche en vain.
        #
        # Run du 21/08 a 04:50:44, sa requete mot pour mot :
        #   discover_tools(« outil generique pour appeler ou invoquer un outil
        #                   decouvert par son nom »)
        # Il n'existait pas. `discover_tools` lui montre les 732 outils indexes,
        # mais le pont ne DECLARE que le jeu contextuel (260 ici) — tout le
        # reste etait visible et inappelable.
        #
        # Cote API, le filtre n'est qu'une indication : le registre execute
        # quand meme, en journalisant « hors filtre prompt — soft-filter »
        # (deux fois dans ce meme run). Cet invocateur retablit la symetrie
        # SANS declarer les 597 outils, ce qui couterait 78 k tokens par tour.
        #
        # ⚠️ Il passe par le MEME chemin que tout le reste — meme `before_call`,
        # meme `registry.execute`, meme `after_call` qui alimente history ET
        # ledger. Ce n'est pas une porte derobee : le truth-lock a prouve cette
        # nuit qu'il fonctionne sur Codex, il serait absurde de le contourner
        # en reparant l'acces aux outils.
        if name == INVOKE_TOOL_NAME:
            inner = str(arguments.get("name", "") or "").strip()
            if not inner:
                raise CodexMCPBridgeError("invoke_tool requires a tool name")
            if inner in _INVOKE_FORBIDDEN:
                raise CodexMCPBridgeError(
                    f"tool cannot be invoked through invoke_tool: {inner}"
                )
            inner_args = arguments.get("arguments") or {}
            if isinstance(inner_args, str):
                try:
                    inner_args = json.loads(inner_args)
                except Exception:
                    raise CodexMCPBridgeError(
                        "invoke_tool arguments must be an object or a JSON object string"
                    )
            if not isinstance(inner_args, dict):
                raise CodexMCPBridgeError("invoke_tool arguments must be an object")
            name, arguments = inner, dict(inner_args)
        elif name not in self.allowed_tools:
            raise CodexMCPBridgeError(f"tool is outside this run scope: {name}")
        if self.cancel_requested is not None and self.cancel_requested():
            raise CodexMCPBridgeError("Lumena task cancellation requested")
        if self.before_call is not None:
            self.before_call()
        caller = CallerContext(kind="react", agent_id=self.agent_id)
        token = None
        if self._runtime_context is not None:
            token = push_runtime_context(self._runtime_context)
        started = asyncio.get_running_loop().time()
        try:
            observation = await self.registry.execute(name, arguments, caller=caller)
        finally:
            if token is not None:
                pop_runtime_context(token)
        duration = asyncio.get_running_loop().time() - started
        if self.after_call is not None:
            self.after_call(name, dict(arguments), observation, duration)
        return {"ok": True, "result": _observation_payload(observation)}


def _bridge_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    host = os.environ.get("LUMENA_CODEX_BRIDGE_HOST", "127.0.0.1")
    port = int(os.environ["LUMENA_CODEX_BRIDGE_PORT"])
    token = os.environ["LUMENA_CODEX_BRIDGE_TOKEN"]
    request = {"token": token, **dict(payload)}
    encoded = (json.dumps(request, ensure_ascii=False) + "\n").encode("utf-8")
    if len(encoded) > MAX_BRIDGE_MESSAGE_BYTES:
        raise CodexMCPBridgeError("outgoing bridge request is too large")
    with socket.create_connection((host, port), timeout=30) as connection:
        connection.sendall(encoded)
        stream = connection.makefile("rb")
        raw = stream.readline(MAX_BRIDGE_MESSAGE_BYTES + 1)
    if not raw or len(raw) > MAX_BRIDGE_MESSAGE_BYTES:
        raise CodexMCPBridgeError("invalid bridge response size")
    response = json.loads(raw.decode("utf-8"))
    if not isinstance(response, dict) or not response.get("ok"):
        raise CodexMCPBridgeError(str((response or {}).get("error", "bridge failed")))
    return response


def _mcp_result(request: Mapping[str, Any]) -> dict[str, Any] | None:
    method = str(request.get("method", "") or "")
    request_id = request.get("id")
    if method.startswith("notifications/"):
        return None
    if method == "initialize":
        params = request.get("params") or {}
        requested = params.get("protocolVersion") if isinstance(params, Mapping) else None
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": str(requested or MCP_PROTOCOL_VERSION),
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "lumena-tools", "version": "1.0"},
                "instructions": MCP_SERVER_INSTRUCTIONS,
            },
        }
    if method == "ping":
        result: Any = {}
    elif method == "tools/list":
        result = {"tools": _bridge_request({"op": "list"})["tools"]}
    elif method == "tools/call":
        params = request.get("params") or {}
        if not isinstance(params, Mapping):
            raise CodexMCPBridgeError("tools/call params must be an object")
        result = _bridge_request(
            {
                "op": "call",
                "name": str(params.get("name", "") or ""),
                "arguments": params.get("arguments") or {},
            }
        )["result"]
    elif method in {"resources/list", "prompts/list"}:
        result = {"resources" if method.startswith("resources") else "prompts": []}
    else:
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": -32601, "message": f"Method not found: {method}"},
        }
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def run_stdio_mcp() -> int:
    """Serve newline-delimited MCP JSON-RPC on stdio without extra packages."""

    for raw in sys.stdin.buffer:
        request: dict[str, Any] | None = None
        if len(raw) > MAX_BRIDGE_MESSAGE_BYTES:
            continue
        response: dict[str, Any] | None = None
        try:
            request = json.loads(raw.decode("utf-8"))
            if not isinstance(request, dict):
                raise CodexMCPBridgeError("JSON-RPC request must be an object")
            response = _mcp_result(request)
        except Exception as exc:
            response = {
                "jsonrpc": "2.0",
                "id": request.get("id") if isinstance(request, dict) else None,
                "error": {"code": -32000, "message": str(exc)[:1000]},
            }
        if response is not None:
            # MCP STDIO is UTF-8. Write bytes directly so a Windows child
            # launched with a legacy console encoding cannot crash on Lumena's
            # accented text or emoji and surface a misleading "transport closed".
            payload = (json.dumps(response, ensure_ascii=False) + "\n").encode("utf-8")
            sys.stdout.buffer.write(payload)
            sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(run_stdio_mcp())
