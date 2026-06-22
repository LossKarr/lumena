"""Refonte UI — tests de l'agrégation READ-ONLY de l'historique des échanges."""
from __future__ import annotations

from src.runtime.peer_history import (
    build_peer_history,
    history_stats,
    history_type_for_event,
    sanitize_detail,
)

OWN = "inst-self"
PEER = "inst-peer-B"
NAMES = {PEER: "Lumena-B", OWN: "Lumena-A"}


# ── Classification des événements ────────────────────────────────────────────

def test_event_type_mapping():
    assert history_type_for_event("delegate_completed") == "delegation"
    assert history_type_for_event("task_sync_started") == "task"
    assert history_type_for_event("task_async_completed") == "task"
    assert history_type_for_event("knowledge_query_completed") == "knowledge_query"
    assert history_type_for_event("mission_completed") == "mission"


def test_pairing_and_system_events_excluded():
    assert history_type_for_event("fleet_pair_completed") is None
    assert history_type_for_event("peer_rate_limited") is None
    assert history_type_for_event("") is None


# ── Sanitization ─────────────────────────────────────────────────────────────

def test_sanitize_detail_redacts_secret_and_truncates():
    secret = "a" * 40  # hex32+ → pattern secret
    out = sanitize_detail(f"token={secret}")
    assert secret not in out
    assert "[REDACTED]" in out
    assert len(sanitize_detail("x" * 1000)) <= 300


# ── Agrégation en fils ───────────────────────────────────────────────────────

def test_groups_events_by_task_id():
    audit = [
        {"ts": "2026-06-17T10:00:00Z", "event": "delegate_accepted", "from_instance_id": PEER,
         "task_id": "t1", "scope": "chat", "status": "running", "detail": ""},
        {"ts": "2026-06-17T10:00:05Z", "event": "delegate_completed", "from_instance_id": PEER,
         "task_id": "t1", "scope": "chat", "status": "completed", "detail": "duration_ms=120"},
    ]
    out = build_peer_history(audit=audit, task_events=[], knowledge=[], own_id=OWN, peer_names=NAMES)
    assert len(out) == 1
    th = out[0]
    assert th["id"] == "task:t1"
    assert th["type"] == "delegation"
    assert th["peer_name"] == "Lumena-B"
    assert th["direction"] == "inbound"  # PEER != OWN
    assert th["status"] == "completed"   # dernier statut
    assert len(th["items"]) == 2
    # items triés par ts croissant
    assert th["items"][0]["event"] == "delegate_accepted"


def test_knowledge_thread_and_direction():
    knowledge = [{
        "id": "k1", "title": "Redis cache", "summary": "use redis",
        "origin_instance_id": OWN, "shared_with_peer_id": PEER,
        "created_at": "2026-06-17T09:00:00Z",
    }]
    out = build_peer_history(audit=[], task_events=[], knowledge=knowledge, own_id=OWN, peer_names=NAMES)
    assert len(out) == 1
    th = out[0]
    assert th["type"] == "knowledge"
    assert th["peer_id"] == PEER            # on est origine → l'autre = destinataire
    assert th["direction"] == "outbound"


def test_threads_sorted_by_recency():
    audit = [
        {"ts": "2026-06-17T08:00:00Z", "event": "task_sync_completed", "from_instance_id": PEER,
         "task_id": "old", "scope": "task.delegate", "status": "completed", "detail": ""},
        {"ts": "2026-06-17T12:00:00Z", "event": "task_sync_completed", "from_instance_id": PEER,
         "task_id": "new", "scope": "task.delegate", "status": "completed", "detail": ""},
    ]
    out = build_peer_history(audit=audit, task_events=[], knowledge=[], own_id=OWN, peer_names=NAMES)
    assert [t["id"] for t in out] == ["task:new", "task:old"]


def test_events_without_task_id_ignored():
    audit = [{"ts": "t", "event": "delegate_refused", "from_instance_id": PEER,
              "task_id": "", "scope": "chat", "status": "refused", "detail": "x"}]
    out = build_peer_history(audit=audit, task_events=[], knowledge=[], own_id=OWN, peer_names=NAMES)
    assert out == []


def test_no_secret_leaks_into_items():
    secret = "b" * 40
    audit = [{"ts": "t", "event": "task_sync_failed", "from_instance_id": PEER,
              "task_id": "t9", "scope": "task.delegate", "status": "error",
              "detail": f"boom token={secret}"}]
    out = build_peer_history(audit=audit, task_events=[], knowledge=[], own_id=OWN, peer_names=NAMES)
    assert secret not in out[0]["items"][0]["detail"]


def test_limit_respected():
    audit = [
        {"ts": f"2026-06-17T10:00:{i:02d}Z", "event": "task_sync_completed",
         "from_instance_id": PEER, "task_id": f"t{i}", "scope": "task.delegate",
         "status": "completed", "detail": ""}
        for i in range(20)
    ]
    out = build_peer_history(audit=audit, task_events=[], knowledge=[], own_id=OWN, peer_names=NAMES, limit=5)
    assert len(out) == 5


def test_outbound_missions_become_threads():
    missions = [{
        "task_id": "ta-1", "peer_id": PEER, "peer_name": "Lumena-B",
        "objective": "préparer un site", "status": "completed",
        "submitted_at": "2026-06-17T10:00:00Z", "last_poll": "2026-06-17T10:05:00Z",
        "result": "site prêt",
    }]
    out = build_peer_history(audit=[], task_events=[], knowledge=[], own_id=OWN,
                             peer_names={}, missions=missions)
    assert len(out) == 1
    th = out[0]
    assert th["id"] == "mission:ta-1"
    assert th["type"] == "mission"
    assert th["direction"] == "outbound"
    assert th["peer_name"] == "Lumena-B"   # nom porté par la mission
    assert th["status"] == "completed"
    # 2 items : assignée + complétée
    events = [i["event"] for i in th["items"]]
    assert "mission_assigned" in events and "mission_completed" in events


def test_mission_running_status_in_stats():
    missions = [{"task_id": "ta-2", "peer_id": PEER, "peer_name": "B",
                 "objective": "analyse", "status": "running",
                 "submitted_at": "2026-06-17T09:00:00Z"}]
    out = build_peer_history(audit=[], task_events=[], knowledge=[], own_id=OWN,
                             peer_names={}, missions=missions)
    assert history_stats(out)["running"] == 1


def test_stats():
    exchanges = [
        {"status": "completed", "type": "delegation"},
        {"status": "running", "type": "task"},
        {"status": "shared", "type": "knowledge"},
    ]
    s = history_stats(exchanges)
    assert s == {"total": 3, "completed": 1, "running": 1, "knowledge": 1}
