"""Resumable download and transactional installation of signed components."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tempfile
import time
from typing import Callable
import zipfile

import httpx

from src.runtime.component_manifest import ComponentArtifact
from src.runtime.component_store import ActiveComponent, ComponentStore
from src.runtime.update_manifest import validate_download_response_url
from src.utils.file_lock import ProcessFileLock


class ComponentInstallationError(RuntimeError):
    pass


_COPY_BYTES = 1024 * 1024
_CONTENT_RANGE = re.compile(r"^bytes (\d+)-(\d+)/(\d+)$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(_COPY_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download_component(
    artifact: ComponentArtifact, cache_dir: Path, *, client: httpx.Client | None = None,
    attempts: int = 3, progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Download one immutable archive, resuming only a validated HTTP 206 response."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    final = cache_dir / artifact.filename
    partial = cache_dir / f"{artifact.filename}.part"
    if final.is_file() and final.stat().st_size == artifact.size \
            and sha256_file(final) == artifact.sha256:
        if progress is not None:
            progress(artifact.size, artifact.size)
        return final
    final.unlink(missing_ok=True)
    owns_client = client is None
    transport = client or httpx.Client(
        follow_redirects=True, timeout=httpx.Timeout(120.0, connect=15.0),
        headers={"User-Agent": "Lumena-Component-Installer"},
    )
    try:
        errors: list[str] = []
        for attempt in range(max(1, attempts)):
            if partial.exists() and partial.stat().st_size > artifact.size:
                partial.unlink()
            for url in artifact.urls:
                try:
                    _download_once(artifact, url, partial, transport, progress=progress)
                    if partial.stat().st_size != artifact.size:
                        raise ComponentInstallationError("component download is truncated")
                    if sha256_file(partial) != artifact.sha256:
                        partial.unlink(missing_ok=True)
                        raise ComponentInstallationError("component checksum mismatch")
                    os.replace(partial, final)
                    return final
                except (httpx.HTTPError, OSError, ComponentInstallationError) as exc:
                    errors.append(str(exc))
            if attempt + 1 < max(1, attempts):
                time.sleep(min(0.25 * (2**attempt), 1.0))
        raise ComponentInstallationError(errors[-1] if errors else "component download failed")
    finally:
        if owns_client:
            transport.close()


def _download_once(
    artifact: ComponentArtifact, url: str, partial: Path, client: httpx.Client,
    *, progress: Callable[[int, int], None] | None = None,
) -> None:
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"Range": f"bytes={offset}-"} if offset else {}
    with client.stream("GET", url, headers=headers) as response:
        response.raise_for_status()
        validate_download_response_url(str(response.url))
        append = False
        if offset and response.status_code == 206:
            match = _CONTENT_RANGE.fullmatch(response.headers.get("Content-Range", ""))
            if not match or int(match.group(1)) != offset or int(match.group(3)) != artifact.size:
                raise ComponentInstallationError("invalid component range response")
            append = True
        elif response.status_code == 206:
            match = _CONTENT_RANGE.fullmatch(response.headers.get("Content-Range", ""))
            if not match or int(match.group(1)) != 0 or int(match.group(3)) != artifact.size:
                raise ComponentInstallationError("invalid component range response")
        elif response.status_code != 200:
            raise ComponentInstallationError("unexpected component download status")
        mode = "ab" if append else "wb"
        written = offset if append else 0
        if progress is not None:
            progress(written, artifact.size)
        with partial.open(mode) as output:
            for chunk in response.iter_bytes(_COPY_BYTES):
                written += len(chunk)
                if written > artifact.size:
                    raise ComponentInstallationError("component download exceeds declared size")
                output.write(chunk)
                if progress is not None:
                    progress(written, artifact.size)
            output.flush()
            os.fsync(output.fileno())


def install_component_archive(
    artifact: ComponentArtifact, archive: Path, store: ComponentStore, *,
    canary: Callable[[Path], bool] | None = None,
) -> ActiveComponent:
    """Verify, extract, test and atomically activate a component generation."""
    archive = Path(archive)
    if not archive.is_file() or archive.stat().st_size != artifact.size \
            or sha256_file(archive) != artifact.sha256:
        raise ComponentInstallationError("component archive does not match manifest")
    lock = ProcessFileLock(store.root / ".component-install.lock", "lumena-components")
    if not lock.acquire():
        raise ComponentInstallationError("another component installation is running")
    staging_root = store.root / ".staging"
    staging_root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f"{artifact.component_id}-", dir=staging_root))
    target = (store.root / Path(artifact.install_dir)).resolve()
    try:
        try:
            target.relative_to(store.root)
        except ValueError as exc:
            raise ComponentInstallationError("component target escapes store") from exc
        required = artifact.unpacked_size + artifact.size + 64 * 1024 * 1024
        if shutil.disk_usage(store.root).free < required:
            raise ComponentInstallationError("insufficient disk space for component staging")
        _extract_verified_zip(archive, staging, artifact)
        if canary is not None and not bool(canary(staging)):
            raise ComponentInstallationError("component canary failed")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            active = store.active(artifact.component_id)
            if active and active.version == artifact.version \
                    and active.archive_sha256 == artifact.sha256:
                shutil.rmtree(staging, ignore_errors=True)
                return active
            raise ComponentInstallationError("component target already exists but is not active")
        os.replace(staging, target)
        relative_target = target.relative_to(store.root).as_posix()
        component = ActiveComponent(
            component_id=artifact.component_id, version=artifact.version,
            path=relative_target, archive_sha256=artifact.sha256,
            activated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        try:
            store.activate(component)
        except Exception:
            shutil.rmtree(target, ignore_errors=True)
            raise
        return component
    except (zipfile.BadZipFile, OSError) as exc:
        raise ComponentInstallationError(f"component extraction failed: {exc}") from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        lock.release()


def _extract_verified_zip(
    archive: Path, staging: Path, artifact: ComponentArtifact,
) -> None:
    total = 0
    seen: set[str] = set()
    with zipfile.ZipFile(archive) as package:
        files = [info for info in package.infolist() if not info.is_dir()]
        if not files or len(files) > 100_000:
            raise ComponentInstallationError("component archive file count is invalid")
        for info in files:
            raw = info.filename
            if not raw or "\\" in raw or ":" in raw:
                raise ComponentInstallationError("unsafe component archive path")
            rel = PurePosixPath(raw)
            if rel.is_absolute() or any(part in {"", ".", ".."} for part in rel.parts):
                raise ComponentInstallationError("unsafe component archive path")
            normalized = rel.as_posix()
            if normalized in seen:
                raise ComponentInstallationError("duplicate component archive path")
            seen.add(normalized)
            if ((info.external_attr >> 16) & 0o170000) == 0o120000:
                raise ComponentInstallationError("component symlinks are forbidden")
            total += int(info.file_size)
            if total > artifact.unpacked_size:
                raise ComponentInstallationError("component expands beyond declared size")
            if info.file_size and info.compress_size <= 0:
                raise ComponentInstallationError("invalid component compression")
            if info.file_size and info.file_size / info.compress_size > 300:
                raise ComponentInstallationError("component compression ratio is unsafe")
            destination = staging.joinpath(*rel.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            with package.open(info) as source, destination.open("wb") as output:
                shutil.copyfileobj(source, output, length=_COPY_BYTES)
        if total != artifact.unpacked_size:
            raise ComponentInstallationError("component unpacked size does not match manifest")
