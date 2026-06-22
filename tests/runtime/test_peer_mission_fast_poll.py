"""Poll adaptatif des missions sortantes : retour rapide du verdict du pair.

Régression du trou runtime (log A 04:13) : A restait sur « ⏳ en cours » car le
poll était couplé au cycle santé 60 s. Désormais : tick léger ~8 s tant qu'une
mission est en attente → l'émetteur apprend le refused/completed quasi tout de suite.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from src.runtime import peer_network_autonomy as pna


@pytest.mark.asyncio
async def test_tick_polls_when_pending():
    with patch("src.runtime.peer_mission_tracker.list_pending", return_value=[{"task_id": "ta-1"}]), \
         patch("src.runtime.peer_mission_tracker.poll_outbound_missions", new=AsyncMock(return_value={"polled": 1})) as poll:
        launched = await pna._poll_pending_missions_tick(timeout=1.0)
    assert launched is True
    poll.assert_awaited_once()


@pytest.mark.asyncio
async def test_tick_noop_when_no_pending():
    with patch("src.runtime.peer_mission_tracker.list_pending", return_value=[]), \
         patch("src.runtime.peer_mission_tracker.poll_outbound_missions", new=AsyncMock()) as poll:
        launched = await pna._poll_pending_missions_tick(timeout=1.0)
    assert launched is False
    poll.assert_not_awaited()


@pytest.mark.asyncio
async def test_tick_never_raises_on_error():
    with patch("src.runtime.peer_mission_tracker.list_pending", side_effect=RuntimeError("boom")):
        launched = await pna._poll_pending_missions_tick(timeout=1.0)
    assert launched is False  # erreur avalée, jamais fatal


def test_mission_poll_interval_clamped(monkeypatch):
    # borné [3, 120]
    monkeypatch.setenv("LUMENA_PEER_MISSION_POLL_SEC", "1")
    assert pna._clamp_int(os.getenv("LUMENA_PEER_MISSION_POLL_SEC"), 8, 3, 120) == 3
    monkeypatch.setenv("LUMENA_PEER_MISSION_POLL_SEC", "999")
    assert pna._clamp_int(os.getenv("LUMENA_PEER_MISSION_POLL_SEC"), 8, 3, 120) == 120
    monkeypatch.setenv("LUMENA_PEER_MISSION_POLL_SEC", "10")
    assert pna._clamp_int(os.getenv("LUMENA_PEER_MISSION_POLL_SEC"), 8, 3, 120) == 10
