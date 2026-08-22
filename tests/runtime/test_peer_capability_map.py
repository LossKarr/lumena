"""Bloc C2 — Carte des capacités vivante (substrat de décision de C3).

Couvre :
- agrège capacités/scopes/niveau par pair trusted ;
- joignabilité dérivée de la fraîcheur de last_seen ;
- quarantaine reflétée ; `delegable` = appelable+joignable+non-quarantaine+scopes ;
- exclut unknown/blocked/sans-token ; endpoint + auth.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.runtime.peer_awareness as pa
import src.runtime.peer_quarantine as quar
from web.routes import peers as peers_module
from web.routes.peers import router as peers_router


AUTH = {"Authorization": "Bearer tok"}


def _iso(seconds_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def _peer(pid, *, scopes=("chat", "task.delegate"), token=True, seen=10, trust="trusted", level="mission"):
    return {
        "instance_id": pid, "instance_name": f"Lumena {pid}", "host": "192.168.1.50", "port": 8081,
        "trust": trust, "capabilities": ["chat", "browser"],
        "allowed_scopes": list(scopes), "capability_level": level,
        "peer_token_outbound": "tok" if token else "",
        "last_seen": _iso(seen),
    }


@pytest.fixture()
def reg(tmp_path, monkeypatch):
    f = tmp_path / "peer_registry.json"
    monkeypatch.setattr(pa, "_PEER_REGISTRY_FILE", f)
    monkeypatch.setattr(quar, "_FILE", tmp_path / "quarantine.json")
    quar.clear_for_tests()
    def _write(peers):
        f.write_text(json.dumps({p["instance_id"]: p for p in peers}), encoding="utf-8")
    return _write


class TestBuildMap:
    def test_aggregates_trusted_peer(self, reg):
        reg([_peer("p1", seen=5)])
        m = pa.build_capability_map()
        assert m["count"] == 1
        p = m["peers"][0]
        assert p["name"] == "Lumena p1"
        assert "task.delegate" in p["allowed_scopes"]
        assert p["capability_level"] == "mission"
        assert p["reachable"] is True
        assert p["delegable"] is True

    def test_stale_peer_not_reachable_not_delegable(self, reg):
        reg([_peer("p1", seen=10_000)])  # vu il y a longtemps
        p = pa.build_capability_map(fresh_sec=300)["peers"][0]
        assert p["reachable"] is False
        assert p["delegable"] is False

    def test_quarantined_not_delegable(self, reg):
        reg([_peer("p1", seen=5)])
        for _ in range(5):
            quar.record_anomaly("p1")
        p = pa.build_capability_map()["peers"][0]
        assert p["quarantined"] is True
        assert p["delegable"] is False  # joignable mais isolé

    def test_no_scopes_not_delegable(self, reg):
        reg([_peer("p1", seen=5, scopes=())])
        p = pa.build_capability_map()["peers"][0]
        assert p["delegable"] is False

    def test_excludes_unknown_blocked_and_tokenless(self, reg):
        reg([
            _peer("ok", seen=5),
            _peer("unk", trust="unknown"),
            _peer("blk", trust="blocked"),
            _peer("notok", token=False),
        ])
        ids = {p["instance_id"] for p in pa.build_capability_map()["peers"]}
        assert ids == {"ok"}

    def test_delegable_count_and_sort(self, reg):
        reg([_peer("stale", seen=9999), _peer("ready", seen=5)])
        m = pa.build_capability_map()
        assert m["delegable_count"] == 1
        assert m["peers"][0]["instance_id"] == "ready"  # délégable en premier


# ── endpoint ──────────────────────────────────────────────────────────────────

def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(peers_router)
    return app


@pytest.fixture()
def authed(tmp_path, monkeypatch, reg) -> Generator[TestClient, None, None]:
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
    monkeypatch.setattr(_paths, "INSTANCE_NAME", "Lumena Principal")
    monkeypatch.setattr(_paths, "INSTANCE_ROLE", "standalone")
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    reg([_peer("p1", seen=5)])
    with TestClient(_make_app(), raise_server_exceptions=True) as c:
        yield c


def test_endpoint_returns_map(authed):
    r = authed.get("/api/peer/capability-map", headers=AUTH)
    assert r.status_code == 200
    d = r.json()
    assert d["count"] == 1 and d["delegable_count"] == 1


def test_endpoint_requires_admin(authed):
    assert authed.get("/api/peer/capability-map").status_code == 401
