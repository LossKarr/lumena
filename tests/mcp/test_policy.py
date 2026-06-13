"""
Tests Phase 9 — Policy Engine MCP.

Vérifie :
  - 6 sous-catégories MCPPolicy
  - Sets BLOCKED / ALLOWED Phase 9
  - is_blocked_phase9 / is_allowed_phase9 / accesseurs
  - Enforcement dans register_dynamic_handler (policy obligatoire)
  - Enforcement dans execute() : bloque IRREVERSIBLE/SECRETS/LOCAL_WRITE/RECOVERABLE
  - Autorise READ_ONLY / EXTERNAL_READ
  - Skip natifs (non-MCP)
  - Invariant : dynamic handler sans policy → bloqué
  - Rollback : unregister nettoie policy
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from src.mcp.policy import (
    MCPPolicy,
    allowed_policies_phase9,
    blocked_policies_phase9,
    is_allowed_phase9,
    is_blocked_phase9,
)
from src.reasoning.caller_context import REACT, UNKNOWN
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.registry_v2 import HandlerDef
from src.reasoning.tool_registry import DynamicRegistryError, ToolRegistry


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


def _make_handler_def(
    name: str = "mcp__test__tool",
    handler=None,
) -> HandlerDef:
    if handler is None:
        async def _default(ctx, **kwargs):
            return HandlerResult.ok(output=f"called:{kwargs}")
        handler = _default
    return HandlerDef(
        name=name,
        description="test",
        parameters={"type": "object", "properties": {}},
        handler=handler,
        category="mcp",
        source_module="mcp.test",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Enum & sets
# ──────────────────────────────────────────────────────────────────────────────


def test_mcp_policy_enum_has_6_values():
    values = {p.value for p in MCPPolicy}
    assert len(MCPPolicy) == 6
    expected = {
        "read_only",
        "local_write",
        "external_read",
        "external_write_recoverable",
        "external_write_irreversible",
        "secrets_auth",
    }
    assert values == expected


def test_blocked_set_phase9_contains_4_categories():
    blocked = blocked_policies_phase9()
    assert blocked == frozenset({
        MCPPolicy.LOCAL_WRITE,
        MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
        MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE,
        MCPPolicy.SECRETS_AUTH,
    })


def test_allowed_set_phase9_contains_2_categories():
    allowed = allowed_policies_phase9()
    assert allowed == frozenset({
        MCPPolicy.READ_ONLY,
        MCPPolicy.EXTERNAL_READ,
    })


def test_blocked_and_allowed_disjoint():
    """Aucune intersection : chaque policy est soit allowed soit blocked."""
    blocked = blocked_policies_phase9()
    allowed = allowed_policies_phase9()
    assert blocked.isdisjoint(allowed)
    assert blocked | allowed == set(MCPPolicy)


@pytest.mark.parametrize(
    "policy,expected",
    [
        (MCPPolicy.READ_ONLY, False),
        (MCPPolicy.EXTERNAL_READ, False),
        (MCPPolicy.LOCAL_WRITE, True),
        (MCPPolicy.EXTERNAL_WRITE_RECOVERABLE, True),
        (MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE, True),
        (MCPPolicy.SECRETS_AUTH, True),
    ],
)
def test_is_blocked_phase9_discrimination(policy, expected):
    assert is_blocked_phase9(policy) is expected


@pytest.mark.parametrize(
    "policy,expected",
    [
        (MCPPolicy.READ_ONLY, True),
        (MCPPolicy.EXTERNAL_READ, True),
        (MCPPolicy.LOCAL_WRITE, False),
        (MCPPolicy.EXTERNAL_WRITE_RECOVERABLE, False),
        (MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE, False),
        (MCPPolicy.SECRETS_AUTH, False),
    ],
)
def test_is_allowed_phase9_discrimination(policy, expected):
    assert is_allowed_phase9(policy) is expected


# ──────────────────────────────────────────────────────────────────────────────
# register avec policy obligatoire
# ──────────────────────────────────────────────────────────────────────────────


def test_register_requires_policy_kwarg(registry):
    """Sans policy → TypeError (kwarg-only obligatoire au niveau signature)."""
    hdef = _make_handler_def(name="mcp__test__no_policy")
    with pytest.raises(TypeError):
        # policy est kwarg-only → TypeError si manquant (avant validation runtime)
        registry.register_dynamic_handler(hdef)  # type: ignore


def test_register_rejects_non_mcppolicy_type(registry):
    hdef = _make_handler_def(name="mcp__test__bad_type")
    with pytest.raises(DynamicRegistryError, match="MCPPolicy"):
        registry.register_dynamic_handler(hdef, policy="read_only")  # type: ignore
    with pytest.raises(DynamicRegistryError, match="MCPPolicy"):
        registry.register_dynamic_handler(hdef, policy=42)  # type: ignore
    with pytest.raises(DynamicRegistryError, match="MCPPolicy"):
        registry.register_dynamic_handler(hdef, policy=None)  # type: ignore


def test_register_stores_policy_correctly(registry):
    hdef = _make_handler_def(name="mcp__test__store_policy")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    try:
        assert registry.get_dynamic_handler_policy("mcp__test__store_policy") == MCPPolicy.READ_ONLY
    finally:
        registry.unregister_dynamic_handler("mcp__test__store_policy")


def test_get_policy_unknown_returns_none(registry):
    assert registry.get_dynamic_handler_policy("nonexistent_xyz") is None


def test_get_policy_for_native_handler_returns_none(registry):
    """Pour un handler natif, get_dynamic_handler_policy retourne None."""
    native = next(iter(registry._native_handler_names))
    assert registry.get_dynamic_handler_policy(native) is None


# ──────────────────────────────────────────────────────────────────────────────
# Execute avec policy autorisée
# ──────────────────────────────────────────────────────────────────────────────


def _register_with_policy(registry, name, policy):
    """Helper qui register + retourne fonction cleanup."""
    hdef = _make_handler_def(name=name)
    registry.register_dynamic_handler(hdef, policy=policy)
    return lambda: registry.unregister_dynamic_handler(name)


@pytest.mark.asyncio
async def test_execute_read_only_handler_allowed(registry):
    cleanup = _register_with_policy(
        registry, "mcp__test__read_only_exec", MCPPolicy.READ_ONLY,
    )
    try:
        obs = await registry.execute(
            "mcp__test__read_only_exec", {}, caller=REACT,
        )
        assert obs.success is True
        assert "BLOCKED" not in (obs.content or "")
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_execute_external_read_allowed(registry):
    cleanup = _register_with_policy(
        registry, "mcp__test__ext_read_exec", MCPPolicy.EXTERNAL_READ,
    )
    try:
        obs = await registry.execute(
            "mcp__test__ext_read_exec", {}, caller=REACT,
        )
        assert obs.success is True
    finally:
        cleanup()


# ──────────────────────────────────────────────────────────────────────────────
# Execute avec policy BLOQUÉE Phase 9
# Fix T (Phase I-7) : le blocage WRITE est désormais levable via double opt-in
# env (LUMENA_MCP_LIVE=1 + LUMENA_MCP_TRUST_LIVE=1). Ces tests vérifient le
# comportement SANS opt-in → on neutralise explicitement les flags (l'env
# développeur peut les avoir à 1 via .env).
# ──────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def no_write_lift(monkeypatch):
    monkeypatch.delenv("LUMENA_MCP_LIVE", raising=False)
    monkeypatch.delenv("LUMENA_MCP_TRUST_LIVE", raising=False)


@pytest.mark.asyncio
async def test_execute_local_write_blocked_phase9(registry, no_write_lift):
    cleanup = _register_with_policy(
        registry, "mcp__test__local_write_block", MCPPolicy.LOCAL_WRITE,
    )
    try:
        obs = await registry.execute(
            "mcp__test__local_write_block", {}, caller=REACT,
        )
        assert obs.success is False
        assert "BLOCKED" in (obs.content or "") or "local_write" in (obs.content or "").lower()
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_execute_external_write_recoverable_blocked_phase9(
    registry, no_write_lift,
):
    cleanup = _register_with_policy(
        registry,
        "mcp__test__recoverable_block",
        MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
    )
    try:
        obs = await registry.execute(
            "mcp__test__recoverable_block", {}, caller=REACT,
        )
        assert obs.success is False
        assert "recoverable" in (obs.content or "").lower() or "BLOCKED" in (obs.content or "")
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_execute_external_write_irreversible_blocked(
    registry, no_write_lift,
):
    cleanup = _register_with_policy(
        registry,
        "mcp__test__irreversible_block",
        MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE,
    )
    try:
        obs = await registry.execute(
            "mcp__test__irreversible_block", {}, caller=REACT,
        )
        assert obs.success is False
        assert "irreversible" in (obs.content or "").lower() or "BLOCKED" in (obs.content or "")
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_execute_secrets_auth_blocked(registry, monkeypatch):
    """SECRETS_AUTH : bloquée MÊME avec double opt-in (jamais levable)."""
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    cleanup = _register_with_policy(
        registry, "mcp__test__secrets_block", MCPPolicy.SECRETS_AUTH,
    )
    try:
        obs = await registry.execute(
            "mcp__test__secrets_block", {}, caller=REACT,
        )
        assert obs.success is False
        assert "secrets" in (obs.content or "").lower() or "BLOCKED" in (obs.content or "")
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_blocked_refusal_message_mentions_lift_or_approval(
    registry, no_write_lift,
):
    cleanup = _register_with_policy(
        registry,
        "mcp__test__msg_phase10",
        MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE,
    )
    try:
        obs = await registry.execute(
            "mcp__test__msg_phase10", {}, caller=REACT,
        )
        msg = (obs.content or "").lower()
        # Fix T : le message doit expliquer COMMENT lever le blocage
        assert (
            "lumena_mcp_trust_live" in msg
            or "approval" in msg
            or "opt-in" in msg
        )
    finally:
        cleanup()


@pytest.mark.asyncio
async def test_fix_t_write_lifted_with_double_optin(registry, monkeypatch):
    """Fix T : avec double opt-in, un WRITE s'exécute réellement."""
    monkeypatch.setenv("LUMENA_MCP_LIVE", "1")
    monkeypatch.setenv("LUMENA_MCP_TRUST_LIVE", "1")
    cleanup = _register_with_policy(
        registry,
        "mcp__test__write_lifted",
        MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
    )
    try:
        obs = await registry.execute(
            "mcp__test__write_lifted", {}, caller=REACT,
        )
        # Le handler factice répond OK : la policy n'a pas refusé.
        assert obs.success is True
    finally:
        cleanup()


