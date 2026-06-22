"""Tests Phase 7 — Route GET /api/instances/local."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator, List
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import peers as peers_module
from web.routes.peers import router as peers_router


# ── App de test isolée ────────────────────────────────────────────────────────

def _make_test_app() -> FastAPI:
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
    app = _make_test_app()
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


AUTH = {"Authorization": "Bearer tok"}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_record(**kwargs):
    """Retourne un InstanceRecord factice."""
    from src.runtime.instance_registry import InstanceRecord
    defaults = dict(
        instance_id="other-002",
        instance_name="Lumena Worker",
        role="worker",
        port=8081,
        pid=12345,
        data_dir="/tmp/data",
        workspace_dir="/tmp/w",
        started_at="2026-05-07T10:00:00+00:00",
        last_seen="2026-05-07T10:05:00+00:00",
        version="1.0.28",
        capabilities=["chat"],
        host="",
    )
    defaults.update(kwargs)
    return InstanceRecord(**defaults)


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestLocalInstancesRoute:

    def test_requires_auth(self, authed):
        r = authed.get("/api/instances/local")
        assert r.status_code in (401, 403)

    def test_returns_structure(self, authed):
        with patch("src.runtime.instance_registry.InstanceRegistry.get_live", return_value=[]):
            r = authed.get("/api/instances/local", headers=AUTH)
        assert r.status_code == 200
        d = r.json()
        assert "own_instance_id" in d
        assert "instances" in d
        assert "count" in d
        # Phase 8.9 : registre vide → entrée synthétique injectée → count >= 1
        assert d["count"] >= 1
        assert isinstance(d["instances"], list)

    def test_own_instance_id_matches_env(self, authed):
        with patch("src.runtime.instance_registry.InstanceRegistry.get_live", return_value=[]):
            r = authed.get("/api/instances/local", headers=AUTH)
        assert r.json()["own_instance_id"] == "self-001"

    def test_is_self_flag_set_correctly(self, authed):
        self_rec = _make_record(instance_id="self-001", instance_name="Lumena Principal", role="standalone", port=8080)
        other_rec = _make_record(instance_id="other-002")
        with patch("src.runtime.instance_registry.InstanceRegistry.get_live", return_value=[self_rec, other_rec]):
            r = authed.get("/api/instances/local", headers=AUTH)
        d = r.json()
        assert d["count"] == 2
        self_entry = next(i for i in d["instances"] if i["instance_id"] == "self-001")
        other_entry = next(i for i in d["instances"] if i["instance_id"] == "other-002")
        assert self_entry["is_self"] is True
        assert other_entry["is_self"] is False

    def test_instance_fields_present(self, authed):
        rec = _make_record(instance_id="self-001", port=8080, pid=9999, version="1.0.28", capabilities=["chat", "code"])
        with patch("src.runtime.instance_registry.InstanceRegistry.get_live", return_value=[rec]):
            r = authed.get("/api/instances/local", headers=AUTH)
        entry = r.json()["instances"][0]
        assert entry["instance_id"] == "self-001"
        assert entry["instance_name"] == "Lumena Worker"
        assert entry["port"] == 8080
        assert entry["pid"] == 9999
        assert entry["version"] == "1.0.28"
        assert "chat" in entry["capabilities"]
        assert "code" in entry["capabilities"]
        assert "started_at" in entry
        assert "last_seen" in entry
        assert "role" in entry

    def test_no_live_instances_injects_synthetic_self(self, authed):
        """Phase 8.9 — registre vide → entrée synthétique pour l'instance courante."""
        with patch("src.runtime.instance_registry.InstanceRegistry.get_live", return_value=[]):
            r = authed.get("/api/instances/local", headers=AUTH)
        d = r.json()
        assert d["count"] == 1
        entry = d["instances"][0]
        assert entry["is_self"] is True
        assert entry["synthetic"] is True
        assert entry["instance_id"] == "self-001"

    def test_multiple_instances_all_returned(self, authed):
        records = [_make_record(instance_id=f"inst-{i:03}", port=8080+i) for i in range(5)]
        with patch("src.runtime.instance_registry.InstanceRegistry.get_live", return_value=records):
            r = authed.get("/api/instances/local", headers=AUTH)
        d = r.json()
        # inst-000 == self-001? Non — aucun ne matche INSTANCE_ID "self-001" → +1 synthétique
        assert d["count"] == 6
        assert sum(1 for i in d["instances"] if i["synthetic"]) == 1
        assert sum(1 for i in d["instances"] if i["is_self"]) == 1

    def test_registry_error_propagates_as_500(self, tmp_path, monkeypatch):
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")
        import src.utils.paths as _paths
        monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
        monkeypatch.setattr(_paths, "INSTANCE_NAME", "Lumena Principal")
        monkeypatch.setattr(_paths, "INSTANCE_ROLE", "standalone")
        monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
        monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
        app = _make_test_app()
        with TestClient(app, raise_server_exceptions=False) as c:
            with patch("src.runtime.instance_registry.InstanceRegistry.get_live", side_effect=RuntimeError("registre corrompu")):
                r = c.get("/api/instances/local", headers=AUTH)
        assert r.status_code == 500


