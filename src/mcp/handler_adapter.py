"""
handler_adapter.py — Adaptateur MCPTool → HandlerDef V2 (Phase 7).

Conversion d'un outil MCP en HandlerDef enregistrable dans le ToolRegistry
Lumena. PAS d'enregistrement effectif dans le registry — c'est Phase 8.

Garanties :
  - Namespace strict : `mcp__{server}__{tool}` (anti-collision avec outils natifs)
  - Description préfixée : `[MCP/{server}] ...`
  - source_module : `mcp.{server}`
  - Handler async wrapper : appelle MCPClient.call_tool() via asyncio.to_thread
    (le client est subprocess sync → évite blocage event loop)
  - Catch des exceptions MCP → HandlerResult.fail (jamais raise vers ReAct)
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Callable, Dict, List, Optional

from src.mcp.client import (
    MCPClient,
    MCPClientError,
    MCPProtocolError,
    MCPTimeoutError,
    MCPTool,
)
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.registry_v2 import HandlerDef


_NAME_RE = re.compile(r"^[A-Za-z0-9_\-.]+$")
_HANDLER_NAME_MAX_LEN = 128


def _validate_server_name(server_name: str) -> None:
    if not isinstance(server_name, str) or not server_name:
        raise ValueError(f"Invalid server_name: {server_name!r}")
    if not _NAME_RE.match(server_name):
        raise ValueError(
            f"server_name must match [A-Za-z0-9_-.]+: {server_name!r}"
        )


def _validate_tool_name(tool_name: str) -> None:
    if not isinstance(tool_name, str) or not tool_name:
        raise ValueError(f"Invalid tool_name: {tool_name!r}")
    if not _NAME_RE.match(tool_name):
        raise ValueError(
            f"tool_name must match [A-Za-z0-9_-.]+: {tool_name!r}"
        )


def make_handler_name(server_name: str, tool_name: str) -> str:
    """Construit `mcp__{server}__{tool}` après validation."""
    _validate_server_name(server_name)
    _validate_tool_name(tool_name)
    candidate = f"mcp__{server_name}__{tool_name}"
    if len(candidate) > _HANDLER_NAME_MAX_LEN:
        raise ValueError(
            f"Handler name too long ({len(candidate)} > {_HANDLER_NAME_MAX_LEN}): "
            f"{candidate!r}"
        )
    return candidate


def _convert_input_schema(input_schema: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Convertit le inputSchema MCP en format HandlerDef.parameters.

    HandlerDef.parameters est volontairement un Dict[str, Any] ouvert.
    On conserve la structure JSON Schema standard (cohérent avec le reste
    de Lumena qui passe schemas JSON Schema au LLM).

    Si input_schema est absent ou invalide → dict vide.
    """
    if not isinstance(input_schema, dict):
        return {}
    return dict(input_schema)


