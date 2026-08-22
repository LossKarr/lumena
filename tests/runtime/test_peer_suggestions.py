"""C3-shadow — Journal d'observation des suggestions (persistance + câblage).

Couvre :
- ring buffer borné, plus récent en tête ;
- `_log_shadow_proposal` enregistre bien la suggestion (visible hors SSE) ;
- isolation fichier via monkeypatch (zéro pollution du vrai `data/`).
"""
from __future__ import annotations

import pytest

from src.runtime import peer_suggestions as ps


@pytest.fixture(autouse=True)
def _isolate_file(tmp_path, monkeypatch):
    monkeypatch.setattr(ps, "_FILE", tmp_path / "peer_suggestions.json")
    yield


def test_record_then_recent_most_recent_first():
    ps.record({"objective": "A", "peer_name": "L1", "reason": "r", "peer_id": "p1", "score": 3})
    ps.record({"objective": "B", "peer_name": "L2", "reason": "r", "peer_id": "p2", "score": 4})
    items = ps.recent(10)
    assert [i["objective"] for i in items] == ["B", "A"]
    assert items[0]["peer_name"] == "L2"


def test_ring_buffer_bounded():
    for i in range(ps._MAX + 20):
        ps.record({"objective": f"obj-{i}", "peer_id": "p", "peer_name": "L"})
    items = ps.recent(ps._MAX)
    assert len(items) == ps._MAX
    assert items[0]["objective"] == f"obj-{ps._MAX + 19}"  # le tout dernier en tête


def test_record_ignores_non_dict():
    ps.record("pas un dict")  # type: ignore[arg-type]
    assert ps.recent(5) == []


def test_clear_for_tests_empties():
    ps.record({"objective": "X", "peer_id": "p", "peer_name": "L"})
    ps.clear_for_tests()
    assert ps.recent(5) == []


def test_log_shadow_proposal_persists(monkeypatch):
    """Le pont C3 : une proposition shadow atterrit dans le journal observable."""
    from src.runtime import peer_network_autonomy as pna
    # neutralise ledger + SSE pour isoler la persistance
    import src.autonomy.activity_ledger as ledger
    monkeypatch.setattr(ledger, "append_autonomy_event", lambda *a, **k: None)
    import src.runtime.peer_event_bus as bus
    monkeypatch.setattr(bus, "publish_peer_event", lambda *a, **k: None)

    pna._log_shadow_proposal({"objective": "Rédiger un rapport", "peer_id": "p1",
                              "peer_name": "Lumena B", "reason": "mission", "score": 5})
    items = ps.recent(5)
    assert len(items) == 1
    assert items[0]["objective"] == "Rédiger un rapport"
    assert items[0]["peer_name"] == "Lumena B"
