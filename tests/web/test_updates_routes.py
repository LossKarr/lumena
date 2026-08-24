from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.runtime.update_service import CatalogEntry
from web.routes import config, deps, updates


class FakeUpdateService:
    def __init__(self) -> None:
        self.selected = ""
        self.checked = False
        self.root = Path.cwd()

    def status(self):
        return {"state": "idle", "current_version": "1.0.47"}

    async def list_releases(self, *, force: bool = False):
        return [CatalogEntry(
            version="1.0.48", tag="v1.0.48", name="Lumena 1.0.48",
            published_at="2026-08-23T12:00:00Z", notes_url="https://github.com/LossKarr/lumena/releases/tag/v1.0.48",
            prerelease=False, certified=True, direction="upgrade", compatible=True,
            installable=True, requires_full_installer=False, installer_available=False,
            blocked_reason=None, update_asset_url="https://github.com/LossKarr/lumena/releases/download/v1.0.48/lumena-update-windows-x64.zip",
            installer_asset_url=None, manifest={"version": "1.0.48"},
        )]

    async def check(self, *, force: bool = False):
        self.checked = force
        return {"state": "available", "available_version": "1.0.48"}

    async def prepare_version(self, version: str):
        self.selected = version
        return {"version": version, "installable": True}

    async def download_selected(self):
        return {"state": "verified", "selected_version": self.selected}

    async def launch_apply(self, **kwargs):
        return {"state": "applying", "busy_reasons": kwargs["busy_reasons"]}

    async def launch_rollback(self, **kwargs):
        return {"state": "rolling_back", "busy_reasons": kwargs["busy_reasons"]}


def _client(monkeypatch):
    fake = FakeUpdateService()
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "")
    monkeypatch.setenv("LUMENA_SETUP_COMPLETE", "0")
    monkeypatch.setattr(deps, "setup_only_mode", False)
    monkeypatch.setattr(deps, "_UPDATE_SERVICE_SINGLETON", fake)
    app = FastAPI()
    app.include_router(updates.router)
    return TestClient(app), fake


def test_update_routes_expose_only_real_catalog_and_download_operations(monkeypatch) -> None:
    client, fake = _client(monkeypatch)

    assert client.get("/api/updates/status").json()["state"] == "idle"
    releases = client.get("/api/updates/releases").json()["releases"]
    assert releases[0]["version"] == "1.0.48"
    assert client.post("/api/updates/check").json()["state"] == "available"
    assert fake.checked is True
    assert client.post("/api/updates/select", json={"version": "1.0.48"}).status_code == 200
    assert client.post("/api/updates/download").json()["state"] == "verified"
    assert client.post("/api/updates/apply").json()["state"] == "applying"
    assert client.post("/api/updates/rollback").json()["state"] == "rolling_back"


def test_update_mutations_require_admin_token(monkeypatch) -> None:
    client, _ = _client(monkeypatch)
    monkeypatch.setenv("LUMENA_ADMIN_TOKEN", "secret")

    assert client.post("/api/updates/check").status_code == 401
    assert client.post("/api/updates/select", json={"version": "1.0.48"}).status_code == 401
    assert client.post("/api/updates/download").status_code == 401
    assert client.post("/api/updates/apply").status_code == 401
    assert client.post("/api/updates/rollback").status_code == 401


def test_update_busy_reasons_include_queued_or_active_task_backlog(monkeypatch) -> None:
    class Orchestrator:
        def stats(self):
            return {"backlog_tasks": 3}

    monkeypatch.setattr(deps, "get_task_orchestrator", lambda: Orchestrator())
    monkeypatch.setattr(deps, "get_lumena", lambda: None)

    reasons = updates.collect_update_busy_reasons()

    assert "3 tache(s) en file ou active(s)" in reasons


def test_configuration_has_safe_updater_defaults() -> None:
    fields = {item["key"]: item for item in config._CONFIG_SCHEMA}

    assert fields["LUMENA_UPDATE_CHECK_ENABLED"]["default"] == "1"
    assert fields["LUMENA_UPDATE_AUTO_INSTALL"]["default"] == "0"
    assert fields["LUMENA_UPDATE_CHANNEL"]["options"] == ["stable"]
    assert fields["LUMENA_UPDATE_INTERVAL_HOURS"]["default"] == "24"


def test_server_registers_updater_routes() -> None:
    from web.server import app

    paths = {route.path for route in app.routes}
    assert "/api/updates/status" in paths
    assert "/api/updates/releases" in paths
    assert "/api/updates/download" in paths
    assert "/api/updates/apply" in paths
    assert "/api/updates/rollback" in paths
