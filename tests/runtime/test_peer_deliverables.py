"""Cran 2.2a — Endpoint de listing des livrables d'une mission (lecture seule).

Couvre :
- liste les fichiers de `artifacts_dir` (nom + taille) ;
- path-safety : refuse un dossier HORS workspace (403) ;
- mission inconnue → 404 ; mission sans artefacts → liste vide ; auth.
"""
from __future__ import annotations

from typing import Generator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import peers as peers_module
from web.routes.peers import router as peers_router


AUTH = {"Authorization": "Bearer tok"}


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(peers_router)
    return app


@pytest.fixture()
def authed(tmp_path, monkeypatch) -> Generator[tuple, None, None]:
    monkeypatch.setattr(peers_module, "_PEER_REGISTRY_FILE", tmp_path / "peer_registry.json")
    import src.utils.paths as _paths
    monkeypatch.setattr(_paths, "INSTANCE_ID", "self-001")
    monkeypatch.setattr(_paths, "INSTANCE_NAME", "Lumena Principal")
    monkeypatch.setattr(_paths, "INSTANCE_ROLE", "standalone")
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "tok")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "1")
    # workspace isolé
    ws = tmp_path / "workspace"
    ws.mkdir()
    monkeypatch.setattr(_paths, "WORKSPACE_DIR", ws)
    # tracker isolé
    import src.runtime.peer_mission_tracker as tracker
    monkeypatch.setattr(tracker, "_TRACKER_FILE", tmp_path / "missions.json")
    with TestClient(_make_app(), raise_server_exceptions=True) as c:
        yield c, ws, tracker


def _mission(tracker, task_id, artifacts_dir=None):
    tracker.register_outbound_mission(
        task_id=task_id, peer_id="peer-1", peer_name="Lumena Salon",
        host="192.168.1.50", port=8081, objective="Coder",
    )
    if artifacts_dir is not None:
        tracker.set_artifacts(task_id, str(artifacts_dir), 0)


class TestDeliverables:
    def test_lists_files(self, authed):
        client, ws, tracker = authed
        recu = ws / "recu-de-lumena-salon"
        recu.mkdir()
        (recu / "rapport.md").write_text("x" * 100, encoding="utf-8")
        (recu / "script.py").write_text("print(1)", encoding="utf-8")
        _mission(tracker, "ta-1", artifacts_dir=recu)

        r = client.get("/api/peer/deliverables?task_id=ta-1", headers=AUTH)
        assert r.status_code == 200
        d = r.json()
        names = {f["name"] for f in d["files"]}
        assert names == {"rapport.md", "script.py"}
        assert any(f["size"] == 100 for f in d["files"])

    def test_path_outside_workspace_refused(self, authed, tmp_path):
        client, ws, tracker = authed
        outside = tmp_path / "secret"   # HORS workspace
        outside.mkdir()
        (outside / "creds.txt").write_text("nope", encoding="utf-8")
        _mission(tracker, "ta-evil", artifacts_dir=outside)

        r = client.get("/api/peer/deliverables?task_id=ta-evil", headers=AUTH)
        assert r.status_code == 403

    def test_no_artifacts_dir_empty(self, authed):
        client, ws, tracker = authed
        _mission(tracker, "ta-2", artifacts_dir=None)
        r = client.get("/api/peer/deliverables?task_id=ta-2", headers=AUTH)
        assert r.status_code == 200
        assert r.json()["files"] == []

    def test_unknown_mission_404(self, authed):
        client, _, _ = authed
        assert client.get("/api/peer/deliverables?task_id=nope", headers=AUTH).status_code == 404

    def test_requires_admin(self, authed):
        client, _, _ = authed
        assert client.get("/api/peer/deliverables?task_id=ta-1").status_code == 401
