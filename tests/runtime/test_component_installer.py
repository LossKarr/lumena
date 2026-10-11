from __future__ import annotations

import hashlib
from pathlib import Path
import zipfile

import httpx
import pytest

from src.runtime.component_installer import (
    ComponentInstallationError, download_component, install_component_archive,
)
from src.runtime.component_manifest import ComponentArtifact
from src.runtime.component_store import ComponentStore
from src.utils.file_lock import ProcessFileLock


URL = "https://github.com/LossKarr/lumena/releases/download/v1.2.3/runtime.zip"


def _archive(tmp_path: Path, payload: bytes = b"runtime-payload") -> tuple[Path, bytes, int]:
    archive = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("bin/runtime.txt", payload)
    return archive, archive.read_bytes(), len(payload)


def _artifact(archive: Path, unpacked_size: int, *, digest: str | None = None) -> ComponentArtifact:
    return ComponentArtifact(
        component_id="qwen-runtime", version="0.1.1-py312-cu128",
        platform="windows-x64", filename="runtime.zip",
        install_dir="runtimes/qwen3-tts/0.1.1-py312-cu128",
        size=archive.stat().st_size,
        unpacked_size=unpacked_size,
        sha256=digest or hashlib.sha256(archive.read_bytes()).hexdigest(),
        urls=(URL,), dependencies=(), required=True, archive_format="zip",
        source_revision="5d83992436eae1d760afd27aff78a71d676296fc",
        license_id="Apache-2.0",
    )


def test_download_resumes_a_valid_partial_response(tmp_path):
    archive, content, unpacked = _archive(tmp_path)
    artifact = _artifact(archive, unpacked)
    cache = tmp_path / "cache"
    cache.mkdir()
    split = len(content) // 2
    (cache / "runtime.zip.part").write_bytes(content[:split])

    def handler(request: httpx.Request):
        assert request.headers["Range"] == f"bytes={split}-"
        return httpx.Response(
            206, content=content[split:], request=request,
            headers={"Content-Range": f"bytes {split}-{len(content)-1}/{len(content)}"},
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = download_component(artifact, cache, client=client, attempts=1)
    assert result.read_bytes() == content
    assert not (cache / "runtime.zip.part").exists()


def test_download_reports_bounded_byte_progress(tmp_path):
    archive, content, unpacked = _archive(tmp_path)
    artifact = _artifact(archive, unpacked)
    progress: list[tuple[int, int]] = []
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=content, request=request)
    )) as client:
        download_component(
            artifact, tmp_path / "cache", client=client, attempts=1,
            progress=lambda written, total: progress.append((written, total)),
        )
    assert progress[0] == (0, len(content))
    assert progress[-1] == (len(content), len(content))
    assert all(0 <= written <= total for written, total in progress)


def test_download_restarts_when_server_ignores_range(tmp_path):
    archive, content, unpacked = _archive(tmp_path)
    artifact = _artifact(archive, unpacked)
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "runtime.zip.part").write_bytes(b"old")

    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=content, request=request)
    )) as client:
        result = download_component(artifact, cache, client=client, attempts=1)
    assert result.read_bytes() == content


def test_download_rejects_wrong_checksum(tmp_path):
    archive, content, unpacked = _archive(tmp_path)
    artifact = _artifact(archive, unpacked, digest="0" * 64)
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, content=content, request=request)
    )) as client:
        with pytest.raises(ComponentInstallationError, match="checksum"):
            download_component(artifact, tmp_path / "cache", client=client, attempts=1)


def test_install_is_atomic_canary_gated_and_persists_active_state(tmp_path):
    archive, _content, unpacked = _archive(tmp_path)
    artifact = _artifact(archive, unpacked)
    store = ComponentStore(tmp_path / "store")
    component = install_component_archive(
        artifact, archive, store,
        canary=lambda root: (root / "bin" / "runtime.txt").read_bytes() == b"runtime-payload",
    )
    assert component.version == artifact.version
    assert store.active("qwen-runtime") == component
    assert (store.root / component.path / "bin" / "runtime.txt").is_file()
    assert list((store.root / ".staging").iterdir()) == []


def test_failed_canary_leaves_no_active_generation(tmp_path):
    archive, _content, unpacked = _archive(tmp_path)
    artifact = _artifact(archive, unpacked)
    store = ComponentStore(tmp_path / "store")
    with pytest.raises(ComponentInstallationError, match="canary"):
        install_component_archive(artifact, archive, store, canary=lambda _root: False)
    assert store.active("qwen-runtime") is None
    assert not (store.root / Path(artifact.install_dir)).exists()


def test_concurrent_installer_is_rejected(tmp_path):
    archive, _content, unpacked = _archive(tmp_path)
    artifact = _artifact(archive, unpacked)
    store = ComponentStore(tmp_path / "store")
    owner = ProcessFileLock(store.root / ".component-install.lock", "owner")
    assert owner.acquire()
    try:
        with pytest.raises(ComponentInstallationError, match="another"):
            install_component_archive(artifact, archive, store)
    finally:
        owner.release()


def test_archive_path_traversal_is_rejected(tmp_path):
    archive = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("../escape.txt", b"no")
    artifact = _artifact(archive, 2)
    with pytest.raises(ComponentInstallationError, match="unsafe"):
        install_component_archive(artifact, archive, ComponentStore(tmp_path / "store"))
