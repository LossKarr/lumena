"""Cran 2 — Bus d'événements P2P (push SSE) + hooks du mission tracker.

Couvre :
- publish/subscribe : un abonné reçoit l'événement publié (fan-out thread-safe).
- enrichissement : ts / seq / type ajoutés.
- hooks tracker : register_outbound_mission → 'queued', update_status → statut.
- sanitisation : aucun token / résultat brut ne transite par le bus.
"""
from __future__ import annotations

import asyncio

import pytest

from src.runtime.peer_event_bus import get_peer_event_bus, publish_peer_event


@pytest.mark.asyncio
async def test_publish_reaches_subscriber():
    bus = get_peer_event_bus()
    bus.clear_for_tests()
    sid, q = bus.subscribe()
    try:
        publish_peer_event("mission", task_id="t1", status="running")
        ev = await asyncio.wait_for(q.get(), timeout=1.0)
        assert ev["type"] == "mission"
        assert ev["task_id"] == "t1"
        assert ev["status"] == "running"
        assert "ts" in ev and "seq" in ev
    finally:
        bus.unsubscribe(sid)


@pytest.mark.asyncio
async def test_seq_increments_and_fanout_two_subscribers():
    bus = get_peer_event_bus()
    bus.clear_for_tests()
    s1, q1 = bus.subscribe()
    s2, q2 = bus.subscribe()
    try:
        publish_peer_event("mission", task_id="a", status="queued")
        publish_peer_event("mission", task_id="b", status="completed")
        e1 = await asyncio.wait_for(q1.get(), timeout=1.0)
        e2 = await asyncio.wait_for(q1.get(), timeout=1.0)
        assert e2["seq"] == e1["seq"] + 1
        # le 2e abonné reçoit aussi
        f1 = await asyncio.wait_for(q2.get(), timeout=1.0)
        assert f1["task_id"] == "a"
    finally:
        bus.unsubscribe(s1)
        bus.unsubscribe(s2)


@pytest.mark.asyncio
async def test_tracker_publishes_lifecycle(tmp_path, monkeypatch):
    """register → 'queued' puis update_status → 'running' arrivent sur le bus,
    sans token ni résultat brut."""
    import src.runtime.peer_mission_tracker as tracker
    monkeypatch.setattr(tracker, "_TRACKER_FILE", tmp_path / "missions.json")

    bus = get_peer_event_bus()
    bus.clear_for_tests()
    sid, q = bus.subscribe()
    try:
        tracker.register_outbound_mission(
            task_id="ta-1", peer_id="peer-1", peer_name="Lumena Salon",
            host="192.168.1.50", port=8081, objective="Coder un script",
        )
        ev = await asyncio.wait_for(q.get(), timeout=1.0)
        assert ev["type"] == "mission"
        assert ev["task_id"] == "ta-1"
        assert ev["status"] == "queued"
        assert ev["peer_name"] == "Lumena Salon"
        # sanitisation : pas de secret/payload brut
        for forbidden in ("result", "peer_token", "peer_token_outbound", "web_ack", "notified"):
            assert forbidden not in ev

        tracker.update_status("ta-1", "running")
        ev2 = await asyncio.wait_for(q.get(), timeout=1.0)
        assert ev2["status"] == "running"
        assert ev2["task_id"] == "ta-1"

        tracker.update_status("ta-1", "completed", result="SECRET_RESULT_DO_NOT_LEAK" * 50)
        ev3 = await asyncio.wait_for(q.get(), timeout=1.0)
        assert ev3["status"] == "completed"
        assert "SECRET_RESULT_DO_NOT_LEAK" not in str(ev3)
    finally:
        bus.unsubscribe(sid)


def test_publish_helper_never_raises():
    # Charge utile non sérialisable-friendly : ne doit jamais lever.
    assert isinstance(publish_peer_event("mission", task_id="x"), dict)
