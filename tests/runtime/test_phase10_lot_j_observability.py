"""Phase 10 Lot J — Robustesse production / observabilité."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import peers as peers_module
from web.routes.peers import router as peers_router


ROOT = Path(__file__).resolve().parents[2]
INDEX = ROOT / "web" / "index.html"
PANELS = ROOT / "web" / "static" / "js" / "panels.js"
MAIN = ROOT / "web" / "static" / "js" / "main.js"

AUTH = {"Authorization": "Bearer tok"}


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(peers_router)
    return app


def _write_registry(path: Path, peers: dict) -> None:
    path.write_text(json.dumps(peers, ensure_ascii=False, indent=2), encoding="utf-8")


TRUSTED_PEER = {
    "instance_id": "peer-j",
    "instance_name": "Lumena J",
    "host": "192.168.1.80",
    "port": 8081,
    "capabilities": ["chat"],
    "trust": "trusted",
    "peer_token_hash": "ab" * 32,
    "peer_token_outbound": "tok-out",
    "allowed_scopes": ["chat", "knowledge.share", "task.delegate"],
    "last_seen": datetime.now(timezone.utc).isoformat(),
}


@pytest.fixture()
def client(tmp_path, monkeypatch) -> TestClient:
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    reg = tmp_path / "peer_registry.json"
    _write_registry(reg, {TRUSTED_PEER["instance_id"]: TRUSTED_PEER})
    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg)
    monkeypatch.setattr(peers_module, "_PEER_TASK_EVENTS_FILE", tmp_path / "peer_tasks" / "task_events.jsonl")
    import src.runtime.peer_protocol as protocol
    monkeypatch.setattr(protocol, "PEER_AUDIT_LOG", tmp_path / "peer_audit.jsonl")
    with peers_module._async_tasks_lock:
        peers_module._async_task_store.clear()
    return TestClient(_make_app(), raise_server_exceptions=False)


def _append_jsonl(path: Path, *entries: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


class TestPeerMetrics:
    def test_metrics_requires_admin_auth(self, client):
        r = client.get("/api/peer/metrics")
        assert r.status_code in (401, 403)

    def test_metrics_aggregates_audit_and_task_events(self, client, tmp_path, monkeypatch):
        import src.runtime.peer_protocol as protocol

        _append_jsonl(
            protocol.PEER_AUDIT_LOG,
            {
                "ts": "2026-05-08T00:00:00+00:00",
                "event": "delegate_completed",
                "from_instance_id": "peer-j",
                "task_id": "d1",
                "scope": "chat",
                "status": "completed",
                "detail": "duration_ms=42",
            },
            {
                "ts": "2026-05-08T00:00:01+00:00",
                "event": "delegate_refused",
                "from_instance_id": "peer-j",
                "task_id": "d2",
                "scope": "task.delegate",
                "status": "refused",
                "detail": "Scope non autorisé",
            },
            {
                "ts": "2026-05-08T00:00:02+00:00",
                "event": "peer_rate_limited",
                "from_instance_id": "peer-j",
                "task_id": "d3",
                "scope": "task.delegate",
                "status": "rate_limited",
                "detail": "Rate limit exceeded",
            },
        )
        _append_jsonl(
            peers_module._PEER_TASK_EVENTS_FILE,
            {
                "ts": "2026-05-08T00:00:03+00:00",
                "event": "task_async_completed",
                "from_instance_id": "peer-j",
                "task_id": "t1",
                "scope": "task.delegate",
                "status": "completed",
                "detail": "duration_ms=100",
            },
        )
        with peers_module._async_tasks_lock:
            peers_module._async_task_store["active-j"] = {
                "status": "running",
                "from_instance_id": "peer-j",
                "created_at": "2026-05-08T00:00:04+00:00",
                "_created_mono": time.monotonic(),
            }

        r = client.get("/api/peer/metrics", headers=AUTH)
        assert r.status_code == 200
        data = r.json()
        assert data["peers"]["trusted"] == 1
        assert data["peers"]["with_peer_token"] == 1
        assert data["delegations"]["completed"] >= 1
        assert data["errors"]["scope"] >= 1
        assert data["errors"]["rate_limited"] >= 1
        assert data["tasks"]["active"] == 1
        assert data["latency"]["avg_ms"] == 71.0
        assert "Scope non autorisé" in data["user_issues"]


class TestPeersHealth:
    def test_health_reports_reachable_trusted_peer(self, client, monkeypatch):
        import src.runtime.peer_discovery as discovery

        async def _fake_probe(host, port, timeout=1.5):
            return {
                "instance_id": "peer-j",
                "instance_name": "Lumena J",
                "host": host,
                "port": port,
            }

        monkeypatch.setattr(discovery, "probe_single_peer", _fake_probe)
        r = client.get("/api/peers/health", headers=AUTH)
        assert r.status_code == 200
        data = r.json()
        assert data["overall"] == "healthy"
        assert data["trusted_count"] == 1
        assert data["reachable_trusted"] == 1
        assert data["peers"][0]["reachable"] is True
        assert data["peers"][0]["status"] == "healthy"

    def test_health_marks_bad_host_without_probe(self, client, monkeypatch, tmp_path):
        reg = tmp_path / "peer_registry.json"
        bad = {**TRUSTED_PEER, "host": "8.8.8.8"}
        _write_registry(reg, {bad["instance_id"]: bad})
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg)

        r = client.get("/api/peers/health", headers=AUTH)
        assert r.status_code == 200
        data = r.json()
        assert data["overall"] == "down"
        assert data["peers"][0]["status"] == "invalid_host"
        assert data["peers"][0]["reachable"] is False


class TestPeerMaintenanceCleanup:
    def test_cleanup_dry_run_does_not_modify_files(self, client):
        import src.runtime.peer_protocol as protocol

        _append_jsonl(protocol.PEER_AUDIT_LOG, *[
            {"ts": f"2026-05-08T00:00:0{i}+00:00", "event": "x", "from_instance_id": "p", "task_id": str(i), "scope": "chat", "status": "completed"}
            for i in range(5)
        ])
        r = client.post(
            "/api/peer/maintenance/cleanup",
            headers=AUTH,
            json={"dry_run": True, "keep_audit_lines": 2, "keep_task_event_lines": 2},
        )
        assert r.status_code == 200
        assert r.json()["audit"]["removed"] == 3
        assert len(protocol.PEER_AUDIT_LOG.read_text(encoding="utf-8").splitlines()) == 5

    def test_cleanup_apply_trims_files(self, client):
        import src.runtime.peer_protocol as protocol

        _append_jsonl(protocol.PEER_AUDIT_LOG, *[
            {"ts": f"2026-05-08T00:00:0{i}+00:00", "event": "x", "from_instance_id": "p", "task_id": str(i), "scope": "chat", "status": "completed"}
            for i in range(5)
        ])
        r = client.post(
            "/api/peer/maintenance/cleanup",
            headers=AUTH,
            json={"dry_run": False, "keep_audit_lines": 2, "keep_task_event_lines": 2},
        )
        assert r.status_code == 200
        lines = protocol.PEER_AUDIT_LOG.read_text(encoding="utf-8").splitlines()
        assert len(lines) == 2
        assert json.loads(lines[0])["task_id"] == "3"


class TestLotJUiStatic:
    def test_observability_card_is_wired(self):
        html = INDEX.read_text(encoding="utf-8")
        js = PANELS.read_text(encoding="utf-8")
        main = MAIN.read_text(encoding="utf-8")

        assert 'id="net-observability-content"' in html
        assert "loadNetworkObservability()" in html
        assert "export async function loadNetworkObservability()" in js
        assert "/api/peer/metrics" in js
        assert "/api/peers/health" in js
        assert "/api/peer/maintenance/cleanup" in js
        assert "cleanupPeerRuntime" in main

