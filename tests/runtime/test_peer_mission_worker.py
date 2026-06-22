"""Brique 3 (M1) — tests de la file d'attente des missions (concurrence bornée)."""
from __future__ import annotations

import asyncio

import pytest

from src.runtime import peer_mission_worker as mw


@pytest.fixture(autouse=True)
def _reset():
    mw.reset_for_tests()
    yield
    mw.reset_for_tests()


def test_default_concurrency_is_one(monkeypatch):
    monkeypatch.delenv("LUMENA_PEER_MISSION_CONCURRENCY", raising=False)
    assert mw.get_mission_concurrency() == 1


def test_concurrency_from_env(monkeypatch):
    monkeypatch.setenv("LUMENA_PEER_MISSION_CONCURRENCY", "3")
    assert mw.get_mission_concurrency() == 3
    monkeypatch.setenv("LUMENA_PEER_MISSION_CONCURRENCY", "0")
    assert mw.get_mission_concurrency() == 1  # min 1
    monkeypatch.setenv("LUMENA_PEER_MISSION_CONCURRENCY", "abc")
    assert mw.get_mission_concurrency() == 1  # fail-safe


@pytest.mark.asyncio
async def test_slot_bounds_concurrency(monkeypatch):
    """Avec concurrence=1, deux missions ne tournent jamais en même temps."""
    monkeypatch.setenv("LUMENA_PEER_MISSION_CONCURRENCY", "1")
    mw.reset_for_tests()

    max_seen = 0
    current = 0
    lock = asyncio.Lock()

    async def job():
        nonlocal max_seen, current
        async with mw.mission_slot():
            async with lock:
                current += 1
                max_seen = max(max_seen, current)
            await asyncio.sleep(0.02)
            async with lock:
                current -= 1

    await asyncio.gather(*[job() for _ in range(5)])
    assert max_seen == 1  # jamais 2 en parallèle


@pytest.mark.asyncio
async def test_slot_allows_configured_parallelism(monkeypatch):
    monkeypatch.setenv("LUMENA_PEER_MISSION_CONCURRENCY", "2")
    mw.reset_for_tests()

    max_seen = 0
    current = 0
    lock = asyncio.Lock()

    async def job():
        nonlocal max_seen, current
        async with mw.mission_slot():
            async with lock:
                current += 1
                max_seen = max(max_seen, current)
            await asyncio.sleep(0.02)
            async with lock:
                current -= 1

    await asyncio.gather(*[job() for _ in range(6)])
    assert max_seen == 2  # exactement la concurrence configurée


@pytest.mark.asyncio
async def test_mission_load_reports_state(monkeypatch):
    monkeypatch.setenv("LUMENA_PEER_MISSION_CONCURRENCY", "1")
    mw.reset_for_tests()

    started = asyncio.Event()
    release = asyncio.Event()

    async def long_job():
        async with mw.mission_slot():
            started.set()
            await release.wait()

    task = asyncio.create_task(long_job())
    await started.wait()
    load = mw.mission_load()
    assert load["running"] == 1
    assert load["concurrency"] == 1
    release.set()
    await task
    assert mw.mission_load()["running"] == 0


# ── M2 — plafond de durée mission (réglable) ─────────────────────────────────

def test_mission_max_timeout_default_and_env(monkeypatch):
    from web.routes.peers import _get_mission_max_timeout
    monkeypatch.delenv("LUMENA_PEER_MISSION_MAX_TIMEOUT", raising=False)
    assert _get_mission_max_timeout() == 1800            # défaut 30 min
    monkeypatch.setenv("LUMENA_PEER_MISSION_MAX_TIMEOUT", "3600")
    assert _get_mission_max_timeout() == 3600
    monkeypatch.setenv("LUMENA_PEER_MISSION_MAX_TIMEOUT", "abc")
    assert _get_mission_max_timeout() == 1800            # fail-safe
    monkeypatch.setenv("LUMENA_PEER_MISSION_MAX_TIMEOUT", "5")
    assert _get_mission_max_timeout() == 10              # plancher = min sync