def _stringify_content(content: Any) -> str:
    """Convertit content MCP en string pour HandlerResult.

    MCP content est typiquement :
      - list[{type:"text", text:"..."}]  → concatène les text
      - str → tel quel
      - dict / list autre → json.dumps
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: List[str] = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "text":
                    text = item.get("text")
                    if isinstance(text, str):
                        texts.append(text)
                        continue
            try:
                texts.append(json.dumps(item, ensure_ascii=False))
            except (TypeError, ValueError):
                texts.append(str(item))
        return "\n".join(texts)
    if content is None:
        return ""
    try:
        return json.dumps(content, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(content)


def _make_wrapped_handler(
    *,
    client: MCPClient,
    server_name: str,
    tool_name: str,
    handler_name: str,
    timeout_s: Optional[float] = None,
) -> Callable:
    """Construit l'async handler wrapper qui appelle MCPClient.call_tool.

    Le wrapper :
      - appelle client.call_tool via asyncio.to_thread (subprocess sync)
      - catch toutes les exceptions MCP → HandlerResult.fail
      - serialise content MCP en string pour HandlerResult.ok
    """

    async def _handler(ctx, **kwargs) -> HandlerResult:
        try:
            result = await asyncio.to_thread(
                client.call_tool, tool_name, kwargs, timeout_s=timeout_s
            )
        except MCPTimeoutError as e:
            return HandlerResult.fail(
                f"MCP timeout: {e}",
                handler_name=handler_name,
            )
        except MCPProtocolError as e:
            return HandlerResult.fail(
                f"MCP protocol error: {e}",
                handler_name=handler_name,
            )
        except MCPClientError as e:
            return HandlerResult.fail(
                f"MCP error: {e}",
                handler_name=handler_name,
            )
        except Exception as e:  # noqa: BLE001
            return HandlerResult.fail(
                f"Unexpected error calling MCP tool {tool_name!r}: {e}",
                handler_name=handler_name,
            )

        if result.is_error:
            message = result.error_message or "MCP tool returned isError=true"
            return HandlerResult.fail(message, handler_name=handler_name)

        output = _stringify_content(result.content)
        if result.structured_content is not None:
            # Optionnel : annexe la version structurée si présente
            try:
                output = (
                    output
                    + "\n\n[structuredContent]\n"
                    + json.dumps(result.structured_content, ensure_ascii=False)
                )
            except (TypeError, ValueError):
                pass
        return HandlerResult.ok(output, handler_name=handler_name)

    return _handler


def adapt_tool(
    *,
    client: MCPClient,
    server_name: str,
    mcp_tool: MCPTool,
    category: str = "mcp",
    timeout_s: Optional[float] = None,
    cached_category: Optional[str] = None,
    llm_callable: Optional[Callable[[str], str]] = None,
    all_tool_descriptions: Optional[List[str]] = None,
) -> HandlerDef:
    """Convertit un MCPTool en HandlerDef V2.

    Args:
        client: instance MCPClient déjà initialisée
        server_name: nom du serveur MCP (utilisé pour namespace + source_module)
        mcp_tool: MCPTool à adapter
        category: catégorie HandlerDef (défaut "mcp", cf Phase 9 policy engine)
        timeout_s: timeout par appel (passé au wrapper)
        cached_category: Phase C — si fourni, court-circuite la cascade
            (résultat déjà stocké dans le Catalog Phase 14).
        llm_callable: Phase C — LLM optionnel pour cascade niveau 3.
        all_tool_descriptions: Phase C — descriptions agrégées des tools
            frères du même serveur (améliore l'heuristique niveau 2).

    Cascade Phase C (opt-in) : déclenchée UNIQUEMENT si `category="mcp"`
    (défaut) ET au moins un des kwargs cascade (`cached_category`,
    `llm_callable`, `all_tool_descriptions`) est fourni. Sans aucun de ces
    signaux, comportement back-compat : `category` est passé tel quel.

    Returns:
        HandlerDef prêt à être enregistré dans ToolRegistry (Phase 8).
    """
    if not isinstance(mcp_tool, MCPTool):
        raise ValueError("mcp_tool must be an MCPTool instance")

    # Phase C — cascade opt-in : on n'active la résolution sémantique que si
    # le caller signale qu'il a un contexte cascade (cache/LLM/desc agrégées).
    # Sinon, on respecte strictement la `category` passée (back-compat).
    if category == "mcp" and (
        cached_category is not None
        or llm_callable is not None
        or all_tool_descriptions is not None
    ):
        from src.mcp.category_inference import infer_semantic_category
        descriptions = (
            list(all_tool_descriptions)
            if all_tool_descriptions is not None
            else [mcp_tool.description or ""]
        )
        category, _decision_source = infer_semantic_category(
            server_name=server_name,
            tool_descriptions=descriptions,
            llm_callable=llm_callable,
            cached=cached_category,
        )

    handler_name = make_handler_name(server_name, mcp_tool.name)

    raw_desc = mcp_tool.description.strip() if mcp_tool.description else ""
    description = f"[MCP/{server_name}] {raw_desc}".strip()
    if description.endswith("[MCP/{}]".format(server_name)):
        description = f"[MCP/{server_name}]"

    parameters = _convert_input_schema(mcp_tool.input_schema)

    handler = _make_wrapped_handler(
        client=client,
        server_name=server_name,
        tool_name=mcp_tool.name,
        handler_name=handler_name,
        timeout_s=timeout_s,
    )

    return HandlerDef(
        name=handler_name,
        description=description,
        parameters=parameters,
        handler=handler,
        category=category,
        source_module=f"mcp.{server_name}",
    )


def adapt_tools(
    *,
    client: MCPClient,
    server_name: str,
    mcp_tools: List[MCPTool],
    category: str = "mcp",
    timeout_s: Optional[float] = None,
) -> List[HandlerDef]:
    """Adapt en lot. Identique à `adapt_tool` en boucle.

    Les outils qui lèvent ValueError (nom invalide, etc.) sont SKIPPÉS,
    pas plantent la liste entière.
    """
    out: List[HandlerDef] = []
    for tool in mcp_tools:
        try:
            out.append(
                adapt_tool(
                    client=client,
                    server_name=server_name,
                    mcp_tool=tool,
                    category=category,
                    timeout_s=timeout_s,
                )
            )
        except (ValueError, TypeError):
            # Skip silencieusement les tools invalides
            continue
    return out
