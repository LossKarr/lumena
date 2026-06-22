"""Cran 2.2b — Annulation d'une mission sortante (DELETE /api/peer/missions/{id}).

Couvre :
- annule : relais sortant (mocké) + mission marquée `cancelled` localement + audit ;
- best-effort : pair injoignable → mission quand même marquée annulée ;
- mission déjà terminée → no-op ; inconnue → 404 ; auth.
"""
from __future__ import annotations

import json
from typing import Generator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import peers as peers_module
from web.routes.peers import router as peers_router


AUTH = {"Authorization": "Bearer tok"}

PEER = {
    "instance_id": "peer-1", "instance_name": "Lumena Salon",
    "host": "192.168.1.50", "port": 8081, "trust": "trusted",
    "peer_token_outbound": "tok-out", "allowed_scopes": ["chat", "task.delegate"],
}


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(peers_router)
    return app


@pytest.fixture()
def authed(tmp_path, monkeypatch) -> Generator[tuple, None, None]:
    reg = tmp_path / "peer_registry.json"
    reg.write_text(json.dumps({"peer-1": PEER}), encoding="utf-8")
    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg)
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
    monkeypatch.setattr(_paths, "INSTANCE_NAME", "Lumena Principal")
    monkeypatch.setattr(_paths, "INSTANCE_ROLE", "standalone")
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    import src.runtime.peer_mission_tracker as tracker
    monkeypatch.setattr(tracker, "_TRACKER_FILE", tmp_path / "missions.json")
    with TestClient(_make_app(), raise_server_exceptions=True) as c:
        yield c, tracker


def _mission(tracker, task_id, status="running"):
    tracker.register_outbound_mission(
        task_id=task_id, peer_id="peer-1", peer_name="Lumena Salon",
        host="192.168.1.50", port=8081, objective="Coder",
    )
    if status != "queued":
        tracker.update_status(task_id, status)


class TestCancelMission:
    def test_cancel_marks_cancelled_and_relays(self, authed):
        client, tracker = authed
        _mission(tracker, "ta-1", "running")
        with patch.object(peers_module, "_outbound_cancel_mission",
                          new=AsyncMock(return_value={"ok": True, "status_code": 200})) as mock_out:
            r = client.delete("/api/peer/missions/ta-1", headers=AUTH)
        assert r.status_code == 200
        d = r.json()
        assert d["status"] == "cancelled"
        assert d["outbound"]["ok"] is True
        mock_out.assert_awaited_once()
        assert tracker.get_mission("ta-1")["status"] == "cancelled"

    def test_cancel_best_effort_when_peer_unreachable(self, authed):
        client, tracker = authed
        _mission(tracker, "ta-2", "running")
        with patch.object(peers_module, "_outbound_cancel_mission",
                          new=AsyncMock(side_effect=Exception("ReadTimeout"))):
            r = client.delete("/api/peer/missions/ta-2", headers=AUTH)
        assert r.status_code == 200
        # malgré l'échec réseau, on marque annulée localement
        assert tracker.get_mission("ta-2")["status"] == "cancelled"
        assert r.json()["outbound"]["ok"] is False

    def test_already_terminal_noop(self, authed):
        client, tracker = authed
        _mission(tracker, "ta-3", "completed")
        with patch.object(peers_module, "_outbound_cancel_mission",
                          new=AsyncMock()) as mock_out:
            r = client.delete("/api/peer/missions/ta-3", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["status"] == "completed"
        mock_out.assert_not_awaited()  # pas de relais sur une mission finie

    def test_unknown_404(self, authed):
        client, _ = authed
        assert client.delete("/api/peer/missions/nope", headers=AUTH).status_code == 404

    def test_requires_admin(self, authed):
        client, _ = authed
        assert client.delete("/api/peer/missions/ta-1").status_code == 401
