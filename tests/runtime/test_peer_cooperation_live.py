"""C3-live — Tick d'initiative RÉELLE (exécution sous gate).

On mocke la carte de capacités, les goals, le handler d'envoi et la présence. On
vérifie : exécution une seule fois par objectif nouveau ; pas d'exécution sous
halt ; no-op si mode ≠ live ; échec du handler tracé sans planter.
"""
from __future__ import annotations

import pytest

from src.runtime import peer_network_autonomy as pna
from src.runtime import peer_live_gate as gate


def _map_one():
    peer = {"instance_id": "p1", "name": "Lumena B", "capabilities": ["chat", "documents"],
            "allowed_scopes": ["chat", "task.delegate"], "capability_level": "mission",
            "delegable": True, "reachable": True, "quarantined": False, "seen_seconds_ago": 5}
    return {"peers": [peer], "count": 1, "delegable_count": 1}


@pytest.fixture(autouse=True)
def _setup(monkeypatch):
    gate.clear_for_tests()
    monkeypatch.setenv("LUMENA_PEER_AUTONOMY", "live")
    monkeypatch.delenv("LUMENA_PEER_HALT", raising=False)
    monkeypatch.setenv("LUMENA_PEER_AUTONOMY_WHEN_PRESENT", "1")  # ignore la présence
    monkeypatch.setattr(pna, "build_capability_map", lambda **k: _map_one(), raising=False)
    import src.runtime.peer_awareness as pa
    monkeypatch.setattr(pa, "build_capability_map", lambda **k: _map_one())
    monkeypatch.setattr(pna, "_fetch_candidate_goals", lambda *a, **k: [{"objective": "Rédiger un rapport"}])
    # pas de mission en cours
    import src.runtime.peer_mission_tracker as mt
    monkeypatch.setattr(mt, "list_pending", lambda: [])
    yield
    gate.clear_for_tests()


@pytest.mark.asyncio
async def test_live_executes_once_then_dedups(monkeypatch):
    calls = []

    async def fake_submit(ctx, peer_id, objective, *a, **k):
        calls.append((peer_id, objective))
        class R: success = True
        return R()
    import src.reasoning.handlers.peer_tasks as pt
    monkeypatch.setattr(pt, "submit_peer_task_handler", fake_submit)

    out1 = await pna.run_peer_cooperation_live_tick()
    assert out1["executed"] == 1
    assert calls == [("p1", "Rédiger un rapport")]

    # 2e tick : même objectif → dedup, aucune nouvelle exécution
    out2 = await pna.run_peer_cooperation_live_tick()
    assert out2["executed"] == 0
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_live_halt_blocks_execution(monkeypatch):
    monkeypatch.setenv("LUMENA_PEER_HALT", "1")
    called = []

    async def fake_submit(ctx, peer_id, objective, *a, **k):
        called.append(objective)
        class R: success = True
        return R()
    import src.reasoning.handlers.peer_tasks as pt
    monkeypatch.setattr(pt, "submit_peer_task_handler", fake_submit)

    out = await pna.run_peer_cooperation_live_tick()
    assert out["executed"] == 0
    assert called == []  # halt = aucune NOUVELLE délégation


@pytest.mark.asyncio
async def test_not_live_is_noop(monkeypatch):
    monkeypatch.setenv("LUMENA_PEER_AUTONOMY", "shadow")
    out = await pna.run_peer_cooperation_live_tick()
    assert out["executed"] == 0


@pytest.mark.asyncio
async def test_handler_failure_is_traced_not_fatal(monkeypatch):
    async def fake_submit(ctx, peer_id, objective, *a, **k):
        class R: success = False
        return R()
    import src.reasoning.handlers.peer_tasks as pt
    monkeypatch.setattr(pt, "submit_peer_task_handler", fake_submit)

    out = await pna.run_peer_cooperation_live_tick()
    # handler refuse → 0 exécutée, pas de crash, pas de comptage budget
    assert out["executed"] == 0
    assert gate.remaining_budget() == gate.max_per_hour()
