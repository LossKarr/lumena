from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pytest
from fastapi import HTTPException, UploadFile

from web.routes import advanced
from src.runtime.component_service import ComponentServiceError


class _PackService:
    def __init__(self):
        self.restored = None
        self.activated = []
        self.deleted = []

    def status(self):
        return {
            "schema_version": 1,
            "trusted_signers": 1,
            "can_import": True,
            "active": {"pack_id": "old", "version": "1.0.0"},
            "installed": [],
        }

    def install(self, path, *, activate=False):
        assert Path(path).read_bytes() == b"signed-pack"
        assert activate is False
        return {"pack_id": "new", "version": "2.0.0", "languages": ["en"], "active": False}

    def activate(self, pack_id, version):
        self.activated.append((pack_id, version))
        return {"pack_id": pack_id, "version": version, "languages": ["en"], "active": True}

    def rollback(self):
        return {"pack_id": "old", "version": "1.0.0", "active": True}

    def uninstall(self, pack_id, version):
        self.deleted.append((pack_id, version))

    def restore_selection(self, previous):
        self.restored = previous


@pytest.mark.asyncio
async def test_import_route_streams_to_private_temp_and_cleans_it(tmp_path, monkeypatch):
    service = _PackService()
    monkeypatch.setattr(advanced, "DATA_DIR", tmp_path)
    monkeypatch.setattr(advanced, "_voice_pack_service", lambda: service)
    monkeypatch.setattr(advanced.deps, "VoiceManager", None)
    upload = UploadFile(filename="voice.zip", file=BytesIO(b"signed-pack"))

    result = await advanced.import_voice_pack(upload, activate=True)

    assert result["pack_id"] == "new"
    assert result["active"] is True
    assert result["restarted"] is False
    assert service.activated == [("new", "2.0.0")]
    assert not (tmp_path / "voice" / ".uploads").exists()


@pytest.mark.asyncio
async def test_import_route_rejects_oversized_archive_before_service(tmp_path, monkeypatch):
    monkeypatch.setattr(advanced, "DATA_DIR", tmp_path)
    monkeypatch.setattr(advanced, "_voice_pack_service", lambda: _PackService())
    monkeypatch.setenv("LUMENA_VOICE_PACK_MAX_MB", "1")
    upload = UploadFile(filename="voice.zip", file=BytesIO(b"x" * (1024 * 1024 + 1)))

    with pytest.raises(HTTPException) as error:
        await advanced.import_voice_pack(upload, activate=False)
    assert error.value.status_code == 422
    assert "volumineux" in error.value.detail
    assert not (tmp_path / "voice" / ".uploads").exists()


@pytest.mark.asyncio
async def test_failed_v2_restart_restores_previous_pack(monkeypatch):
    service = _PackService()

    class _Manager:
        running = True
        active_backend = "v2"

        async def stop(self):
            self.running = False
            self.active_backend = "none"

        async def start(self, _core, *, backend):
            assert backend == "v2"
            self.active_backend = "legacy"
            return True

    manager = _Manager()

    class _VoiceManager:
        @staticmethod
        def get_instance():
            return manager

    monkeypatch.setattr(advanced.deps, "VoiceManager", _VoiceManager)
    monkeypatch.setattr(advanced.deps, "lumena", object())
    previous = {"pack_id": "old", "version": "1.0.0"}

    with pytest.raises(HTTPException) as error:
        await advanced._restart_voice_for_pack(previous, service)
    assert error.value.status_code == 503
    assert service.restored == previous


@pytest.mark.asyncio
async def test_delete_route_returns_refreshed_bounded_status(monkeypatch):
    service = _PackService()
    monkeypatch.setattr(advanced, "_voice_pack_service", lambda: service)
    result = await advanced.uninstall_voice_pack("new", "2.0.0")
    assert result["deleted"] is True
    assert service.deleted == [("new", "2.0.0")]
    assert result["status"]["schema_version"] == 1


@pytest.mark.asyncio
async def test_qwen_install_routes_use_one_bounded_authenticated_service(monkeypatch):
    class _Service:
        def status(self):
            return {"schema_version": 1, "state": "idle", "available": True}

        def start(self):
            return {"schema_version": 1, "state": "preparing", "progress_percent": 1}

    monkeypatch.setattr(advanced, "_qwen_install_service", lambda: _Service())
    assert (await advanced.qwen_install_status())["available"] is True
    assert (await advanced.start_qwen_install())["state"] == "preparing"


@pytest.mark.asyncio
async def test_qwen_install_route_reports_unpublished_release_without_paths(monkeypatch):
    class _Service:
        def start(self):
            raise ComponentServiceError("release_assets_missing")

    monkeypatch.setattr(advanced, "_qwen_install_service", lambda: _Service())
    with pytest.raises(HTTPException) as error:
        await advanced.start_qwen_install()
    assert error.value.status_code == 503
    assert "pas encore publié" in error.value.detail
    assert "\\" not in error.value.detail
