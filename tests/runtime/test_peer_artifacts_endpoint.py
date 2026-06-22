"""Brique 4 — tests d'intégration : store de bundle (B) + endpoints artefacts."""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from web.routes import peers as peers_module
from web.routes.peers import router as peers_router, verify_peer_token

PEER = "peer-A"


@pytest.fixture(autouse=True)
def _clean_bundles():
    with peers_module._artifact_lock:
        peers_module._artifact_bundles.clear()
    yield
    with peers_module._artifact_lock:
        peers_module._artifact_bundles.clear()


# ── Store côté B ─────────────────────────────────────────────────────────────

def test_store_mission_bundle(tmp_path, monkeypatch):
    import src.utils.paths as paths
    monkeypatch.setattr(paths, "WORKSPACE_DIR", tmp_path)
    monkeypatch.setattr(paths, "DATA_DIR", tmp_path / "data")
    f1 = tmp_path / "site" / "index.html"
    f1.parent.mkdir(parents=True)
    f1.write_text("<html>", encoding="utf-8")
    f2 = tmp_path / "site" / "style.css"
    f2.write_text("body{}", encoding="utf-8")

    n = peers_module._store_mission_bundle("ta-1", PEER, [str(f1), str(f2)])
    assert n == 2
    entry = peers_module._get_artifact_bundle("ta-1")
    assert entry["from_instance_id"] == PEER
    assert entry["bundle"]["kind"] == "zip"
    assert len(entry["manifest"]) == 2
    assert all("_abs" not in m for m in entry["manifest"])  # sanitizé


def test_store_no_files_returns_zero(tmp_path, monkeypatch):
    import src.utils.paths as paths
    monkeypatch.setattr(paths, "WORKSPACE_DIR", tmp_path)
    assert peers_module._store_mission_bundle("t", PEER, []) == 0
    assert peers_module._get_artifact_bundle("t") is None


# ── Endpoints (propriété vérifiée) ───────────────────────────────────────────

@pytest.fixture()
def client_as(tmp_path):
    """App avec auth pair simulée → retourne un client + fonction set_peer."""
    app = FastAPI()
    app.include_router(peers_router)
    state = {"peer": PEER}
    app.dependency_overrides[verify_peer_token] = lambda: {"instance_id": state["peer"]}
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c, state


def _inject_zip_bundle(task_id, owner, tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("site/index.html", "<html>")
    data = buf.getvalue()
    zpath = tmp_path / f"{task_id}.zip"
    zpath.write_bytes(data)
    import hashlib, time
    with peers_module._artifact_lock:
        peers_module._artifact_bundles[task_id] = {
            "manifest": [{"filename": "index.html", "rel_path": "site/index.html",
                          "size": 6, "sha256": "x", "mime": "text/html", "artifact_id": "a1"}],
            "bundle": {"kind": "zip", "path": str(zpath), "filename": f"{task_id}.zip",
                       "sha256": hashlib.sha256(data).hexdigest(), "count": 1},
            "from_instance_id": owner,
            "created_mono": time.monotonic(),
        }
    return data


def test_manifest_owner_ok_and_stranger_403(client_as, tmp_path):
    client, state = client_as
    _inject_zip_bundle("ta-1", PEER, tmp_path)
    # propriétaire
    r = client.get("/api/peer/artifact/ta-1/manifest")
    assert r.status_code == 200
    assert r.json()["available"] is True and r.json()["count"] == 1
    # étranger
    state["peer"] = "intrus"
    r2 = client.get("/api/peer/artifact/ta-1/manifest")
    assert r2.status_code == 403


def test_manifest_unknown_task(client_as):
    client, _ = client_as
    r = client.get("/api/peer/artifact/nope/manifest")
    assert r.status_code == 200
    assert r.json()["available"] is False


def test_file_download(client_as, tmp_path):
    client, _ = client_as
    data = _inject_zip_bundle("ta-2", PEER, tmp_path)
    r = client.get("/api/peer/artifact/ta-2/file")
    assert r.status_code == 200
    assert r.content == data


def test_file_unknown_404(client_as):
    client, _ = client_as
    r = client.get("/api/peer/artifact/nope/file")
    assert r.status_code == 404
