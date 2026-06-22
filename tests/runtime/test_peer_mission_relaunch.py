"""Cran 2.2c — Relancer une mission + refus→approuver (escalade capacité).

Couvre :
- relaunch : réutilise objectif+pair, appelle submit_peer_task_handler → nouvelle mission ;
- escalate_capability=True : passe le pair en « mission » avant de relancer ;
- respect du filet : un pair en quarantaine n'est PAS relancé (refus) ;
- mission inconnue → 404 ; auth.
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
    "peer_token_outbound": "tok-out", "allowed_scopes": ["chat"],
    "capability_level": "chat",
}


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(peers_router)
    return app


@pytest.fixture()
def authed(tmp_path, monkeypatch) -> Generator[tuple, None, None]:
    reg = tmp_path / "peer_registry.json"
    reg.write_text(json.dumps({"peer-1": dict(PEER)}), encoding="utf-8")
    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", reg)
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
    monkeypatch.setattr(_paths, "INSTANCE_NAME", "Lumena Principal")
    monkeypatch.setattr(_paths, "INSTANCE_ROLE", "standalone")
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    import src.runtime.peer_mission_tracker as tracker
    monkeypatch.setattr(tracker, "_TRACKER_FILE", tmp_path / "missions.json")
    import src.runtime.peer_quarantine as quar
    monkeypatch.setattr(quar, "_FILE", tmp_path / "quarantine.json")
    quar.clear_for_tests()
    # le VRAI submit_peer_task_handler charge SON registre → même fichier
    import src.reasoning.handlers.peer_tasks as pt
    monkeypatch.setattr(pt, "_PEER_REGISTRY_FILE", reg)
    with TestClient(_make_app(), raise_server_exceptions=True) as c:
        yield c, tracker, reg, quar


def _mission(tracker, task_id, status="failed"):
    tracker.register_outbound_mission(
        task_id=task_id, peer_id="peer-1", peer_name="Lumena Salon",
        host="192.168.1.50", port=8081, objective="Coder un script Python",
    )
    tracker.update_status(task_id, status)


def _ok_result():
    from src.reasoning.handlers.contracts import HandlerResult
    return HandlerResult.ok("✅ Mission bien lancée (réf. ta-new12345).", handler_name="submit_peer_task")


class TestRelaunch:
    def test_relaunch_calls_submit_with_objective(self, authed):
        client, tracker, _, _ = authed
        _mission(tracker, "ta-old", "failed")
        with patch("src.reasoning.handlers.peer_tasks.submit_peer_task_handler",
                   new=AsyncMock(return_value=_ok_result())) as mock_submit:
            r = client.post("/api/peer/missions/relaunch", json={"task_id": "ta-old"}, headers=AUTH)
        assert r.status_code == 200 and r.json()["ok"] is True
        # appelé avec (ctx, peer_id, objective)
        args = mock_submit.await_args.args
        assert args[1] == "peer-1"
        assert "Coder un script" in args[2]

    def test_escalate_sets_mission_then_relaunches(self, authed):
        client, tracker, reg, _ = authed
        _mission(tracker, "ta-ref", "refused")
        with patch("src.reasoning.handlers.peer_tasks.submit_peer_task_handler",
                   new=AsyncMock(return_value=_ok_result())):
            r = client.post("/api/peer/missions/relaunch",
                            json={"task_id": "ta-ref", "escalate_capability": True}, headers=AUTH)
        assert r.status_code == 200 and r.json()["ok"] is True
        # le pair est passé en mission (capacité + scopes auto)
        data = json.loads(reg.read_text(encoding="utf-8"))
        assert data["peer-1"]["capability_level"] == "mission"
        assert "task.delegate" in data["peer-1"]["allowed_scopes"]

    def test_relaunch_blocked_by_quarantine(self, authed):
        """Filet : un pair en quarantaine ne doit PAS être relancé."""
        client, tracker, _, quar = authed
        _mission(tracker, "ta-q", "failed")
        for _ in range(5):
            quar.record_anomaly("peer-1")
        assert quar.is_quarantined("peer-1") is True
        monkeyenv = {"LUMENA_PEER_COLLABORATION": "1"}
        with patch.dict("os.environ", monkeyenv):
            r = client.post("/api/peer/missions/relaunch", json={"task_id": "ta-q"}, headers=AUTH)
        # submit_peer_task_handler RÉEL refuse (quarantaine) → ok=False
        assert r.status_code == 200
        assert r.json()["ok"] is False
        assert "quarantaine" in (r.json().get("error") or "").lower()

    def test_unknown_404(self, authed):
        client, _, _, _ = authed
        assert client.post("/api/peer/missions/relaunch", json={"task_id": "nope"}, headers=AUTH).status_code == 404

    def test_requires_admin(self, authed):
        client, _, _, _ = authed
        assert client.post("/api/peer/missions/relaunch", json={"task_id": "x"}).status_code == 401
