from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest

from src.runtime.update_service import UpdateService, UpdateServiceError, UpdateSettings
from src.utils.persistence import atomic_write_json


def _release_fixture(root: Path, *, bad_checksum: bool = False, with_installer: bool = False):
    payload = b"certified-update-payload"
    requirements = root / "requirements-lock.txt"
    requirements.write_text("locked\n", encoding="utf-8")
    req_hash = hashlib.sha256(requirements.read_bytes()).hexdigest()
    commit = "a" * 40
    asset_hash = "f" * 64 if bad_checksum else hashlib.sha256(payload).hexdigest()
    manifest = {
        "schema_version": 1,
        "version": "1.0.48",
        "channel": "stable",
        "published_at": "2026-08-23T12:00:00Z",
        "commit": commit,
        "asset_name": "lumena-update-windows-x64.zip",
        "asset_size": len(payload),
        "sha256": asset_hash,
        "min_supported_version": "1.0.47",
        "python": "3.12",
        "requirements_lock_sha256": req_hash,
        "data_schema_version": 1,
        "reads_data_schema_min": 1,
        "reads_data_schema_max": 1,
        "downgrade_supported": True,
        "ci": {
            "workflow": "Certified Release - Lumena",
            "run_id": 42,
            "run_url": "https://github.com/LossKarr/lumena/actions/runs/42",
            "conclusion": "success",
            "commit": commit,
        },
        "guard_smoke_profile": "update-v1",
        "managed_files_sha256": "d" * 64,
        "restart_required": True,
        "release_notes_url": "https://github.com/LossKarr/lumena/releases/tag/v1.0.48",
    }
    certification = {
        "schema_version": 1,
        "version": "1.0.48",
        "commit": commit,
        "ci_run_id": 42,
        "ci_run_url": manifest["ci"]["run_url"],
        "ci_conclusion": "success",
        "full_regression_required": True,
        "guard_smoke_profile": "update-v1",
        "asset_sha256": asset_hash,
        "managed_files_sha256": "d" * 64,
    }
    base = "https://github.com/LossKarr/lumena/releases/download/v1.0.48/"
    assets = [
        {"name": "update-manifest.json", "browser_download_url": base + "update-manifest.json"},
        {"name": "release-certification.json", "browser_download_url": base + "release-certification.json"},
        {
            "name": "lumena-update-windows-x64.zip",
            "browser_download_url": base + "lumena-update-windows-x64.zip",
            "size": len(payload),
        },
    ]
    if with_installer:
        assets.append({
            "name": "lumena-setup-v1.0.48.exe",
            "browser_download_url": base + "lumena-setup-v1.0.48.exe",
        })
    release = {
        "tag_name": "v1.0.48",
        "name": "Lumena 1.0.48",
        "published_at": manifest["published_at"],
        "html_url": manifest["release_notes_url"],
        "draft": False,
        "prerelease": False,
        "assets": assets,
    }
    historical = {
        "tag_name": "v1.0.46",
        "name": "Lumena 1.0.46",
        "published_at": "2026-08-01T12:00:00Z",
        "html_url": "https://github.com/LossKarr/lumena/releases/tag/v1.0.46",
        "draft": False,
        "prerelease": False,
        "assets": [],
    }
    return payload, manifest, certification, [release, historical]


