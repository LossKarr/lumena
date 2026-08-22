"""Lot 5.1 — Pools par profondeur (anti-deadlock lead⇄worker).

Preuve CLÉ : un *lead* (profondeur 1, pool top=1) qui tient son créneau n'empêche PAS
ses *workers* (profondeur 2, pool worker=2) de tourner → AUCUN deadlock. Avec un
sémaphore global unique (l'ancien design), ce scénario bloquerait. + tailles de pools,
+ le worker lit la profondeur dans le metadata.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from src.runtime.task_orchestrator import TaskOrchestrator
from src.subagents import queue as q
from src.subagents import worker as worker_mod


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.delenv("LUMENA_MISSION_CONCURRENCY", raising=False)
    monkeypatch.delenv("LUMENA_MISSION_WORKER_CONCURRENCY", raising=False)
    q.reset_for_tests()
    yield
    q.reset_for_tests()


# ── tailles de pools ─────────────────────────────────────────────────────────────

def test_default_worker_concurrency_is_two():
    assert q.get_worker_concurrency() == 2
    assert q.get_subagent_concurrency() == 1


def test_pool_size_top_vs_worker():
    assert q._pool_size(0) == 1   # chat
    assert q._pool_size(1) == 1   # lead top
    assert q._pool_size(2) == 2   # worker
    assert q._pool_size(3) == 2   # worker profond


# ── PREUVE ANTI-DEADLOCK ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_lead_holding_slot_does_not_block_workers(monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_CONCURRENCY", "1")       # 1 seul lead
    monkeypatch.setenv("LUMENA_MISSION_WORKER_CONCURRENCY", "2")  # 2 workers
    q.reset_for_tests()
    ran = []

    async def lead():
        async with q.mission_slot(1):          # le lead TIENT l'unique créneau top
            async def wk(tag):
                async with q.mission_slot(2):  # pool worker INDÉPENDANT
                    ran.append(tag)
                    await asyncio.sleep(0.02)
            # si les pools étaient partagés (1 global), ceci bloquerait → timeout
            await asyncio.wait_for(asyncio.gather(wk("A"), wk("B")), timeout=1.0)

    await asyncio.wait_for(lead(), timeout=2.0)
    assert set(ran) == {"A", "B"}  # les 2 workers ont tourné MALGRÉ le lead actif


@pytest.mark.asyncio
async def test_worker_pool_caps_overlap_at_two(monkeypatch):
    monkeypatch.setenv("LUMENA_MISSION_WORKER_CONCURRENCY", "2")
    q.reset_for_tests()
    peak = {"max": 0}

    async def wk():
        async with q.mission_slot(2):
            peak["max"] = max(peak["max"], q.queue_load()["by_depth"][2]["running"])
            await asyncio.sleep(0.02)

    await asyncio.gather(wk(), wk(), wk())
    assert peak["max"] == 2  # 2 en // au pic, jamais 3


def test_queue_load_compat_keys():
    # compat Lot 2.1 : `running`/`waiting`/`concurrency` toujours présents (agrégés)
    load = q.queue_load()
    assert load["running"] == 0 and load["waiting"] == 0
    assert load["concurrency"] == 1 and load["worker_concurrency"] == 2


# ── le worker lit la profondeur ──────────────────────────────────────────────────

def test_mission_depth_reads_metadata(tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    rec = orch.start_task(conversation_id="__missions__", channel="mission",
                          message_preview="w", metadata={"kind": "mission", "depth": 2})
    assert worker_mod._mission_depth(core, rec.task_id) == 2


def test_mission_depth_defaults_to_one(tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "s.json"))
    core = types.SimpleNamespace(task_orchestrator=orch)
    rec = orch.start_task(conversation_id="__missions__", channel="mission",
                          message_preview="x", metadata={"kind": "mission"})  # pas de depth
    assert worker_mod._mission_depth(core, rec.task_id) == 1
    assert worker_mod._mission_depth(core, "inconnu") == 1
