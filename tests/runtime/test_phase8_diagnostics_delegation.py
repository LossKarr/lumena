"""Tests Phase 8.1 (diagnostic réseau) et Phase 8.6 (test délégation)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Generator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import peers as peers_module
from web.routes.peers import router as peers_router


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(peers_router)
    return app


@pytest.fixture()
def authed(tmp_path, monkeypatch) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
    monkeypatch.setattr(_paths, "INSTANCE_NAME", "Lumena Principal")
    monkeypatch.setattr(_paths, "INSTANCE_ROLE", "standalone")
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    monkeypatch.setenv("LUMENA_PORT", "8080")
    with TestClient(_make_app(), raise_server_exceptions=True) as c:
        yield c


AUTH = {"Authorization": "Bearer tok"}


# ─────────────────────────────────────────────────────────────────────────────
# Phase 8.1 — Diagnostic réseau
# ─────────────────────────────────────────────────────────────────────────────

class TestNetworkDiagnosticRoute:

    def test_requires_auth(self, authed):
        r = authed.get("/api/instance/network-diagnostic")
        assert r.status_code in (401, 403)

    def test_returns_required_fields(self, authed):
        with patch("src.runtime.network_diagnostics.build_network_diagnostic",
                   return_value={"ok": True, "instance_id": "self-001", "host": "0.0.0.0",
                                 "port": 8080, "lan_ips": ["192.168.1.10"],
                                 "listening": True, "network_accessible": True,
                                 "firewall_check": "not_applicable", "issues": [],
                                 "suggested_actions": []}):
            r = authed.get("/api/instance/network-diagnostic", headers=AUTH)
        assert r.status_code == 200
        d = r.json()
        for field in ("ok", "instance_id", "host", "port", "lan_ips",
                      "listening", "network_accessible", "firewall_check",
                      "issues", "suggested_actions"):
            assert field in d, f"Champ manquant : {field}"

    def test_ok_true_when_no_errors(self, authed):
        with patch("src.runtime.network_diagnostics.build_network_diagnostic",
                   return_value={"ok": True, "instance_id": "self-001", "host": "0.0.0.0",
                                 "port": 8080, "lan_ips": ["192.168.1.10"],
                                 "listening": True, "network_accessible": True,
                                 "firewall_check": "not_applicable", "issues": [],
                                 "suggested_actions": []}):
            r = authed.get("/api/instance/network-diagnostic", headers=AUTH)
        assert r.json()["ok"] is True

    def test_ok_false_when_bind_localhost(self, authed):
        diag = {
            "ok": False, "instance_id": "self-001", "host": "127.0.0.1",
            "port": 8080, "lan_ips": [], "listening": True,
            "network_accessible": False, "firewall_check": "not_applicable",
            "issues": [{"code": "bind_localhost_only", "severity": "error",
                        "message": "Lumena écoute uniquement sur 127.0.0.1"}],
            "suggested_actions": ["set_lumena_host_0000"],
        }
        with patch("src.runtime.network_diagnostics.build_network_diagnostic", return_value=diag):
            r = authed.get("/api/instance/network-diagnostic", headers=AUTH)
        d = r.json()
        assert d["ok"] is False
        assert any(i["code"] == "bind_localhost_only" for i in d["issues"])


class TestNetworkDiagnosticsModule:
    """Tests unitaires de src/runtime/network_diagnostics.py."""

    def test_is_private_lan_accepts_rfc1918(self):
        from src.runtime.network_diagnostics import _is_private_lan
        assert _is_private_lan("192.168.1.10") is True
        assert _is_private_lan("10.0.0.1") is True
        assert _is_private_lan("172.16.0.1") is True

    def test_is_private_lan_rejects_loopback(self):
        from src.runtime.network_diagnostics import _is_private_lan
        assert _is_private_lan("127.0.0.1") is False

    def test_is_private_lan_rejects_public(self):
        from src.runtime.network_diagnostics import _is_private_lan
        assert _is_private_lan("8.8.8.8") is False

    def test_is_private_lan_rejects_link_local(self):
        from src.runtime.network_diagnostics import _is_private_lan
        assert _is_private_lan("169.254.0.1") is False

    def test_check_bind_host_0000_is_accessible(self, monkeypatch):
        from src.runtime import network_diagnostics as nd
        monkeypatch.setenv("LUMENA_HOST", "0.0.0.0")
        r = nd.check_bind_host()
        assert r["network_accessible"] is True

    def test_check_bind_host_loopback_not_accessible(self, monkeypatch):
        from src.runtime import network_diagnostics as nd
        monkeypatch.setenv("LUMENA_HOST", "127.0.0.1")
        r = nd.check_bind_host()
        assert r["network_accessible"] is False

    def test_check_port_listening_closed_port(self):
        from src.runtime.network_diagnostics import check_port_listening
        r = check_port_listening(1)  # port 1 est fermé
        assert r["listening"] is False

    def test_build_diagnostic_ok_false_when_not_listening(self, monkeypatch):
        from src.runtime import network_diagnostics as nd
        monkeypatch.setenv("LUMENA_PORT", "19999")
        monkeypatch.setenv("LUMENA_HOST", "0.0.0.0")
        with patch("src.runtime.network_diagnostics.check_port_listening",
                   return_value={"listening": False, "port": 19999}), \
             patch("src.runtime.network_diagnostics.check_firewall_hint",
                   return_value={"firewall_check": "not_applicable", "platform": "Linux"}):
            diag = nd.build_network_diagnostic()
        assert diag["ok"] is False
        assert any(i["code"] == "port_not_listening" for i in diag["issues"])

    def test_build_diagnostic_suggests_firewall_action(self, monkeypatch):
        from src.runtime import network_diagnostics as nd
        monkeypatch.setenv("LUMENA_PORT", "8080")
        monkeypatch.setenv("LUMENA_HOST", "0.0.0.0")
        with patch("src.runtime.network_diagnostics.check_port_listening",
                   return_value={"listening": True, "port": 8080}), \
             patch("src.runtime.network_diagnostics.check_firewall_hint",
                   return_value={"platform": "Windows", "firewall_rule_found": False,
                                 "firewall_check": "possible_block"}):
            diag = nd.build_network_diagnostic()
        assert "open_windows_firewall_port" in diag["suggested_actions"]
        assert any(i["code"] == "firewall_possible_block" for i in diag["issues"])


# ─────────────────────────────────────────────────────────────────────────────
# Phase 8.6 — Test délégation en un clic
# ─────────────────────────────────────────────────────────────────────────────

class TestDelegationRoute:

    def _seed_trusted_peer(self, tmp_path):
        reg = tmp_path / "peer_registry.json"
        reg.write_text(json.dumps({"remote-inst": {
            "instance_id": "remote-inst", "trust": "trusted",
            "instance_name": "Lumena Remote", "host": "192.168.1.10", "port": 8081,
            # Phase 8.5 — peer token outbound requis pour que test-delegation puisse appeler
            "peer_token_outbound": "test-peer-token-abc123",
            "peer_token_hash": "dummy-hash",
            "allowed_scopes": ["chat"],
        }}), encoding="utf-8")

    def test_requires_auth(self, authed):
        r = authed.post("/api/peer/test-delegation", json={"instance_id": "x"})
        assert r.status_code in (401, 403)

    def test_unknown_peer_returns_404(self, authed):
        r = authed.post("/api/peer/test-delegation",
                        json={"instance_id": "ghost"}, headers=AUTH)
        assert r.status_code == 404

    def test_untrusted_peer_returns_403(self, authed, tmp_path, monkeypatch):
        reg = tmp_path / "peer_registry.json"
        reg.write_text(json.dumps({"unk-inst": {
            "instance_id": "unk-inst", "trust": "unknown",
            "host": "192.168.1.10", "port": 8081,
        }}), encoding="utf-8")
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg)
        with TestClient(_make_app()) as c:
            r = c.post("/api/peer/test-delegation",
                       json={"instance_id": "unk-inst"},
                       headers={"Authorization": "Bearer tok"})
        assert r.status_code == 403

    def test_successful_delegation(self, tmp_path, monkeypatch):
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")
        import src.utils.paths as _paths
        monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
        monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
        monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
        self._seed_trusted_peer(tmp_path)
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"task_id": "t-001", "status": "completed",
                                       "response": "Délégation OK.", "evidence": [], "logs": []}

        async def fake_post(*args, **kwargs):
            return mock_resp

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = fake_post
            mock_client_cls.return_value = mock_client

            with TestClient(_make_app()) as c:
                r = c.post("/api/peer/test-delegation",
                           json={"instance_id": "remote-inst"},
                           headers={"Authorization": "Bearer tok"})

        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is True
        assert d["status"] == "completed"
        assert "latency_ms" in d

    def test_network_error_returns_ok_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")
        import src.utils.paths as _paths
        monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
        monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
        monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
        self._seed_trusted_peer(tmp_path)
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")

        async def fail_post(*args, **kwargs):
            raise ConnectionRefusedError("Connection refused")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = fail_post
            mock_client_cls.return_value = mock_client

            with TestClient(_make_app(), raise_server_exceptions=False) as c:
                r = c.post("/api/peer/test-delegation",
                           json={"instance_id": "remote-inst"},
                           headers={"Authorization": "Bearer tok"})

        assert r.status_code == 200
        d = r.json()
        assert d["ok"] is False
        assert "latency_ms" in d
        assert d["status"] == "error"
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