def _client(payload: bytes, manifest: dict, certification: dict, releases: list[dict], calls: list[str]):
    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        path = request.url.path
        if path.endswith("/releases"):
            return httpx.Response(200, json=releases, request=request)
        if path.endswith("/update-manifest.json"):
            return httpx.Response(200, json=manifest, request=request)
        if path.endswith("/release-certification.json"):
            return httpx.Response(200, json=certification, request=request)
        if path.endswith("/lumena-update-windows-x64.zip"):
            return httpx.Response(200, content=payload, request=request)
        raise AssertionError(f"unexpected updater request: {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


def _redirecting_client(
    payload: bytes, manifest: dict, certification: dict,
    releases: list[dict], calls: list[str],
):
    """Reproduce GitHub's browser_download_url -> release CDN redirects."""
    cdn_host = "release-assets.githubusercontent.com"

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        path = request.url.path
        if path.endswith("/releases"):
            return httpx.Response(200, json=releases, request=request)
        if request.url.host == "github.com":
            asset_name = path.rsplit("/", 1)[-1]
            return httpx.Response(
                302,
                headers={"Location": f"https://{cdn_host}/github-production-release-asset/{asset_name}"},
                request=request,
            )
        if request.url.host == cdn_host and path.endswith("/update-manifest.json"):
            return httpx.Response(200, json=manifest, request=request)
        if request.url.host == cdn_host and path.endswith("/release-certification.json"):
            return httpx.Response(200, json=certification, request=request)
        if request.url.host == cdn_host and path.endswith("/lumena-update-windows-x64.zip"):
            return httpx.Response(200, content=payload, request=request)
        raise AssertionError(f"unexpected updater request: {request.url}")

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)


@pytest.mark.asyncio
async def test_catalog_keeps_history_but_only_certified_release_is_installable(tmp_path: Path) -> None:
    payload, manifest, certification, releases = _release_fixture(tmp_path)
    calls: list[str] = []
    client = _client(payload, manifest, certification, releases, calls)
    service = UpdateService(root=tmp_path, updates_dir=tmp_path / "updates", client=client)

    entries = await service.list_releases(force=True)

    assert [entry.version for entry in entries] == ["1.0.48", "1.0.46"]
    assert entries[0].certified and entries[0].installable
    assert not entries[1].certified and not entries[1].installable
    assert "historique" in (entries[1].blocked_reason or "")
    await client.aclose()


@pytest.mark.asyncio
async def test_catalog_and_download_accept_github_release_asset_cdn_redirects(tmp_path: Path) -> None:
    payload, manifest, certification, releases = _release_fixture(tmp_path)
    calls: list[str] = []
    client = _redirecting_client(payload, manifest, certification, releases, calls)
    service = UpdateService(root=tmp_path, updates_dir=tmp_path / "updates", client=client)

    entries = await service.list_releases(force=True)
    assert entries[0].certified and entries[0].installable

    await service.prepare_version("1.0.48")
    result = await service.download_selected()

    assert result["state"] == "verified"
    assert any("release-assets.githubusercontent.com" in call for call in calls)
    await client.aclose()


@pytest.mark.asyncio
async def test_catalog_requires_private_installer_asset_when_dependencies_change(tmp_path: Path) -> None:
    payload, manifest, certification, releases = _release_fixture(tmp_path)
    manifest["requirements_lock_sha256"] = "e" * 64
    calls: list[str] = []
    client = _client(payload, manifest, certification, releases, calls)
    service = UpdateService(root=tmp_path, updates_dir=tmp_path / "updates", client=client)

    entry = (await service.list_releases(force=True))[0]

    assert entry.certified and entry.requires_full_installer
    assert not entry.installable and not entry.installer_available
    assert "installateur complet" in (entry.blocked_reason or "")
    await client.aclose()


@pytest.mark.asyncio
async def test_private_installer_is_exposed_but_never_treated_as_lightweight_update(tmp_path: Path) -> None:
    payload, manifest, certification, releases = _release_fixture(tmp_path, with_installer=True)
    manifest["requirements_lock_sha256"] = "e" * 64
    calls: list[str] = []
    client = _client(payload, manifest, certification, releases, calls)
    service = UpdateService(root=tmp_path, updates_dir=tmp_path / "updates", client=client)

    entry = (await service.list_releases(force=True))[0]

    assert entry.certified and entry.requires_full_installer and entry.installer_available
    assert not entry.installable
    assert entry.installer_asset_url and entry.installer_asset_url.endswith(".exe")
    assert "manuelle" in (entry.blocked_reason or "")
    with pytest.raises(UpdateServiceError, match="paquet leger"):
        await service.prepare_version("1.0.48")
    await client.aclose()


