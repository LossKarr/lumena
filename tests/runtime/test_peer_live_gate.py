"""C3-live — Garde-fou d'exécution (budget / dedup / halt / présence).

Le gate décide si une délégation autonome peut partir MAINTENANT. Il gate le
FUTUR (jamais le présent). On vérifie chaque frein indépendamment + le tick live
qui n'exécute qu'une fois par nouvel objectif et jamais sous halt.
"""
from __future__ import annotations

import pytest

from src.runtime import peer_live_gate as gate


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    gate.clear_for_tests()
    # halt OFF + jamais bloqué par présence (sauf test dédié)
    monkeypatch.delenv("LUMENA_PEER_HALT", raising=False)
    monkeypatch.setenv("LUMENA_PEER_AUTONOMY_WHEN_PRESENT", "1")
    yield
    gate.clear_for_tests()


def test_allows_then_dedups_same_objective():
    assert gate.block_reason("Rédiger un rapport") == ""
    gate.record_delegation("Rédiger un rapport")
    # même objectif (casse/espaces ignorés) → bloqué
    assert gate.block_reason("  rédiger un RAPPORT ") == "recently_delegated"


def test_in_flight_blocks():
    assert gate.block_reason("Tâche X", in_flight_objectives=["Tâche X"]) == "in_flight"


def test_empty_objective_blocked():
    assert gate.block_reason("   ") == "empty"


def test_hourly_budget(monkeypatch):
    monkeypatch.setenv("LUMENA_PEER_AUTONOMY_MAX_PER_HOUR", "2")
    gate.record_delegation("a")
    gate.record_delegation("b")
    assert gate.remaining_budget() == 0
    assert gate.block_reason("c") == "hourly_budget"


def test_halt_blocks_new(monkeypatch):
    monkeypatch.setenv("LUMENA_PEER_HALT", "1")
    assert gate.block_reason("quoi que ce soit") == "halt"


def test_user_present_blocks_by_default(monkeypatch):
    monkeypatch.setenv("LUMENA_PEER_AUTONOMY_WHEN_PRESENT", "0")
    import src.autonomy.presence as presence
    monkeypatch.setattr(presence, "is_user_present", lambda *a, **k: True)
    assert gate.block_reason("tâche") == "user_present"


def test_when_present_flag_lets_it_act(monkeypatch):
    monkeypatch.setenv("LUMENA_PEER_AUTONOMY_WHEN_PRESENT", "1")
    import src.autonomy.presence as presence
    monkeypatch.setattr(presence, "is_user_present", lambda *a, **k: True)
    assert gate.block_reason("tâche") == ""
