"""Bloc C-1.a — Kill-switch SOFT du réseau (LUMENA_PEER_HALT).

Principe (Lumena 24/7) : le halt VETO toute NOUVELLE activité (collaboration in/out,
conscience, découverte) mais NE coupe PAS la boucle d'autonomie (poll/health) — les
missions EN COURS continuent et leurs résultats reviennent (drain gracieux).

Couvre :
- halt veto collaboration / conscience / découverte, même maître ON ;
- halt NE veto PAS l'autonomie réseau (poll des missions en cours préservé) ;
- endpoint GET/POST /api/peer/halt ;
- présence dans le schéma config (effet immédiat → pas de restart).
"""
from __future__ import annotations

from typing import Generator
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import peers as peers_module
from web.routes.peers import router as peers_router

from src.runtime.peer_network_autonomy import is_peer_halt_enabled, is_peer_network_autonomy_enabled
from src.runtime.peer_awareness import _is_peer_awareness_enabled
from src.runtime.peer_discovery import is_peer_discovery_enabled
from src.reasoning.handlers.peer_tasks import _is_collaboration_enabled as _collab


AUTH = {"Authorization": "Bearer tok"}


# ── le flag halt lui-même ─────────────────────────────────────────────────────

class TestHaltFlag:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("LUMENA_PEER_HALT", raising=False)
        assert is_peer_halt_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "on", "yes"])
    def test_truthy(self, monkeypatch, val):
        monkeypatch.setenv("LUMENA_PEER_HALT", val)
        assert is_peer_halt_enabled() is True


# ── le veto : on bloque le FUTUR ──────────────────────────────────────────────

class TestHaltVetoesNewActivity:
    def test_halt_vetoes_collaboration_even_with_master_on(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_ENABLED", "1")   # maître ON
        monkeypatch.setenv("LUMENA_PEER_HALT", "1")      # mais halt
        assert _collab() is False

    def test_halt_vetoes_awareness(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_ENABLED", "1")
        monkeypatch.setenv("LUMENA_PEER_HALT", "1")
        assert _is_peer_awareness_enabled() is False

    def test_halt_vetoes_discovery(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_ENABLED", "1")
        monkeypatch.setenv("LUMENA_PEER_HALT", "1")
        assert is_peer_discovery_enabled() is False

    def test_collaboration_on_when_master_on_and_no_halt(self, monkeypatch):
        monkeypatch.setenv("LUMENA_PEER_ENABLED", "1")
        monkeypatch.delenv("LUMENA_PEER_HALT", raising=False)
        assert _collab() is True


# ── le PRÉSENT préservé : halt NE coupe PAS le poll des missions en cours ─────

class TestHaltDoesNotInterruptInFlight:
    def test_autonomy_poll_NOT_vetoed_by_halt(self, monkeypatch):
        """La boucle d'autonomie (poll/health) draine les missions en cours :
        elle DOIT rester active sous halt, sinon on couperait le présent."""
        monkeypatch.setenv("LUMENA_PEER_ENABLED", "1")
        monkeypatch.setenv("LUMENA_PEER_HALT", "1")
        # halt actif MAIS l'autonomie reste active → les résultats en vol reviennent
        assert is_peer_halt_enabled() is True
        assert is_peer_network_autonomy_enabled() is True

    def test_autonomy_via_unit_flag_also_survives_halt(self, monkeypatch):
        monkeypatch.delenv("LUMENA_PEER_ENABLED", raising=False)
        monkeypatch.setenv("LUMENA_PEER_NETWORK_AUTONOMY", "1")
        monkeypatch.setenv("LUMENA_PEER_HALT", "1")
        assert is_peer_network_autonomy_enabled() is True


# ── endpoint /api/peer/halt ───────────────────────────────────────────────────

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


class TestHaltEndpoint:
    def test_set_and_get_halt(self, authed, monkeypatch):
        # _write_env_values ne doit pas toucher le vrai .env pendant les tests.
        with patch("web.routes.config._write_env_values", lambda d: None):
            r = authed.post("/api/peer/halt", json={"halt": True}, headers=AUTH)
            assert r.status_code == 200
            assert r.json()["halt"] is True
            assert is_peer_halt_enabled() is True   # live via os.environ
            g = authed.get("/api/peer/halt", headers=AUTH)
            assert g.json()["halt"] is True
            # reprise
            r2 = authed.post("/api/peer/halt", json={"halt": False}, headers=AUTH)
            assert r2.json()["halt"] is False
            assert is_peer_halt_enabled() is False

    def test_halt_requires_admin(self, authed):
        r = authed.post("/api/peer/halt", json={"halt": True})
        assert r.status_code == 401


def test_halt_in_config_schema_no_restart():
    from web.routes.config import _CONFIG_SCHEMA
    entry = next((e for e in _CONFIG_SCHEMA if e["key"] == "LUMENA_PEER_HALT"), None)
    assert entry is not None
    assert entry["type"] == "bool"
    assert entry["default"] == "0"
    # effet immédiat (urgence) → surtout PAS de restart requis
    assert entry.get("restart", False) is False