@pytest.mark.asyncio
async def test_prepare_and_download_are_bounded_and_checksum_verified(tmp_path: Path) -> None:
    payload, manifest, certification, releases = _release_fixture(tmp_path)
    calls: list[str] = []
    client = _client(payload, manifest, certification, releases, calls)
    service = UpdateService(root=tmp_path, updates_dir=tmp_path / "updates", client=client)

    await service.list_releases(force=True)
    await service.prepare_version("1.0.48")
    result = await asyncio.wait_for(service.download_selected(), timeout=2)

    archive = Path(result["staged_archive"])
    assert result["state"] == "verified"
    assert archive.read_bytes() == payload
    assert not archive.with_suffix(archive.suffix + ".part").exists()
    await client.aclose()


@pytest.mark.asyncio
async def test_bad_checksum_fails_closed_and_removes_partial(tmp_path: Path) -> None:
    payload, manifest, certification, releases = _release_fixture(tmp_path, bad_checksum=True)
    calls: list[str] = []
    client = _client(payload, manifest, certification, releases, calls)
    service = UpdateService(root=tmp_path, updates_dir=tmp_path / "updates", client=client)

    await service.list_releases(force=True)
    await service.prepare_version("1.0.48")
    with pytest.raises(UpdateServiceError, match="checksum"):
        await service.download_selected()

    assert service.status()["state"] == "failed"
    assert not list((tmp_path / "updates").rglob("*.part"))
    await client.aclose()


@pytest.mark.asyncio
async def test_fresh_catalog_cache_avoids_second_github_request(tmp_path: Path) -> None:
    payload, manifest, certification, releases = _release_fixture(tmp_path)
    calls: list[str] = []
    client = _client(payload, manifest, certification, releases, calls)
    service = UpdateService(root=tmp_path, updates_dir=tmp_path / "updates", client=client)

    await service.list_releases(force=True)
    first_count = len(calls)
    await service.list_releases()

    assert len(calls) == first_count
    await client.aclose()


