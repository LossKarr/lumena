"""Regression tests for the dedicated runtime MCP tool category."""

from unittest.mock import Mock

import pytest

from src.mcp.client import MCPTool
from src.mcp.handler_adapter import adapt_tool
from src.mcp.policy import MCPPolicy
from src.mcp.react_integration import (
    CAPABILITY_TOOL_NAME,
    MCPReActIntegration,
    MCPReActIntegrationDeps,
    MCP_LOOP_CATEGORY,
    RESUME_TASK_TOOL_NAME,
    RUN_AUTONOMY_TOOL_NAME,
    TICKET_TOOL_NAME,
)
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.registry_v2 import HandlerDef
from src.reasoning.tool_categories import get_category_contract, get_semantic_category
from src.reasoning.tool_registry import ToolRegistry


def test_mcp_category_contract_is_unified_phase_d():
    """Phase D : le contrat `mcp` est unifie ; `mcp_loop_integration` n'existe
    plus comme contrat distinct mais reste accessible via back-compat."""
    mcp = get_category_contract("mcp")
    loop = get_category_contract(MCP_LOOP_CATEGORY)

    assert mcp is not None
    assert loop is not None
    # MCP_LOOP_CATEGORY pointe maintenant vers le contrat unifie "mcp"
    assert MCP_LOOP_CATEGORY == "mcp"
    assert loop.name == mcp.name == "mcp"
    # Back-compat : ancienne cle de module → contrat unifie
    assert get_category_contract("mcp_loop_integration") is mcp


def test_mcp_category_contract_phase_d_enriched_doctrine():
    contract = get_category_contract("mcp")

    assert contract is not None
    # Phase D : 5 preconditions, autonomy_allowed=True (orchestration MCP).
    assert len(contract.preconditions) >= 5
    assert any("ACTIVE" in p for p in contract.preconditions)
    assert any("trust_score" in p for p in contract.preconditions)
    assert contract.autonomy_allowed is True
    assert contract.requires_workspace is False
    assert any(
        "policy" in reason.lower() or "approval" in reason.lower()
        for reason in contract.refusal_reasons
    )


def test_mcp_semantic_category_is_unified():
    assert get_semantic_category("mcp") == "mcp"
    # Phase D : ancienne cle mappe vers la categorie unifiee "mcp".
    assert get_semantic_category("mcp_loop_integration") == "mcp"
    assert get_semantic_category(MCP_LOOP_CATEGORY) == "mcp"


def test_handler_adapter_defaults_real_mcp_tools_to_mcp_category():
    client = Mock()
    tool = MCPTool(
        name="search",
        description="Search external data",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )

    handler_def = adapt_tool(client=client, server_name="github", mcp_tool=tool)

    assert handler_def.name == "mcp__github__search"
    assert handler_def.category == "mcp"
    assert handler_def.source_module == "mcp.github"


def test_dynamic_mcp_handler_keeps_mcp_category_in_tool_modules():
    registry = ToolRegistry()

    async def _handler(ctx, **kwargs):
        return HandlerResult.ok(output="ok")

    handler_def = HandlerDef(
        name="mcp__github__search",
        description="[MCP/github] search",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=_handler,
        category="mcp",
        source_module="mcp.github",
    )

    registry.register_dynamic_handler(handler_def, policy=MCPPolicy.READ_ONLY)
    try:
        assert registry._tool_modules["mcp__github__search"] == "mcp"
        assert registry.get_tool_module_category("mcp__github__search") == "mcp"
        assert registry.get_tool_semantic_category("mcp__github__search") == "mcp"
    finally:
        registry.unregister_dynamic_handler("mcp__github__search")


def test_phase26_loop_tools_unified_under_mcp_category_phase_d():
    """Phase D : les 4 outils Phase 26 partagent maintenant la categorie
    sematique "mcp" (etait "mcp_loop_integration")."""
    registry = ToolRegistry()
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))

    attached, reason = integration.attach_to_tool_registry(registry)

    assert (attached, reason) == (True, "attached")
    # MCP_LOOP_CATEGORY est maintenant == "mcp" (constant alias preserve).
    assert MCP_LOOP_CATEGORY == "mcp"
    assert registry._tool_modules[CAPABILITY_TOOL_NAME] == "mcp"
    assert registry._tool_modules[TICKET_TOOL_NAME] == "mcp"
    assert registry._tool_modules[RUN_AUTONOMY_TOOL_NAME] == "mcp"
    assert registry._tool_modules[RESUME_TASK_TOOL_NAME] == "mcp"
    # Les Phase 26 tools restent natifs (pas dynamiques).
    assert not registry.is_dynamic_handler(CAPABILITY_TOOL_NAME)
    assert not registry.is_dynamic_handler(TICKET_TOOL_NAME)
    assert not registry.is_dynamic_handler(RUN_AUTONOMY_TOOL_NAME)
    assert not registry.is_dynamic_handler(RESUME_TASK_TOOL_NAME)


