"""Lot 1.4 — Hook lease dans `ToolRegistry.execute`.

Vérifie : outil EXCLUSIF → lease tenu pendant `_execute_inner` ; outil non-exclusif
→ AUCUN lease créé (comportement inchangé) ; deux `execute` sur la même ressource →
sérialisés ; ressources différentes → parallèle.
"""
from __future__ import annotations

import asyncio

import pytest

from src.reasoning.tool_registry import ToolRegistry
from src.subagents import resource_lease as rl


@pytest.fixture
def fresh_lease(monkeypatch):
    """Lease neuf et isolé pour le test (le wrapper lit `get_resource_lease()`)."""
    lease = rl.ResourceLease()
    monkeypatch.setattr(rl, "_LEASE", lease)
    rl.reset_browser_exclusivity_for_tests()  # navigateur (#4) isolé aussi
    return lease


@pytest.mark.asyncio
async def test_exclusive_tool_holds_lease_during_inner(fresh_lease, monkeypatch):
    reg = ToolRegistry(lumena=None)
    held = {}

    async def stub_inner(name, args, *, caller=None):
        key = rl.resource_key_for(name, args)
        held["value"] = fresh_lease.is_held(key)
        return "obs"

    monkeypatch.setattr(reg, "_execute_inner", stub_inner)
    await reg.execute("write_file", {"path": "a.txt"})
    assert held["value"] is True  # lease tenu pendant l'exécution


@pytest.mark.asyncio
async def test_non_exclusive_tool_creates_no_lease(fresh_lease, monkeypatch):
    reg = ToolRegistry(lumena=None)
    called = {"n": 0}

    async def stub_inner(name, args, *, caller=None):
        called["n"] += 1
        return "obs"

    monkeypatch.setattr(reg, "_execute_inner", stub_inner)
    await reg.execute("read_file", {"path": "a.txt"})
    assert called["n"] == 1
    assert fresh_lease._locks == {}  # aucun lease créé pour un outil non-exclusif


@pytest.mark.asyncio
async def test_same_resource_serializes_at_execute(fresh_lease, monkeypatch):
    reg = ToolRegistry(lumena=None)
    order = []

    async def stub_inner(name, args, *, caller=None):
        order.append(f"start-{name}")
        await asyncio.sleep(0.03)
        order.append(f"end-{name}")
        return "obs"

    monkeypatch.setattr(reg, "_execute_inner", stub_inner)
    # deux outils navigateur → même clé "browser" → sérialisés
    await asyncio.gather(
        reg.execute("browser_navigate", {}),
        reg.execute("browser_click", {}),
    )
    assert order[0].startswith("start") and order[1].startswith("end")  # pas d'entrelacement


@pytest.mark.asyncio
async def test_different_resources_run_parallel(fresh_lease, monkeypatch):
    reg = ToolRegistry(lumena=None)
    order = []

    async def stub_inner(name, args, *, caller=None):
        order.append(f"start-{name}")
        await asyncio.sleep(0.03)
        order.append(f"end-{name}")
        return "obs"

    monkeypatch.setattr(reg, "_execute_inner", stub_inner)
    # browser vs computer_use → clés différentes → parallèle
    await asyncio.gather(
        reg.execute("browser_navigate", {}),
        reg.execute("click", {}),
    )
    assert order[0].startswith("start") and order[1].startswith("start")