# ──────────────────────────────────────────────────────────────────────────────
# Skip pour handlers natifs
# ──────────────────────────────────────────────────────────────────────────────


def test_mcp_policy_check_skipped_for_native_handlers(registry):
    """_mcp_policy_check retourne None pour les handlers natifs."""
    native = next(iter(registry._native_handler_names))
    result = registry._mcp_policy_check(native, REACT)
    assert result is None


def test_mcp_policy_check_native_no_policy_required(registry):
    """Un handler natif n'a pas besoin de policy — exec OK même sans entrée
    dans _dynamic_policies."""
    native = next(iter(registry._native_handler_names))
    # Vérifie qu'il n'est pas dans _dynamic_policies (cohérence)
    assert native not in registry._dynamic_policies
    # _mcp_policy_check skip → None
    assert registry._mcp_policy_check(native, REACT) is None


@pytest.mark.asyncio
async def test_execute_native_handler_not_affected_by_mcp_policy_check(registry):
    """Un handler natif (ex: get_time si dispo, sinon premier natif) marche
    normalement même si on register des dynamic en parallèle."""
    cleanup = _register_with_policy(
        registry, "mcp__test__parallel_dynamic", MCPPolicy.READ_ONLY,
    )
    try:
        # Le dynamic ne doit pas affecter le natif
        # On vérifie que _mcp_policy_check skip bien pour discover_tools
        result = registry._mcp_policy_check("discover_tools", REACT)
        assert result is None
    finally:
        cleanup()


