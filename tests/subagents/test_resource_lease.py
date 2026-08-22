"""Lot 0.c — ResourceLease + mapping outil → clé.

Couvre : mapping des clés réelles ; sérialisation même clé ; parallélisme clés
différentes ; timeout d'attente ; annulation pendant l'attente ; libération même
sur exception du bloc protégé.
"""
from __future__ import annotations

import asyncio

import pytest

from src.subagents.resource_lease import ResourceLease, resource_key_for


# ── Mapping ─────────────────────────────────────────────────────────────────────

def test_mapping_browser_global():
    assert resource_key_for("browser_navigate", {}) == "browser"
    assert resource_key_for("browser_click", {"selector": "x"}) == "browser"


def test_mapping_computer_use_global():
    assert resource_key_for("click", {}) == "computer_use"
    assert resource_key_for("screenshot", {}) == "computer_use"


def test_mapping_files_per_path():
    k1 = resource_key_for("write_file", {"path": "a/b.txt"})
    k2 = resource_key_for("write_file", {"path": "a/c.txt"})
    assert k1.startswith("files:") and k2.startswith("files:")
    assert k1 != k2  # chemins différents → clés différentes (pas de verrou global)


def test_mapping_mcp_per_server():
    assert resource_key_for("mcp__weather__forecast", {}) == "mcp:weather"
    assert resource_key_for("mcp__db__query", {}) == "mcp:db"


def test_mapping_non_exclusive_is_none():
    assert resource_key_for("read_file", {"path": "x"}) is None
    assert resource_key_for("memory_search", {}) is None
    assert resource_key_for("", {}) is None


# ── Lease ───────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_same_key_serializes():
    lease = ResourceLease()
    order = []

    async def worker(tag, hold_s):
        async with lease.hold("browser"):
            order.append(f"start-{tag}")
            await asyncio.sleep(hold_s)
            order.append(f"end-{tag}")

    await asyncio.gather(worker("A", 0.05), worker("B", 0.01))
    # B ne démarre qu'après la fin de A (même clé) — pas d'entrelacement
    assert order in (
        ["start-A", "end-A", "start-B", "end-B"],
        ["start-B", "end-B", "start-A", "end-A"],
    )


@pytest.mark.asyncio
async def test_different_keys_run_in_parallel():
    lease = ResourceLease()
    order = []

    async def worker(key, tag):
        async with lease.hold(key):
            order.append(f"start-{tag}")
            await asyncio.sleep(0.05)
            order.append(f"end-{tag}")

    await asyncio.gather(worker("browser", "A"), worker("computer_use", "B"))
    # clés différentes → les deux démarrent avant qu'aucune ne finisse
    assert order[0].startswith("start") and order[1].startswith("start")


@pytest.mark.asyncio
async def test_timeout_when_held():
    lease = ResourceLease()
    async with lease.hold("browser"):
        with pytest.raises(asyncio.TimeoutError):
            async with lease.hold("browser", timeout=0.02):
                pass  # ne devrait jamais entrer


@pytest.mark.asyncio
async def test_cancellation_during_wait_releases_for_others():
    lease = ResourceLease()

    async def holder(ev):
        async with lease.hold("browser"):
            ev.set()
            await asyncio.sleep(0.2)

    started = asyncio.Event()
    h = asyncio.create_task(holder(started))
    await started.wait()

    waiter = asyncio.create_task(_acquire(lease, "browser"))
    await asyncio.sleep(0.02)
    waiter.cancel()  # annule l'attente
    with pytest.raises(asyncio.CancelledError):
        await waiter

    await h  # le holder finit normalement
    assert lease.is_held("browser") is False  # libéré, ré-acquérable


async def _acquire(lease, key):
    async with lease.hold(key):
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_release_on_exception():
    lease = ResourceLease()
    with pytest.raises(ValueError):
        async with lease.hold("browser"):
            raise ValueError("boom")
    # le lease a été libéré malgré l'exception
    assert lease.is_held("browser") is False
    async with lease.hold("browser"):  # ré-acquérable immédiatement
        assert lease.is_held("browser") is True