def test_mcp_context_filter_uses_real_mcp_category_for_active_mcp_tools():
    registry = ToolRegistry()

    async def _handler(ctx, **kwargs):
        return HandlerResult.ok(output="ok")

    handler_def = HandlerDef(
        name="mcp__github__search",
        description="[MCP/github] search",
        parameters={},
        handler=_handler,
        category="mcp",
        source_module="mcp.github",
    )
    registry.register_dynamic_handler(handler_def, policy=MCPPolicy.READ_ONLY)
    try:
        registry.apply_context_filter("utilise le MCP actif github")
        assert registry._allowed_tools is not None
        assert "mcp__github__search" in registry._allowed_tools
    finally:
        registry.unregister_dynamic_handler("mcp__github__search")


def test_mcp_install_context_filter_exposes_all_mcp_tools_phase_d():
    """Phase D : sous le contrat unifie, "installe un MCP" expose la fois
    les outils Phase 26 (boucle) ET les outils MCP actifs (categorie "mcp").
    Plus de cloisonnement loop_integration vs mcp."""
    registry = ToolRegistry()
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))
    integration.attach_to_tool_registry(registry)

    async def _handler(ctx, **kwargs):
        return HandlerResult.ok(output="ok")

    handler_def = HandlerDef(
        name="mcp__github__search",
        description="[MCP/github] search",
        parameters={},
        handler=_handler,
        category="mcp",
        source_module="mcp.github",
    )
    registry.register_dynamic_handler(handler_def, policy=MCPPolicy.READ_ONLY)
    try:
        registry.apply_context_filter("installe un MCP pour github")
        assert registry._allowed_tools is not None
        # Outils Phase 26 (boucle MCP) visibles
        assert CAPABILITY_TOOL_NAME in registry._allowed_tools
        assert TICKET_TOOL_NAME in registry._allowed_tools
        assert RUN_AUTONOMY_TOOL_NAME in registry._allowed_tools
        assert RESUME_TASK_TOOL_NAME in registry._allowed_tools
        # Phase D : outils MCP actifs aussi visibles (contrat unifie)
        assert "mcp__github__search" in registry._allowed_tools
    finally:
        registry.unregister_dynamic_handler("mcp__github__search")


# ══════════════════════════════════════════════════════════════════════════════
# Phase D — signature : preuves de suppression `mcp_loop_integration`
# ══════════════════════════════════════════════════════════════════════════════


def test_phase_d_mcp_loop_integration_removed_from_contracts():
    """Le contrat `mcp_loop_integration` est supprime du dict _CONTRACTS.
    L'acces via get_category_contract redirige vers le contrat unifie `mcp`
    grace au mapping back-compat de _MODULE_TO_SEMANTIC."""
    from src.reasoning.tool_categories import _CONTRACTS

    assert "mcp_loop_integration" not in _CONTRACTS
    assert "mcp" in _CONTRACTS
    # Back-compat : ancienne cle resolue vers le contrat unifie.
    redirected = get_category_contract("mcp_loop_integration")
    assert redirected is not None
    assert redirected.name == "mcp"


def test_phase_d_mcp_loop_category_constant_is_mcp():
    """La constante exportee depuis react_integration pointe vers "mcp"
    apres Phase D — back-compat pour le code externe."""
    assert MCP_LOOP_CATEGORY == "mcp"


def test_phase_d_mcp_contract_doctrine_completeness():
    """Le contrat enrichi Phase D contient exactement les 5 preconditions,
    4 effets autorises et 5 raisons de refus du plan."""
    contract = get_category_contract("mcp")
    assert contract is not None
    assert len(contract.preconditions) == 5
    assert len(contract.allowed_effects) == 4
    assert len(contract.refusal_reasons) == 5
    # Marqueurs cle du plan
    assert any("Catalog" in p for p in contract.preconditions)
    assert any("JSON-RPC" in e or "stdio" in e for e in contract.allowed_effects)
    assert any("approval" in r.lower() for r in contract.refusal_reasons)


def test_phase_d_module_alias_mapping_resolves_to_mcp():
    """Tout module historiquement classe `mcp_loop_integration` mappe
    maintenant vers la categorie sematique unifiee `mcp`."""
    assert get_semantic_category("mcp_loop_integration") == "mcp"
    assert get_semantic_category("mcp") == "mcp"