class TestPeerProbeRoute:

    def test_requires_auth(self, authed):
        r = authed.post("/api/peer/probe", json={"host": "192.168.1.10", "port": 8080})
        assert r.status_code in (401, 403)

    def test_found_returns_peer_data(self, authed):
        peer = {
            "instance_id": "remote-abc",
            "instance_name": "Lumena Remote",
            "host": "192.168.1.10",
            "port": 8080,
            "version": "1.0.28",
            "role": "standalone",
            "capabilities": ["chat"],
            "requires_pairing": False,
            "trust": "unknown",
        }
        with patch("src.runtime.peer_discovery.probe_single_peer", return_value=peer):
            r = authed.post("/api/peer/probe", json={"host": "192.168.1.10", "port": 8080}, headers=AUTH)
        assert r.status_code == 200
        d = r.json()
        assert d["instance_id"] == "remote-abc"
        assert d["host"] == "192.168.1.10"
        assert d["port"] == 8080

    def test_not_found_returns_404(self, authed):
        with patch("src.runtime.peer_discovery.probe_single_peer", return_value=None):
            r = authed.post("/api/peer/probe", json={"host": "192.168.1.10", "port": 8080}, headers=AUTH)
        assert r.status_code == 404
        assert "192.168.1.10" in r.json()["detail"]

    def test_invalid_port_zero_rejected(self, authed):
        with patch("src.runtime.peer_discovery.probe_single_peer", return_value=None):
            r = authed.post("/api/peer/probe", json={"host": "192.168.1.10", "port": 0}, headers=AUTH)
        assert r.status_code == 422

    def test_invalid_port_too_high_rejected(self, authed):
        with patch("src.runtime.peer_discovery.probe_single_peer", return_value=None):
            r = authed.post("/api/peer/probe", json={"host": "192.168.1.10", "port": 99999}, headers=AUTH)
        assert r.status_code == 422

    def test_empty_host_rejected(self, authed):
        with patch("src.runtime.peer_discovery.probe_single_peer", return_value=None):
            r = authed.post("/api/peer/probe", json={"host": "", "port": 8080}, headers=AUTH)
        assert r.status_code == 422

    def test_timeout_clamped(self, authed):
        """Timeout supérieur à _TIMEOUT_MAX doit être ramené à la borne haute."""
        captured = {}
        async def fake_probe(host, port, timeout):
            captured["timeout"] = timeout
            return None
        with patch("src.runtime.peer_discovery.probe_single_peer", side_effect=fake_probe):
            authed.post("/api/peer/probe", json={"host": "10.0.0.1", "port": 8080, "timeout": 999}, headers=AUTH)
        assert captured.get("timeout", 999) <= 5.0
class TestPhase89Fallback:
    """Phase 8.9 — L'instance courante est toujours visible même hors registre."""

    def test_synthetic_entry_when_registry_empty(self, authed):
        with patch("src.runtime.instance_registry.InstanceRegistry.get_live", return_value=[]):
            r = authed.get("/api/instances/local", headers=AUTH)
        d = r.json()
        assert d["count"] >= 1
        self_entry = next((i for i in d["instances"] if i["is_self"]), None)
        assert self_entry is not None, "L'instance courante doit toujours apparaître"
        assert self_entry["synthetic"] is True
        assert self_entry["instance_id"] == "self-001"
        assert self_entry["instance_name"] == "Lumena Principal"

    def test_synthetic_entry_has_required_fields(self, authed):
        with patch("src.runtime.instance_registry.InstanceRegistry.get_live", return_value=[]):
            r = authed.get("/api/instances/local", headers=AUTH)
        entry = next(i for i in r.json()["instances"] if i["is_self"])
        for field in ("instance_id", "instance_name", "role", "port", "pid", "version", "capabilities", "last_seen"):
            assert field in entry, f"Champ manquant dans l'entrée synthétique : {field}"

    def test_no_synthetic_when_self_in_registry(self, authed):
        """Si l'instance est dans le registre, pas d'entrée synthétique."""
        rec = _make_record(instance_id="self-001", port=8080)
        with patch("src.runtime.instance_registry.InstanceRegistry.get_live", return_value=[rec]):
            r = authed.get("/api/instances/local", headers=AUTH)
        entries = r.json()["instances"]
        synthetic = [e for e in entries if e.get("synthetic")]
        assert not synthetic, "Pas d'entrée synthétique si self est dans le registre"

    def test_synthetic_entry_is_first(self, authed):
        """L'entrée synthétique doit être en première position."""
        with patch("src.runtime.instance_registry.InstanceRegistry.get_live", return_value=[]):
            r = authed.get("/api/instances/local", headers=AUTH)
        assert r.json()["instances"][0]["is_self"] is True

    def test_registry_stale_all_others_still_returned(self, authed):
        """Registre avec d'autres instances + self absent → self synthétique + les autres."""
        others = [_make_record(instance_id=f"other-{i}", port=8081+i) for i in range(3)]
        with patch("src.runtime.instance_registry.InstanceRegistry.get_live", return_value=others):
            r = authed.get("/api/instances/local", headers=AUTH)
        d = r.json()
        assert d["count"] == 4  # 3 autres + 1 synthétique
        self_entries = [i for i in d["instances"] if i["is_self"]]
        assert len(self_entries) == 1
        assert self_entries[0]["synthetic"] is True