# ──────────────────────────────────────────────────────────────────────────────
# Invariant : dynamic sans policy → bloqué
# ──────────────────────────────────────────────────────────────────────────────


def test_dynamic_handler_in_dict_without_policy_is_blocked(registry):
    """Cas anormal : manipulation directe de _dynamic_handlers sans
    _dynamic_policies. _mcp_policy_check doit refuser."""
    hdef = _make_handler_def(name="mcp__test__invariant_violated")
    # Manipulation directe (pour simuler invariant violé)
    registry._dynamic_handlers["mcp__test__invariant_violated"] = hdef
    # Pas de policy attachée !
    try:
        result = registry._mcp_policy_check(
            "mcp__test__invariant_violated", REACT,
        )
        assert result is not None
        assert result.success is False
        assert "invariant" in (result.content or "").lower() or "no policy" in (result.content or "").lower()
    finally:
        registry._dynamic_handlers.pop("mcp__test__invariant_violated", None)


# ──────────────────────────────────────────────────────────────────────────────
# Rollback : unregister nettoie policy
# ──────────────────────────────────────────────────────────────────────────────


def test_unregister_clears_policy(registry):
    hdef = _make_handler_def(name="mcp__test__clear_policy")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    assert registry.get_dynamic_handler_policy("mcp__test__clear_policy") == MCPPolicy.READ_ONLY
    registry.unregister_dynamic_handler("mcp__test__clear_policy")
    assert registry.get_dynamic_handler_policy("mcp__test__clear_policy") is None
    assert "mcp__test__clear_policy" not in registry._dynamic_policies


def test_unregister_then_register_needs_new_policy(registry):
    """Après unregister, register sans policy fail; avec policy OK."""
    hdef = _make_handler_def(name="mcp__test__cycle_policy")
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.READ_ONLY)
    assert registry.unregister_dynamic_handler("mcp__test__cycle_policy") is True
    # 2e register doit aussi exiger policy
    with pytest.raises(TypeError):
        registry.register_dynamic_handler(hdef)  # type: ignore
    # Avec policy → OK
    registry.register_dynamic_handler(hdef, policy=MCPPolicy.EXTERNAL_READ)
    try:
        assert registry.get_dynamic_handler_policy("mcp__test__cycle_policy") == MCPPolicy.EXTERNAL_READ
    finally:
        registry.unregister_dynamic_handler("mcp__test__cycle_policy")


def test_unregister_unknown_does_not_touch_policies_dict(registry):
    """unregister sur inconnu n'affecte pas _dynamic_policies."""
    before = dict(registry._dynamic_policies)
    registry.unregister_dynamic_handler("nonexistent")
    assert registry._dynamic_policies == before


# ──────────────────────────────────────────────────────────────────────────────
# Boot : _dynamic_policies vide
# ──────────────────────────────────────────────────────────────────────────────


def test_dynamic_policies_empty_at_boot(registry):
    assert registry._dynamic_policies == {}
