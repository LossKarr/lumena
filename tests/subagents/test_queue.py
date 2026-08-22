"""Lot 2.1 — File neutre des missions (plafond de concurrence).

Vérifie : défaut 1 ; concurrence 1 → sérialisé (pas de chevauchement) ; concurrence 2
→ chevauchement permis ; `queue_load` cohérent ; relâche même sur exception ;
neutralité P2P.
"""
from __future__ import annotations

import asyncio

import pytest

from src.subagents import queue as q


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("LUMENA_MISSION_CONCURRENCY", raising=False)
    q.reset_for_tests()
    yield
    q.reset_for_tests()


def test_default_concurrency_is_one():
    assert q.get_subagent_concurrency() == 1


@pytest.mark.asyncio
async def test_concurrency_one_serializes(monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_CONCURRENCY", "1")
    q.reset_for_tests()
    order = []

    async def worker(tag):
        async with q.mission_slot():
            order.append(f"start-{tag}")
            await asyncio.sleep(0.02)
            order.append(f"end-{tag}")

    await asyncio.gather(worker("A"), worker("B"), worker("C"))
    # jamais deux 'start' sans un 'end' entre les deux → aucun chevauchement
    for i in range(1, len(order)):
        if order[i].startswith("start"):
            assert order[i - 1].startswith("end")


@pytest.mark.asyncio
async def test_concurrency_two_allows_overlap(monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_CONCURRENCY", "2")
    q.reset_for_tests()
    peak = {"max": 0}

    async def worker():
        async with q.mission_slot():
            peak["max"] = max(peak["max"], q.queue_load()["running"])
            await asyncio.sleep(0.02)

    await asyncio.gather(worker(), worker(), worker())
    assert peak["max"] == 2  # deux missions ont tourné en même temps (pas trois)


@pytest.mark.asyncio
async def test_queue_load_reflects_running(monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_CONCURRENCY", "1")
    q.reset_for_tests()
    inside = asyncio.Event()
    release = asyncio.Event()

    async def hold():
        async with q.mission_slot():
            inside.set()
            await release.wait()

    t = asyncio.create_task(hold())
    await inside.wait()
    load = q.queue_load()
    assert load["running"] == 1
    assert load["concurrency"] == 1
    release.set()
    await t
    assert q.queue_load()["running"] == 0


@pytest.mark.asyncio
async def test_release_on_exception(monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_CONCURRENCY", "1")
    q.reset_for_tests()
    with pytest.raises(ValueError):
        async with q.mission_slot():
            raise ValueError("boom")
    # le créneau a été relâché malgré l'exception → ré-acquérable
    async with q.mission_slot():
        assert q.queue_load()["running"] == 1
    assert q.queue_load()["running"] == 0


def test_neutral_no_peer_import():
    import inspect
    # zéro dépendance P2P : on scanne les lignes d'IMPORT (pas le docstring qui
    # mentionne légitimement `peer_mission_worker` pour expliquer la neutralité).
    for line in inspect.getsource(q).splitlines():
        s = line.strip()
        if s.startswith(("import ", "from ")):
            assert "peer" not in s.lower(), f"import P2P interdit : {s}"
