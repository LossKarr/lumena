"""Phase 11C - peer network autonomy.

Autonomy is deliberately conservative:
- it updates health metadata;
- it discovers unknown peers;
- it never auto-trusts a peer or creates tokens.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import peers as peers_module
from web.routes.peers import router as peers_router


def _write_registry(path, peers: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(peers, ensure_ascii=False, indent=2), encoding="utf-8")


def _trusted(iid: str = "peer-ok", host: str = "192.168.1.50") -> dict:
    return {
        "instance_id": iid,
        "instance_name": iid,
        "host": host,
        "port": 8081,
        "trust": "trusted",
        "capabilities": ["chat"],
        "allowed_scopes": ["chat"],
        "peer_token_hash": "hash",
        "peer_token_outbound": "tok",
        "last_seen": datetime.now(timezone.utc).isoformat(),
    }


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(peers_router)
    return app


@pytest.fixture()
def autonomy(tmp_path, monkeypatch):
    from src.runtime import peer_network_autonomy as mod

    reg = tmp_path / "peer_registry.json"
    monkeypatch.setattr(mod, "_PEER_REGISTRY_FILE", reg)
    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg)
    with mod._STATE_LOCK:
        mod._STATE.update({
            "enabled": False,
            "running": False,
            "last_run_at": "",
            "last_scan_at": "",
            "last_health_at": "",
            "last_error": "",
            "last_summary": {},
        })
    monkeypatch.setenv("LUMENA_PEER_NETWORK_AUTONOMY", "1")
    monkeypatch.setenv("LUMENA_PEER_DISCOVERY", "1")
    monkeypatch.setenv("LUMENA_PEER_AUTONOMY_TIMEOUT", "0.2")
    monkeypatch.setenv("LUMENA_PEER_AUTOSCAN_MAX_HOSTS", "2")
    return mod, reg


class TestPeerNetworkAutonomy:

    def test_summary_marks_unknown_as_needs_pairing(self, autonomy):
        mod, reg = autonomy
        _write_registry(reg, {
            "trusted": _trusted("trusted"),
            "unknown": {**_trusted("unknown"), "trust": "unknown", "peer_token_outbound": ""},
            "blocked": {**_trusted("blocked"), "trust": "blocked"},
        })

        status = mod.get_peer_network_autonomy_status()
        summary = status["last_summary"]

        assert summary["known"] == 3
        assert summary["trusted"] == 1
        assert summary["unknown"] == 1
        assert summary["blocked"] == 1
        assert summary["needs_pairing"][0]["instance_id"] == "unknown"

    @pytest.mark.asyncio
    async def test_health_updates_reachable_peer_without_touching_tokens(self, autonomy, monkeypatch):
        mod, reg = autonomy
        _write_registry(reg, {"peer-ok": _trusted("peer-ok")})

        async def fake_probe(host, port, timeout=3.0):
            return {"instance_id": "peer-ok", "instance_name": "peer-ok"}

        import src.runtime.peer_discovery as discovery
        monkeypatch.setattr(discovery, "probe_single_peer", fake_probe)

        status = await mod.run_peer_network_autonomy_once(scan=False, health=True)
        data = json.loads(reg.read_text(encoding="utf-8"))

        assert status["last_summary"]["reachable_trusted"] == 1
        assert data["peer-ok"]["autonomy_status"] == "reachable"
        assert data["peer-ok"]["peer_token_outbound"] == "tok"
        assert data["peer-ok"]["trust"] == "trusted"

    @pytest.mark.asyncio
    async def test_scan_adds_discovered_peer_as_unknown_only(self, autonomy, monkeypatch):
        mod, reg = autonomy
        _write_registry(reg, {})

        async def fake_scan_lan_for_peers(network=None, ports=None, timeout=1.5, max_hosts=254):
            return [{
                "instance_id": "peer-new",
                "instance_name": "Salon",
                "host": "192.168.1.57",
                "port": 8081,
                "version": "1.0.35",
                "role": "standalone",
                "capabilities": ["chat", "browser"],
                "requires_pairing": True,
                "trust": "unknown",
            }]

        import src.runtime.peer_discovery as discovery
        import src.runtime.network_diagnostics as diag
        monkeypatch.setattr(discovery, "scan_lan_for_peers", fake_scan_lan_for_peers)
        monkeypatch.setattr(diag, "get_network_interfaces", lambda: [{"network": "192.168.1.0/24"}])

        status = await mod.run_peer_network_autonomy_once(scan=True, health=False)
        data = json.loads(reg.read_text(encoding="utf-8"))

        assert status["last_summary"]["discovered"] == 1
        assert data["peer-new"]["trust"] == "unknown"
        assert "peer_token_outbound" not in data["peer-new"]
        assert data["peer-new"]["autonomy_status"] == "discovered"

    def test_chat_context_mentions_autonomy_without_secrets(self, autonomy):
        mod, reg = autonomy
        _write_registry(reg, {
            "unknown": {**_trusted("unknown"), "trust": "unknown", "peer_token_outbound": ""},
        })

        ctx = mod.build_peer_network_context()

        assert "Autonomie reseau Lumena" in ctx
        assert "a jumeler: 1" in ctx
        assert "tok" not in ctx
        assert "hash" not in ctx


class TestPeerAutonomyRoutes:

    def test_status_route_is_admin_protected_and_sanitized(self, autonomy):
        mod, reg = autonomy
        _write_registry(reg, {"peer-ok": _trusted("peer-ok")})
        app = _make_app()
        app.dependency_overrides[peers_module.deps.verify_admin_token] = lambda: True
        client = TestClient(app, raise_server_exceptions=False)

        r = client.get("/api/peer/autonomy/status")

        assert r.status_code == 200
        data = r.json()
        assert data["enabled"] is True
        assert "last_summary" in data
        assert "peer_token_outbound" not in json.dumps(data)

    def test_run_once_route_calls_autonomy(self, autonomy, monkeypatch):
        mod, _reg = autonomy

        async def fake_run_once(scan=True, health=True):
            return {"ok": True, "scan": scan, "health": health}

        monkeypatch.setattr(mod, "run_peer_network_autonomy_once", fake_run_once)
        app = _make_app()
        app.dependency_overrides[peers_module.deps.verify_admin_token] = lambda: True
        client = TestClient(app, raise_server_exceptions=False)

        r = client.post("/api/peer/autonomy/run-once", json={"scan": False, "health": True})

        assert r.status_code == 200
        assert r.json() == {"ok": True, "scan": False, "health": True}
