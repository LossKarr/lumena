"""Tests Phase F — 5 outils LLM user-facing dans react_integration.

Couvre par outil :
  - caller_kind invalide → blocked
  - confirmation_phrase invalide → blocked
  - service indisponible → blocked
  - server inconnu → blocked
  - succes → ok avec recommendation_code attendue
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

import pytest

from src.mcp.react_integration import (
    ADD_MCP_CONFIRMATION_PHRASE,
    ADD_MCP_TOOL_NAME,
    DISABLE_MCP_CONFIRMATION_PHRASE,
    DISABLE_MCP_TOOL_NAME,
    MCPReActIntegration,
    MCPReActIntegrationDeps,
    REMOVE_MCP_CONFIRMATION_PHRASE,
    REMOVE_MCP_TOOL_NAME,
    SET_MCP_CATEGORY_CONFIRMATION_PHRASE,
    SET_MCP_CATEGORY_TOOL_NAME,
    SET_MCP_PREFERENCE_CONFIRMATION_PHRASE,
    SET_MCP_PREFERENCE_TOOL_NAME,
)


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _parse(json_str: str) -> Dict[str, Any]:
    return json.loads(json_str)


@dataclass
class _DummyEntry:
    server_id: str = "alice"
    prefer_over_native: bool = False
    semantic_category: Optional[str] = None


class _FakeCatalog:
    def __init__(self, *, known_ids=("alice",), raise_on_remove=False):
        self.known_ids = set(known_ids)
        self.raise_on_remove = raise_on_remove
        self.update_prefer_calls: list = []
        self.update_category_calls: list = []
        self.remove_calls: list = []

    def remove_server(self, server_id):
        self.remove_calls.append(server_id)
        if self.raise_on_remove:
            raise RuntimeError("simulated_failure")
        if server_id not in self.known_ids:
            return False
        return True

    def update_prefer_over_native(self, server_id, prefer):
        self.update_prefer_calls.append((server_id, prefer))
        if server_id not in self.known_ids:
            raise RuntimeError("server_not_found")
        return _DummyEntry(server_id=server_id, prefer_over_native=prefer)

    def update_semantic_category(self, server_id, category, source):
        self.update_category_calls.append((server_id, category, source))
        if server_id not in self.known_ids:
            raise RuntimeError("server_not_found")
        return _DummyEntry(server_id=server_id, semantic_category=category)


class _FakeActivationService:
    def __init__(self, *, success=True, reason=""):
        self.success = success
        self.reason = reason
        self.deactivate_calls: list = []

    def deactivate(self, server_id):
        self.deactivate_calls.append(server_id)
        return type("R", (), {"success": self.success, "reason": self.reason})()


class _FakeOrchestrator:
    def __init__(self, *, raise_on_propose=False):
        self.raise_on_propose = raise_on_propose
        self.calls: list = []

    def propose(self, **kwargs):
        self.calls.append(kwargs)
        if self.raise_on_propose:
            raise RuntimeError("orchestrator_failed")
        return {"server_id": "alice", "package_spec": kwargs.get("package_spec")}


def _integration(*, catalog=None, activation_service=None, catalog_add_orchestrator=None):
    deps = MCPReActIntegrationDeps(
        catalog=catalog,
        activation_service=activation_service,
        catalog_add_orchestrator=catalog_add_orchestrator,
    )
    return MCPReActIntegration(deps)


async def _call(handler, **kwargs):
    return await handler(**kwargs)


# ══════════════════════════════════════════════════════════════════════════════
# Section A — add_mcp
# ══════════════════════════════════════════════════════════════════════════════


class TestAddMcpDryRun:
    @pytest.mark.asyncio
    async def test_dry_run_resolves_package_spec(self):
        integration = _integration()
        handler = integration._make_add_mcp_handler()
        out = await _call(handler, target="npm:mcp-foo", caller_kind="react")
        data = _parse(out)
        assert data["decision"] == "ok"
        assert data["payload"]["kind"] == "package_spec"
        assert data["payload"]["package_spec"] == "npm:mcp-foo"
        assert data["payload"]["dry_run"] is True
        assert data["payload"]["recommendation_code"] == "mcp_target_resolved"

    @pytest.mark.asyncio
    async def test_dry_run_intent_kind_for_free_text(self):
        integration = _integration()
        out = await _call(
            integration._make_add_mcp_handler(),
            target="trouve moi un MCP pour scraper",
            caller_kind="react",
        )
        data = _parse(out)
        assert data["payload"]["kind"] == "intent"

    @pytest.mark.asyncio
    async def test_empty_target_blocked(self):
        out = await _call(
            _integration()._make_add_mcp_handler(),
            target="",
            caller_kind="react",
        )
        data = _parse(out)
        assert data["decision"] == "blocked"
        assert data["blockers"] == ["mcp_target_invalid"]

    @pytest.mark.asyncio
    async def test_non_string_target_blocked(self):
        out = await _call(
            _integration()._make_add_mcp_handler(),
            target=42,
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["mcp_target_invalid"]

    @pytest.mark.asyncio
    async def test_dry_run_accepts_research_agent_caller(self):
        out = await _call(
            _integration()._make_add_mcp_handler(),
            target="npm:mcp-foo",
            caller_kind="research_agent",
        )
        assert _parse(out)["decision"] == "ok"

    @pytest.mark.asyncio
    async def test_dry_run_blocks_code_agent(self):
        out = await _call(
            _integration()._make_add_mcp_handler(),
            target="npm:mcp-foo",
            caller_kind="code_agent",
        )
        data = _parse(out)
        assert data["blockers"] == ["code_agent_out_of_scope"]


class TestAddMcpLive:
    @pytest.mark.asyncio
    async def test_live_requires_react_caller(self):
        out = await _call(
            _integration()._make_add_mcp_handler(),
            target="npm:mcp-foo",
            live=True,
            confirmation_phrase=ADD_MCP_CONFIRMATION_PHRASE,
            caller_kind="research_agent",
        )
        assert _parse(out)["blockers"] == ["caller_kind_not_allowed"]

    @pytest.mark.asyncio
    async def test_live_blocked_without_confirmation_phrase(self):
        out = await _call(
            _integration()._make_add_mcp_handler(),
            target="npm:mcp-foo",
            live=True,
            confirmation_phrase="wrong",
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["confirmation_phrase_invalid"]

    @pytest.mark.asyncio
    async def test_live_blocked_when_orchestrator_unavailable(self):
        out = await _call(
            _integration()._make_add_mcp_handler(),
            target="npm:mcp-foo",
            live=True,
            confirmation_phrase=ADD_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["mcp_service_unavailable"]

    @pytest.mark.asyncio
    async def test_live_calls_orchestrator_propose(self, monkeypatch):
        # Phase I-8 (Fix AB) : pas de sonde registry réelle en test —
        # None = réseau indisponible → le flux d'origine continue.
        import src.mcp.target_resolver as _tr
        monkeypatch.setattr(
            _tr, "probe_package_exists", lambda spec, **kw: None
        )
        orch = _FakeOrchestrator()
        integration = _integration(catalog_add_orchestrator=orch)
        out = await _call(
            integration._make_add_mcp_handler(),
            target="npm:mcp-foo",
            live=True,
            confirmation_phrase=ADD_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        data = _parse(out)
        assert data["decision"] == "ok"
        assert data["payload"]["recommendation_code"] == "mcp_added"
        assert orch.calls[0]["package_spec"] == "npm:mcp-foo"

    @pytest.mark.asyncio
    async def test_live_orchestrator_failure_returns_blocked(self, monkeypatch):
        import src.mcp.target_resolver as _tr
        monkeypatch.setattr(
            _tr, "probe_package_exists", lambda spec, **kw: None
        )
        orch = _FakeOrchestrator(raise_on_propose=True)
        integration = _integration(catalog_add_orchestrator=orch)
        out = await _call(
            integration._make_add_mcp_handler(),
            target="npm:mcp-foo",
            live=True,
            confirmation_phrase=ADD_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["mcp_action_failed"]


# ══════════════════════════════════════════════════════════════════════════════
# Section B — disable_mcp
# ══════════════════════════════════════════════════════════════════════════════


class TestDisableMcp:
    @pytest.mark.asyncio
    async def test_disable_blocked_without_confirmation(self):
        out = await _call(
            _integration()._make_disable_mcp_handler(),
            server_id="alice",
            confirmation_phrase="wrong",
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["confirmation_phrase_invalid"]

    @pytest.mark.asyncio
    async def test_disable_blocked_for_research_caller(self):
        out = await _call(
            _integration()._make_disable_mcp_handler(),
            server_id="alice",
            confirmation_phrase=DISABLE_MCP_CONFIRMATION_PHRASE,
            caller_kind="research_agent",
        )
        assert _parse(out)["blockers"] == ["caller_kind_not_allowed"]

    @pytest.mark.asyncio
    async def test_disable_blocked_invalid_server_id(self):
        svc = _FakeActivationService()
        out = await _call(
            _integration(activation_service=svc)._make_disable_mcp_handler(),
            server_id="!!invalid!!",
            confirmation_phrase=DISABLE_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["mcp_server_id_invalid"]

    @pytest.mark.asyncio
    async def test_disable_blocked_without_activation_service(self):
        out = await _call(
            _integration()._make_disable_mcp_handler(),
            server_id="alice",
            confirmation_phrase=DISABLE_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["mcp_service_unavailable"]

    @pytest.mark.asyncio
    async def test_disable_succeeds(self):
        svc = _FakeActivationService(success=True)
        out = await _call(
            _integration(activation_service=svc)._make_disable_mcp_handler(),
            server_id="alice",
            confirmation_phrase=DISABLE_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        data = _parse(out)
        assert data["decision"] == "ok"
        assert data["payload"]["recommendation_code"] == "mcp_disabled"
        assert svc.deactivate_calls == ["alice"]

    @pytest.mark.asyncio
    async def test_disable_fails_when_service_returns_false(self):
        svc = _FakeActivationService(success=False, reason="not_running")
        out = await _call(
            _integration(activation_service=svc)._make_disable_mcp_handler(),
            server_id="alice",
            confirmation_phrase=DISABLE_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["mcp_action_failed"]


# ══════════════════════════════════════════════════════════════════════════════
# Section C — remove_mcp
# ══════════════════════════════════════════════════════════════════════════════


class TestRemoveMcp:
    @pytest.mark.asyncio
    async def test_remove_blocked_without_confirmation(self):
        out = await _call(
            _integration()._make_remove_mcp_handler(),
            server_id="alice",
            confirmation_phrase="wrong",
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["confirmation_phrase_invalid"]

    @pytest.mark.asyncio
    async def test_remove_blocked_invalid_server_id(self):
        cat = _FakeCatalog()
        out = await _call(
            _integration(catalog=cat)._make_remove_mcp_handler(),
            server_id="INVALID!",
            confirmation_phrase=REMOVE_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["mcp_server_id_invalid"]

    @pytest.mark.asyncio
    async def test_remove_returns_unknown_when_catalog_returns_false(self):
        cat = _FakeCatalog(known_ids=())
        out = await _call(
            _integration(catalog=cat)._make_remove_mcp_handler(),
            server_id="ghost",
            confirmation_phrase=REMOVE_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["mcp_server_unknown"]

    @pytest.mark.asyncio
    async def test_remove_succeeds(self):
        cat = _FakeCatalog(known_ids=("alice",))
        out = await _call(
            _integration(catalog=cat)._make_remove_mcp_handler(),
            server_id="alice",
            confirmation_phrase=REMOVE_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        data = _parse(out)
        assert data["decision"] == "ok"
        assert data["payload"]["recommendation_code"] == "mcp_removed"
        assert cat.remove_calls == ["alice"]

    @pytest.mark.asyncio
    async def test_remove_blocked_when_catalog_raises(self):
        cat = _FakeCatalog(raise_on_remove=True)
        out = await _call(
            _integration(catalog=cat)._make_remove_mcp_handler(),
            server_id="alice",
            confirmation_phrase=REMOVE_MCP_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["mcp_action_failed"]


# ══════════════════════════════════════════════════════════════════════════════
# Section D — set_mcp_preference
# ══════════════════════════════════════════════════════════════════════════════


class TestSetMcpPreference:
    @pytest.mark.asyncio
    async def test_pref_blocked_without_confirmation(self):
        out = await _call(
            _integration()._make_set_mcp_preference_handler(),
            server_id="alice",
            prefer_over_native=True,
            confirmation_phrase="wrong",
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["confirmation_phrase_invalid"]

    @pytest.mark.asyncio
    async def test_pref_sets_true(self):
        cat = _FakeCatalog()
        out = await _call(
            _integration(catalog=cat)._make_set_mcp_preference_handler(),
            server_id="alice",
            prefer_over_native=True,
            confirmation_phrase=SET_MCP_PREFERENCE_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        data = _parse(out)
        assert data["decision"] == "ok"
        assert data["payload"]["prefer_over_native"] is True
        assert cat.update_prefer_calls == [("alice", True)]

    @pytest.mark.asyncio
    async def test_pref_unknown_server(self):
        cat = _FakeCatalog(known_ids=())
        out = await _call(
            _integration(catalog=cat)._make_set_mcp_preference_handler(),
            server_id="ghost",
            prefer_over_native=False,
            confirmation_phrase=SET_MCP_PREFERENCE_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["mcp_server_unknown"]

    @pytest.mark.asyncio
    async def test_pref_blocked_without_catalog(self):
        out = await _call(
            _integration()._make_set_mcp_preference_handler(),
            server_id="alice",
            prefer_over_native=True,
            confirmation_phrase=SET_MCP_PREFERENCE_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["mcp_service_unavailable"]


# ══════════════════════════════════════════════════════════════════════════════
# Section E — set_mcp_category (HUMAN_TO_CATEGORY)
# ══════════════════════════════════════════════════════════════════════════════


class TestSetMcpCategory:
    @pytest.mark.asyncio
    async def test_category_blocked_without_confirmation(self):
        out = await _call(
            _integration()._make_set_mcp_category_handler(),
            server_id="alice",
            human_phrase="messagerie",
            confirmation_phrase="wrong",
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["confirmation_phrase_invalid"]

    @pytest.mark.asyncio
    async def test_category_translates_messagerie_to_mail(self):
        cat = _FakeCatalog()
        out = await _call(
            _integration(catalog=cat)._make_set_mcp_category_handler(),
            server_id="alice",
            human_phrase="messagerie",
            confirmation_phrase=SET_MCP_CATEGORY_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        data = _parse(out)
        assert data["decision"] == "ok"
        assert data["payload"]["semantic_category"] == "mail"
        assert cat.update_category_calls == [("alice", "mail", "user_override")]

    @pytest.mark.asyncio
    async def test_category_unknown_human_phrase(self):
        cat = _FakeCatalog()
        out = await _call(
            _integration(catalog=cat)._make_set_mcp_category_handler(),
            server_id="alice",
            human_phrase="zzz totalement inconnu xxx",
            confirmation_phrase=SET_MCP_CATEGORY_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["mcp_category_unknown"]

    @pytest.mark.asyncio
    async def test_category_empty_phrase_blocked(self):
        out = await _call(
            _integration(catalog=_FakeCatalog())._make_set_mcp_category_handler(),
            server_id="alice",
            human_phrase="   ",
            confirmation_phrase=SET_MCP_CATEGORY_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["mcp_category_unknown"]

    @pytest.mark.asyncio
    async def test_category_blocked_without_catalog(self):
        out = await _call(
            _integration()._make_set_mcp_category_handler(),
            server_id="alice",
            human_phrase="messagerie",
            confirmation_phrase=SET_MCP_CATEGORY_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["mcp_service_unavailable"]

    @pytest.mark.asyncio
    async def test_category_unknown_server_id(self):
        cat = _FakeCatalog(known_ids=())
        out = await _call(
            _integration(catalog=cat)._make_set_mcp_category_handler(),
            server_id="ghost",
            human_phrase="messagerie",
            confirmation_phrase=SET_MCP_CATEGORY_CONFIRMATION_PHRASE,
            caller_kind="react",
        )
        assert _parse(out)["blockers"] == ["mcp_server_unknown"]


# ══════════════════════════════════════════════════════════════════════════════
# Section F — attach_to_tool_registry câble les 5 outils
# ══════════════════════════════════════════════════════════════════════════════


class _FakeRegistry:
    """Mini registry pour tester attach_to_tool_registry sans deps lourdes."""
    def __init__(self):
        self.tools = {}
        self._tool_modules = {}
        self._sig_cache = {}
        self._tools_desc_cache = None
        self._tool_collection = None
        self._native_handler_names = frozenset()

    def register(self, *, name, description, parameters, handler):
        self.tools[name] = {
            "name": name,
            "description": description,
            "parameters": parameters,
            "handler": handler,
        }


def test_attach_registers_five_phase_f_tools():
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))
    reg = _FakeRegistry()
    attached, reason = integration.attach_to_tool_registry(reg)
    assert (attached, reason) == (True, "attached")
    assert ADD_MCP_TOOL_NAME in reg.tools
    assert DISABLE_MCP_TOOL_NAME in reg.tools
    assert REMOVE_MCP_TOOL_NAME in reg.tools
    assert SET_MCP_PREFERENCE_TOOL_NAME in reg.tools
    assert SET_MCP_CATEGORY_TOOL_NAME in reg.tools


def test_attach_phase_f_required_lists():
    integration = MCPReActIntegration(MCPReActIntegrationDeps(catalog=object()))
    reg = _FakeRegistry()
    integration.attach_to_tool_registry(reg)
    assert reg.tools[ADD_MCP_TOOL_NAME]["required"] == ["target"]
    assert reg.tools[DISABLE_MCP_TOOL_NAME]["required"] == [
        "server_id", "confirmation_phrase",
    ]
    assert reg.tools[REMOVE_MCP_TOOL_NAME]["required"] == [
        "server_id", "confirmation_phrase",
    ]
    assert reg.tools[SET_MCP_PREFERENCE_TOOL_NAME]["required"] == [
        "server_id", "prefer_over_native", "confirmation_phrase",
    ]
    assert reg.tools[SET_MCP_CATEGORY_TOOL_NAME]["required"] == [
        "server_id", "human_phrase", "confirmation_phrase",
    ]
