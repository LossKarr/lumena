"""Lot 0.b — Factory de registre par mission.

Invariant central : créer un registre de mission **n'altère JAMAIS** le registre du
chat (outils + permissions intacts). + natifs présents, MCP actifs déférés (Phase 0).
"""
from __future__ import annotations

import types

import pytest

from src.reasoning.tool_registry import ToolRegistry
from src.subagents.registry_factory import create_mission_registry


def _stub_core():
    """Cœur factice : porte un registre « chat » + pas de MCP (attach best-effort)."""
    core = types.SimpleNamespace()
    core._tool_registry = ToolRegistry(lumena=None)
    core.tool_system = types.SimpleNamespace(_tool_registry=core._tool_registry)
    core.mcp_react_integration = None
    return core


def test_returns_distinct_registry():
    core = _stub_core()
    reg = create_mission_registry(core)
    assert isinstance(reg, ToolRegistry)
    assert reg is not core._tool_registry


def test_does_not_touch_chat_registry():
    core = _stub_core()
    chat_reg = core._tool_registry
    create_mission_registry(core)
    # aucun rebind, aucune substitution
    assert core._tool_registry is chat_reg
    assert core.tool_system._tool_registry is chat_reg


def test_mission_registry_has_native_tools():
    core = _stub_core()
    reg = create_mission_registry(core)
    assert len(reg.tools) > 0
    # quelques natifs incontournables
    assert any(name in reg.tools for name in ("read_file", "write_file", "list_directory"))


def test_allowed_tools_does_not_leak_to_chat():
    core = _stub_core()
    chat_reg = core._tool_registry
    reg = create_mission_registry(core)
    reg._allowed_tools = {"read_file"}
    reg._caller_set_allowed = True
    # le chat n'est ni filtré ni modifié
    assert chat_reg._allowed_tools is None
    assert chat_reg._caller_set_allowed is False
    assert reg._allowed_tools == {"read_file"}


def test_active_mcp_not_exposed_in_phase0():
    core = _stub_core()
    # simule un serveur MCP actif côté CHAT
    core._tool_registry.tools["mcp__demo__do"] = {"name": "mcp__demo__do", "description": "x"}
    reg = create_mission_registry(core)
    # déféré : la mission ne le voit PAS (limitation Phase 0 assumée)
    assert "mcp__demo__do" not in reg.tools


def test_attach_mcp_control_called_when_available():
    core = _stub_core()
    calls = []
    core.mcp_react_integration = types.SimpleNamespace(
        attach_to_tool_registry=lambda r: calls.append(r)
    )
    reg = create_mission_registry(core)
    assert calls == [reg]  # attach appelé sur le registre de la mission


def test_attach_failure_is_non_fatal():
    core = _stub_core()

    def _boom(_r):
        raise RuntimeError("mcp down")

    core.mcp_react_integration = types.SimpleNamespace(attach_to_tool_registry=_boom)
    # ne doit pas propager : une mission se crée même si l'attach MCP échoue
    reg = create_mission_registry(core)
    assert isinstance(reg, ToolRegistry)