@pytest.mark.asyncio
async def test_stale_catalog_uses_etag_and_304_cache(tmp_path: Path) -> None:
    payload, manifest, certification, releases = _release_fixture(tmp_path)
    calls: list[str] = []
    initial = _client(payload, manifest, certification, releases, calls)
    updates = tmp_path / "updates"
    service = UpdateService(root=tmp_path, updates_dir=updates, client=initial)
    await service.list_releases(force=True)
    cache = json.loads(service.cache_path.read_text(encoding="utf-8"))
    cache["etag"] = '"catalog-v1"'
    cache["cached_at"] = "2000-01-01T00:00:00Z"
    atomic_write_json(service.cache_path, cache)
    await initial.aclose()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("if-none-match") == '"catalog-v1"'
        return httpx.Response(304, request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = UpdateService(root=tmp_path, updates_dir=updates, client=client)
    entries = await service.list_releases()

    assert entries[0].version == "1.0.48" and entries[0].certified
    await client.aclose()


@pytest.mark.asyncio
async def test_interrupted_download_resumes_with_http_range(tmp_path: Path) -> None:
    payload, manifest, certification, releases = _release_fixture(tmp_path)
    calls: list[str] = []
    base_client = _client(payload, manifest, certification, releases, calls)
    updates = tmp_path / "updates"
    service = UpdateService(root=tmp_path, updates_dir=updates, client=base_client)
    await service.list_releases(force=True)
    await service.prepare_version("1.0.48")
    await base_client.aclose()
    partial = updates / "staging" / "1.0.48" / "lumena-update-windows-x64.zip.part"
    partial.parent.mkdir(parents=True, exist_ok=True)
    split = 7
    partial.write_bytes(payload[:split])

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("range") == f"bytes={split}-"
        return httpx.Response(
            206, content=payload[split:],
            headers={"Content-Range": f"bytes {split}-{len(payload)-1}/{len(payload)}"},
            request=request,
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    service = UpdateService(root=tmp_path, updates_dir=updates, client=client)
    result = await service.download_selected()

    assert result["state"] == "verified"
    assert Path(result["staged_archive"]).read_bytes() == payload
    await client.aclose()


def test_interrupted_transaction_is_reported_as_failed(tmp_path: Path) -> None:
    updates = tmp_path / "updates"
    updates.mkdir()
    (updates / "state.json").write_text(
        json.dumps({"schema_version": 1, "state": "applying"}), encoding="utf-8"
    )

    service = UpdateService(root=tmp_path, updates_dir=updates)

    assert service.status()["state"] == "failed"
    assert "interrompue" in service.status()["error"]


def test_live_detached_helper_keeps_transaction_state(tmp_path: Path, monkeypatch) -> None:
    from src.runtime import update_service as module

    updates = tmp_path / "updates"
    updates.mkdir()
    (updates / "state.json").write_text(
        json.dumps({"schema_version": 1, "state": "restarting", "helper_pid": 4242}),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_process_alive", lambda pid: pid == 4242)

    service = UpdateService(root=tmp_path, updates_dir=updates)

    assert service.status()["state"] == "restarting"


def test_latest_rollback_uses_most_recent_snapshot_not_version_name(tmp_path: Path) -> None:
    updates = tmp_path / "updates"
    older = updates / "rollback" / "9.0.0-1"
    newer = updates / "rollback" / "1.0.0-2"
    older.mkdir(parents=True)
    newer.mkdir()
    (older / "transaction.json").write_text("{}", encoding="utf-8")
    (newer / "transaction.json").write_text("{}", encoding="utf-8")
    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    service = UpdateService(root=tmp_path, updates_dir=updates)

    assert service._latest_rollback() == newer


def test_incomplete_rollback_directory_is_never_advertised(tmp_path: Path) -> None:
    updates = tmp_path / "updates"
    (updates / "rollback" / "incomplete").mkdir(parents=True)

    service = UpdateService(root=tmp_path, updates_dir=updates)

    assert service._latest_rollback() is None
    assert service.status()["rollback_available"] is False


@pytest.mark.asyncio
async def test_apply_waits_when_any_work_is_active(tmp_path: Path) -> None:
    service = UpdateService(root=tmp_path, updates_dir=tmp_path / "updates")
    service._transition("verified", staged_version="1.0.48")

    with pytest.raises(UpdateServiceError, match="travaille encore"):
        await service.launch_apply(
            busy_reasons=["1 mission active"], parent_pid=123,
            restart_command=["python", "run_desktop.py"],
            health_url="http://127.0.0.1:8080/api/health",
        )

    assert service.status()["state"] == "waiting_idle"


@pytest.mark.asyncio
async def test_apply_launches_detached_helper_only_after_verified_staging(tmp_path: Path, monkeypatch) -> None:
    from src.runtime import update_service as module

    payload, manifest, _, _ = _release_fixture(tmp_path)
    updates = tmp_path / "updates"
    staged = updates / "staging" / "1.0.48"
    staged.mkdir(parents=True)
    archive = staged / "lumena-update-windows-x64.zip"
    archive.write_bytes(payload)
    atomic_write_json(staged / "update-manifest.json", manifest)
    helper = tmp_path / "scripts" / "lumena_updater.py"
    helper.parent.mkdir()
    helper.write_text("# helper", encoding="utf-8")
    transaction = updates / "transactions" / "1.0.48" / "transaction.json"
    transaction.parent.mkdir(parents=True)
    transaction.write_text("{}", encoding="utf-8")
    service = UpdateService(root=tmp_path, updates_dir=updates)
    service._transition("verified", staged_archive=str(archive), staged_version="1.0.48")

    monkeypatch.setattr(module, "prepare_transaction", lambda **kwargs: transaction)
    captured: dict[str, object] = {}

    class Process:
        pid = 987

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return Process()

    monkeypatch.setattr(module.subprocess, "Popen", fake_popen)
    result = await service.launch_apply(
        busy_reasons=[], parent_pid=123,
        restart_command=["python", "run_desktop.py"],
        health_url="http://127.0.0.1:8080/api/health",
    )

    assert result["state"] == "applying" and result["helper_pid"] == 987
    assert "lumena_updater.py" in " ".join(captured["command"])
    assert "--transaction" in captured["command"]