class TestPhase87DelegationContext:
    """Phase 8.7 — Contexte système injecté dans le prompt de délégation."""

    _PEER_TOKEN = "delegation-test-peer-token-xyz987654"

    def _make_delegation_app(self, tmp_path, monkeypatch):
        monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")
        import src.utils.paths as _paths
        monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
        monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
        monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
        # Pair trusted avec peer_token_hash (Phase 8.5)
        from src.runtime.peer_tokens import hash_peer_token
        reg = tmp_path / "peer_registry.json"
        import json
        reg.write_text(json.dumps({"trusted-peer-id": {
            "instance_id": "trusted-peer-id", "trust": "trusted",
            "instance_name": "Pair", "host": "10.0.0.1", "port": 8081,
            "peer_token_hash": hash_peer_token(self._PEER_TOKEN),
            "allowed_scopes": ["chat"],
        }}), encoding="utf-8")
        return _make_test_app()

    def test_delegation_prompt_prefixed(self, tmp_path, monkeypatch):
        """Le prompt transmis à lumena.chat doit contenir le préfixe de contexte."""
        app = self._make_delegation_app(tmp_path, monkeypatch)
        captured_prompt = {}

        class FakeLumena:
            is_initialized = True
            async def chat(self, prompt, **kwargs):
                captured_prompt["prompt"] = prompt
                return "OK"
            async def think_and_act_silent(self, task, **kwargs):
                captured_prompt["prompt"] = task
                return "OK"

        from web.routes import deps as _deps
        monkeypatch.setattr(_deps, "lumena", FakeLumena())

        payload = {
            "task_id": "t-001",
            "from_instance_id": "trusted-peer-id",
            "from_user_id": "u1",
            "actor_id": "actor",
            "scope": "chat",
            "prompt": "Bonjour de l'autre côté.",
        }
        with TestClient(app, raise_server_exceptions=True) as c:
            r = c.post("/api/peer/delegate", json=payload,
                       headers={"Authorization": f"Bearer {self._PEER_TOKEN}"})
        assert r.status_code == 200
        prompt = captured_prompt.get("prompt", "")
        assert "DÉLÉGATION INTER-LUMENA" in prompt
        assert "trusted-peer-id" in prompt
        # A4 : le prompt expose la CAPACITÉ réelle (ici chat → lecture seule)
        assert "chat" in prompt.lower()
        assert "lecture seule" in prompt.lower()
        assert "Bonjour de l'autre côté." in prompt

    def test_original_prompt_preserved_after_prefix(self, tmp_path, monkeypatch):
        """Le prompt original est en fin de prompt augmenté, non tronqué."""
        app = self._make_delegation_app(tmp_path, monkeypatch)
        captured = {}

        class FakeLumena:
            is_initialized = True
            async def chat(self, prompt, **kwargs):
                captured["prompt"] = prompt
                return "réponse"
            async def think_and_act_silent(self, task, **kwargs):
                captured["prompt"] = task
                return "réponse"

        from web.routes import deps as _deps
        monkeypatch.setattr(_deps, "lumena", FakeLumena())

        original = "Calcule 2 + 2."
        payload = {
            "task_id": "t-002", "from_instance_id": "trusted-peer-id",
            "from_user_id": "u1", "actor_id": "actor", "scope": "chat",
            "prompt": original,
        }
        with TestClient(app) as c:
            c.post("/api/peer/delegate", json=payload,
                   headers={"Authorization": f"Bearer {self._PEER_TOKEN}"})
        # A4 Couche 1 : le prompt du pair est ENCADRÉ comme donnée (préambule +
        # bloc délimité) → il n'est plus en fin de chaîne, mais bien préservé.
        assert original in captured["prompt"]
        assert "DEMANDE EXTERNE" in captured["prompt"]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
