"""Certified GitHub Releases catalogue, state machine and download service."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import httpx

from src.runtime.update_manifest import (
    CERTIFICATION_ASSET_NAME,
    EXPECTED_REPOSITORY,
    MANIFEST_ASSET_NAME,
    ReleaseManifest,
    UpdateManifestError,
    Version,
    sha256_path,
    validate_certification,
    validate_download_response_url,
    validate_download_url,
)
from src.runtime.update_installation import (
    UpdateInstallationError, git_checkout_ready, prepare_transaction,
)
from src.utils.file_lock import ProcessFileLock
from src.utils.persistence import atomic_write_json, safe_read_json
from src.utils.paths import ROOT_DIR, UPDATES_DIR
from src.version import __version__, get_build_identity

_ALLOWED_STATES = frozenset({
    "idle", "checking", "up_to_date", "available", "downloading", "verified",
    "waiting_idle", "applying", "restarting", "healthy", "rolling_back", "failed",
})


class UpdateServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class UpdateSettings:
    check_enabled: bool = True
    auto_install: bool = False
    channel: str = "stable"
    interval_hours: int = 24
    repository: str = EXPECTED_REPOSITORY

    @classmethod
    def from_env(cls) -> "UpdateSettings":
        channel = os.getenv("LUMENA_UPDATE_CHANNEL", "stable").strip().lower()
        if channel != "stable":
            channel = "stable"
        try:
            interval = max(1, min(168, int(os.getenv("LUMENA_UPDATE_INTERVAL_HOURS", "24"))))
        except (TypeError, ValueError):
            interval = 24
        return cls(
            check_enabled=_env_flag("LUMENA_UPDATE_CHECK_ENABLED", True),
            auto_install=_env_flag("LUMENA_UPDATE_AUTO_INSTALL", False),
            channel=channel,
            interval_hours=interval,
        )


@dataclass(frozen=True)
class CatalogEntry:
    version: str
    tag: str
    name: str
    published_at: str
    notes_url: str
    prerelease: bool
    certified: bool
    direction: str
    compatible: bool
    installable: bool
    requires_full_installer: bool
    installer_available: bool
    blocked_reason: str | None
    update_asset_url: str | None
    installer_asset_url: str | None
    manifest: dict[str, Any] | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class UpdateService:
    def __init__(
        self, *, root: Path = ROOT_DIR, updates_dir: Path = UPDATES_DIR,
        settings: UpdateSettings | None = None, client: httpx.AsyncClient | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.updates_dir = Path(updates_dir).resolve()
        self.settings = settings or UpdateSettings.from_env()
        self.state_path = self.updates_dir / "state.json"
        self.cache_path = self.updates_dir / "cache" / "releases.json"
        self.staging_dir = self.updates_dir / "staging"
        self.rollback_dir = self.updates_dir / "rollback"
        self.log_path = self.updates_dir / "updates.jsonl"
        self.lock = ProcessFileLock(self.updates_dir / "update.lock", "lumena-update")
        self._async_lock = asyncio.Lock()
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(15.0, connect=5.0),
            headers={"Accept": "application/vnd.github+json", "User-Agent": "Lumena-Updater"},
        )
        self.updates_dir.mkdir(parents=True, exist_ok=True)
        self._recover_interrupted_state()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def status(self) -> dict[str, Any]:
        state = self._read_state()
        state.setdefault("state", "idle")
        state["current_version"] = __version__
        state["settings"] = asdict(self.settings)
        state["installation_type"] = self.installation_type()
        state["rollback_available"] = self._latest_rollback() is not None
        return state

    def installation_type(self) -> str:
        if (self.root / ".git").exists():
            return "git"
        if (self.root / "build-info.json").is_file():
            return "installed"
        return "portable"

    async def check(self, *, force: bool = False) -> dict[str, Any]:
        entries = await self.list_releases(force=force)
        newest = next(
            (entry for entry in entries if entry.direction == "upgrade" and entry.certified),
            None,
        )
        state_name = "available" if newest else "up_to_date"
        self._transition(
            state_name,
            last_check=_utc_now(),
            available_version=newest.version if newest else None,
            selected_version=newest.version if newest else self._read_state().get("selected_version"),
            error=None,
        )
        return self.status()

    async def list_releases(self, *, force: bool = False) -> list[CatalogEntry]:
        if not force:
            cached = self._read_cache()
            if cached is not None:
                return [CatalogEntry(**entry) for entry in cached]
        async with self._async_lock:
            if not force:
                cached = self._read_cache()
                if cached is not None:
                    return [CatalogEntry(**entry) for entry in cached]
            self._transition("checking", error=None)
            try:
                releases, etag, not_modified = await self._fetch_release_pages()
                if not_modified:
                    cached = self._read_cache(allow_stale=True)
                    if cached is None:
                        raise UpdateServiceError("GitHub a repondu 304 sans catalogue local")
                    raw_cache = safe_read_json(self.cache_path, default={})
                    atomic_write_json(
                        self.cache_path,
                        {
                            "cached_at": _utc_now(), "etag": etag,
                            "entries": raw_cache.get("entries", []),
                        },
                    )
                    return [CatalogEntry(**entry) for entry in cached]
                entries = [await self._catalog_entry(item) for item in releases]
                entries.sort(key=lambda entry: Version.parse(entry.version), reverse=True)
                atomic_write_json(
                    self.cache_path,
                    {
                        "cached_at": _utc_now(), "etag": etag,
                        "entries": [entry.as_dict() for entry in entries],
                    },
                )
                return entries
            except Exception as exc:
                self._transition("failed", error=f"verification GitHub impossible: {exc}")
                raise UpdateServiceError(str(exc)) from exc

    async def prepare_version(self, version: str) -> dict[str, Any]:
        requested = str(Version.parse(version))
        entries = await self.list_releases()
        entry = next((item for item in entries if item.version == requested), None)
        if entry is None:
            raise UpdateServiceError("version absente du catalogue certifie")
        if not entry.compatible:
            raise UpdateServiceError(entry.blocked_reason or "version incompatible")
        if entry.requires_full_installer:
            if entry.installer_available:
                raise UpdateServiceError(
                    "cette version exige l'installateur complet joint a la release; "
                    "le paquet leger ne peut pas modifier les dependances"
                )
            raise UpdateServiceError("cette version exige l'installateur complet, pas encore joint a la release")
        if not entry.installable:
            raise UpdateServiceError(entry.blocked_reason or "version non installable automatiquement")
        self._transition(
            "available", selected_version=entry.version,
            available_version=entry.version if entry.direction == "upgrade" else None,
            selected_direction=entry.direction, error=None,
        )
        return entry.as_dict()

    async def download_selected(self) -> dict[str, Any]:
        # Resolve the catalogue before taking the operation lock. list_releases()
        # owns the same asyncio lock while refreshing an expired cache.
        entries = await self.list_releases()
        async with self._async_lock:
            state = self._read_state()
            version = str(state.get("selected_version") or "")
            if not version:
                raise UpdateServiceError("aucune version selectionnee")
            entry = next((item for item in entries if item.version == version), None)
            if entry is None or not entry.installable or not entry.manifest or not entry.update_asset_url:
                raise UpdateServiceError("release selectionnee non telechargeable")
            manifest = ReleaseManifest.from_mapping(entry.manifest)
            if manifest.requirements_lock_sha256 != self._requirements_hash():
                if entry.installer_available:
                    raise UpdateServiceError("installateur complet requis pour modifier les dependances")
                raise UpdateServiceError("dependances modifiees et installateur complet absent")
            validate_download_url(entry.update_asset_url)
            usage = shutil.disk_usage(self.updates_dir)
            if usage.free < max(manifest.asset_size * 2, 64 * 1024 * 1024):
                raise UpdateServiceError("espace disque insuffisant pour telecharger et sauvegarder la mise a jour")
            if not self.lock.acquire():
                raise UpdateServiceError("une autre operation de mise a jour est deja active")
            try:
                try:
                    return await self._download(entry, manifest)
                except Exception as exc:
                    self._transition("failed", error=f"telechargement invalide: {exc}")
                    if isinstance(exc, UpdateServiceError):
                        raise
                    raise UpdateServiceError(str(exc)) from exc
            finally:
                self.lock.release()

    async def launch_apply(
        self, *, busy_reasons: list[str], parent_pid: int,
        restart_command: list[str], health_url: str,
    ) -> dict[str, Any]:
        if busy_reasons:
            self._transition("waiting_idle", busy_reasons=busy_reasons)
            raise UpdateServiceError("Lumena travaille encore: " + ", ".join(busy_reasons))
        async with self._async_lock:
            state = self._read_state()
            if state.get("state") != "verified":
                raise UpdateServiceError("le paquet doit etre telecharge et verifie avant installation")
            archive = Path(str(state.get("staged_archive") or ""))
            manifest_path = archive.parent / MANIFEST_ASSET_NAME
            if not archive.is_file() or not manifest_path.is_file():
                raise UpdateServiceError("staging certifie absent")
            manifest = ReleaseManifest.from_json(manifest_path.read_bytes())
            mode = self.installation_type()
            transaction_path: Path | None = None
            if mode == "git":
                ready, reason = git_checkout_ready(self.root)
                if not ready:
                    raise UpdateServiceError(reason)
            else:
                try:
                    transaction_path = prepare_transaction(
                        root=self.root, archive=archive,
                        release_manifest_path=manifest_path,
                        transaction_dir=self.rollback_dir / f"{manifest.version}-{int(time.time())}",
                    )
                except (OSError, UpdateInstallationError, UpdateManifestError) as exc:
                    raise UpdateServiceError(str(exc)) from exc
            if not self.lock.acquire():
                raise UpdateServiceError("une autre operation de mise a jour est active")
            try:
                command = self._helper_command(
                    mode=mode, manifest=manifest, parent_pid=parent_pid,
                    restart_command=restart_command, health_url=health_url,
                    transaction_path=transaction_path,
                )
                flags = 0
                if os.name == "nt":
                    flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                process = subprocess.Popen(
                    command, cwd=self.root, creationflags=flags, close_fds=True,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                self._transition(
                    "applying", helper_pid=process.pid, transaction_path=str(transaction_path or ""),
                    target_version=str(manifest.version), target_commit=manifest.commit,
                    busy_reasons=[], error=None,
                )
                return self.status()
            finally:
                self.lock.release()

    async def launch_rollback(
        self, *, busy_reasons: list[str], parent_pid: int,
        restart_command: list[str], health_url: str,
    ) -> dict[str, Any]:
        if busy_reasons:
            self._transition("waiting_idle", busy_reasons=busy_reasons)
            raise UpdateServiceError("Lumena travaille encore: " + ", ".join(busy_reasons))
        if self.installation_type() == "git":
            raise UpdateServiceError("rollback local indisponible pour un checkout Git")
        rollback_dir = self._latest_rollback()
        plan = rollback_dir / "transaction.json" if rollback_dir else None
        if plan is None or not plan.is_file():
            raise UpdateServiceError("aucun snapshot de rollback disponible")
        value = safe_read_json(plan, default={})
        snapshot = Path(str(value.get("snapshot_dir") or ""))
        previous = safe_read_json(snapshot / "build-info.json", default={})
        version = str(previous.get("version") or "")
        commit = str(previous.get("commit") or "")
        if not version or not commit:
            raise UpdateServiceError("identite de l'ancienne version absente du snapshot")
        manifest_stub = type("RollbackTarget", (), {"version": version, "commit": commit})()
        command = self._helper_command(
            mode="portable", manifest=manifest_stub, parent_pid=parent_pid,
            restart_command=restart_command, health_url=health_url,
            transaction_path=plan, rollback_only=True,
        )
        flags = 0
        if os.name == "nt":
            flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        process = subprocess.Popen(
            command, cwd=self.root, creationflags=flags, close_fds=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self._transition(
            "rolling_back", helper_pid=process.pid, transaction_path=str(plan),
            target_version=version, target_commit=commit, busy_reasons=[], error=None,
        )
        return self.status()

    def _helper_command(
        self, *, mode: str, manifest: ReleaseManifest, parent_pid: int,
        restart_command: list[str], health_url: str,
        transaction_path: Path | None, rollback_only: bool = False,
    ) -> list[str]:
        helper = self.root / "scripts" / "lumena_updater.py"
        if not helper.is_file():
            raise UpdateServiceError("helper de mise a jour absent")
        command = [
            sys.executable, str(helper), "--root", str(self.root), "--state", str(self.state_path),
            "--parent-pid", str(parent_pid), "--mode", mode,
            "--version", str(manifest.version), "--commit", manifest.commit,
            "--python", sys.executable, "--restart-json", json.dumps(restart_command),
            "--health-url", health_url,
        ]
        if transaction_path is not None:
            command.extend(["--transaction", str(transaction_path)])
        if rollback_only:
            command.append("--rollback-only")
        return command

    async def _download(self, entry: CatalogEntry, manifest: ReleaseManifest) -> dict[str, Any]:
        target_dir = self.staging_dir / str(manifest.version)
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / manifest.asset_name
        partial = target.with_suffix(target.suffix + ".part")
        existing = partial.stat().st_size if partial.exists() else 0
        headers = {"Range": f"bytes={existing}-"} if existing else {}
        self._transition("downloading", progress_bytes=existing, total_bytes=manifest.asset_size, error=None)
        async with self._client.stream("GET", entry.update_asset_url, headers=headers) as response:
            if existing and response.status_code != 206:
                partial.unlink(missing_ok=True)
                existing = 0
            response.raise_for_status()
            validate_download_response_url(str(response.url))
            mode = "ab" if existing and response.status_code == 206 else "wb"
            with partial.open(mode) as handle:
                async for chunk in response.aiter_bytes(1024 * 256):
                    handle.write(chunk)
                    existing += len(chunk)
                    self._transition("downloading", progress_bytes=existing, total_bytes=manifest.asset_size)
        if partial.stat().st_size != manifest.asset_size:
            raise UpdateServiceError("taille du paquet telecharge differente du manifeste")
        if sha256_path(partial) != manifest.sha256:
            partial.unlink(missing_ok=True)
            raise UpdateServiceError("checksum du paquet invalide")
        partial.replace(target)
        atomic_write_json(target_dir / MANIFEST_ASSET_NAME, entry.manifest)
        self._transition(
            "verified", staged_version=str(manifest.version), staged_archive=str(target),
            progress_bytes=manifest.asset_size, total_bytes=manifest.asset_size, error=None,
        )
        return self.status()

    async def _fetch_release_pages(self) -> tuple[list[Mapping[str, Any]], str | None, bool]:
        releases: list[Mapping[str, Any]] = []
        cached = safe_read_json(self.cache_path, default={})
        cached_etag = str(cached.get("etag") or "") if isinstance(cached, dict) else ""
        response_etag: str | None = None
        for page in range(1, 6):
            url = f"https://api.github.com/repos/{self.settings.repository}/releases"
            headers = {"If-None-Match": cached_etag} if page == 1 and cached_etag else None
            response = await self._client.get(
                url, params={"per_page": 100, "page": page}, headers=headers,
            )
            if page == 1 and response.status_code == 304:
                return [], cached_etag, True
            response.raise_for_status()
            if page == 1:
                response_etag = response.headers.get("etag")
            payload = response.json()
            if not isinstance(payload, list):
                raise UpdateServiceError("reponse catalogue GitHub invalide")
            batch = [item for item in payload if isinstance(item, Mapping)]
            releases.extend(batch)
            if len(batch) < 100:
                break
        return releases, response_etag, False

    async def _catalog_entry(self, release: Mapping[str, Any]) -> CatalogEntry:
        tag = str(release.get("tag_name") or "")
        version_text = tag[1:] if tag.startswith("v") else tag
        try:
            version = Version.parse(version_text)
        except UpdateManifestError:
            return _uncertified_entry(release, version_text or "invalide", "tag de version invalide")
        if release.get("draft") is True:
            return _uncertified_entry(release, str(version), "release brouillon")
        if release.get("prerelease") is True and self.settings.channel == "stable":
            return _uncertified_entry(release, str(version), "prerelease exclue du canal stable")

        assets = {
            str(asset.get("name")): asset
            for asset in release.get("assets", [])
            if isinstance(asset, Mapping) and asset.get("name")
        }
        manifest_asset = assets.get(MANIFEST_ASSET_NAME)
        cert_asset = assets.get(CERTIFICATION_ASSET_NAME)
        update_asset = assets.get("lumena-update-windows-x64.zip")
        installer_asset = next(
            (asset for name, asset in assets.items() if name.lower().startswith("lumena-setup") and name.lower().endswith(".exe")),
            None,
        )
        if not manifest_asset or not cert_asset or not update_asset:
            return _uncertified_entry(
                release, str(version), "release historique sans manifeste ou paquet certifie",
                installer_asset_url=_asset_url(installer_asset),
            )
        try:
            manifest_response, cert_response = await asyncio.gather(
                self._client.get(_asset_url_required(manifest_asset)),
                self._client.get(_asset_url_required(cert_asset)),
            )
            manifest_response.raise_for_status()
            cert_response.raise_for_status()
            validate_download_response_url(str(manifest_response.url))
            validate_download_response_url(str(cert_response.url))
            manifest = ReleaseManifest.from_json(manifest_response.content)
            certification = cert_response.json()
            validate_certification(certification, manifest)
            if manifest.version != version:
                raise UpdateManifestError("tag and manifest version differ")
            update_url = _asset_url_required(update_asset)
            validate_download_url(update_url)
            if int(update_asset.get("size") or 0) != manifest.asset_size:
                raise UpdateManifestError("GitHub asset size differs from manifest")
            compatibility = manifest.classify(
                current_version=Version.parse(__version__),
                local_data_schema=get_build_identity(self.root).data_schema_version,
                local_requirements_sha256=self._requirements_hash(),
            )
            installer_url = _asset_url(installer_asset)
            if installer_url:
                validate_download_url(installer_url)
            reason = compatibility.blocked_reason
            installable = compatibility.compatible and compatibility.direction != "current"
            if compatibility.requires_full_installer:
                installable = False
                reason = (
                    "installateur complet disponible; installation manuelle requise"
                    if installer_url
                    else "installateur complet requis mais absent de la release"
                )
            if self.installation_type() == "git" and compatibility.direction == "downgrade":
                installable = False
                reason = "retrogradation indisponible pour un depot de developpement"
            return CatalogEntry(
                version=str(version), tag=tag, name=str(release.get("name") or tag),
                published_at=str(release.get("published_at") or ""),
                notes_url=manifest.release_notes_url, prerelease=bool(release.get("prerelease")),
                certified=True, direction=compatibility.direction,
                compatible=compatibility.compatible, installable=installable,
                requires_full_installer=compatibility.requires_full_installer,
                installer_available=bool(installer_url), blocked_reason=reason,
                update_asset_url=update_url, installer_asset_url=installer_url,
                manifest=json.loads(manifest_response.content),
            )
        except (httpx.HTTPError, ValueError, UpdateManifestError, json.JSONDecodeError) as exc:
            return _uncertified_entry(release, str(version), f"certification invalide: {exc}")

    def _requirements_hash(self) -> str:
        path = self.root / "requirements-lock.txt"
        return sha256_path(path) if path.is_file() else ""

    def _transition(self, state: str, **updates: Any) -> None:
        if state not in _ALLOWED_STATES:
            raise UpdateServiceError(f"invalid updater state: {state}")
        value = self._read_state()
        value.update(updates)
        value.update({"schema_version": 1, "state": state, "updated_at": _utc_now()})
        atomic_write_json(self.state_path, value)
        self._append_log(state, updates)

    def _append_log(self, state: str, details: Mapping[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        safe_details = {key: value for key, value in details.items() if "token" not in key.lower()}
        event = {"at": _utc_now(), "state": state, "details": safe_details}
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=True, separators=(",", ":")) + "\n")

    def _read_state(self) -> dict[str, Any]:
        value = safe_read_json(self.state_path, default={})
        return value if isinstance(value, dict) else {}

    def _read_cache(self, *, allow_stale: bool = False) -> list[dict[str, Any]] | None:
        value = safe_read_json(self.cache_path, default={})
        cached_at = value.get("cached_at")
        entries = value.get("entries")
        if not cached_at or not isinstance(entries, list):
            return None
        try:
            age = datetime.now(timezone.utc) - datetime.fromisoformat(str(cached_at).replace("Z", "+00:00"))
        except ValueError:
            return None
        if not allow_stale and age.total_seconds() > self.settings.interval_hours * 3600:
            return None
        return [entry for entry in entries if isinstance(entry, dict)]

    def _recover_interrupted_state(self) -> None:
        state = self._read_state()
        transactional = state.get("state") in {"applying", "restarting", "rolling_back"}
        helper_pid = int(state.get("helper_pid") or 0)
        if transactional and helper_pid > 0 and _process_alive(helper_pid):
            return
        if state.get("state") in {"downloading", "applying", "restarting", "rolling_back"}:
            self._transition("failed", error="transaction interrompue; reprise ou rollback requis")

    def _latest_rollback(self) -> Path | None:
        if not self.rollback_dir.is_dir():
            return None
        candidates = [
            item for item in self.rollback_dir.iterdir()
            if item.is_dir() and (item / "transaction.json").is_file()
        ]
        return max(candidates, key=lambda item: item.stat().st_mtime_ns) if candidates else None


def _asset_url(asset: Mapping[str, Any] | None) -> str | None:
    if not asset:
        return None
    value = str(asset.get("browser_download_url") or "")
    return value or None


def _asset_url_required(asset: Mapping[str, Any]) -> str:
    value = _asset_url(asset)
    if not value:
        raise UpdateManifestError("release asset has no download URL")
    validate_download_url(value)
    return value


def _uncertified_entry(
    release: Mapping[str, Any], version: str, reason: str,
    installer_asset_url: str | None = None,
) -> CatalogEntry:
    return CatalogEntry(
        version=version, tag=str(release.get("tag_name") or ""),
        name=str(release.get("name") or release.get("tag_name") or version),
        published_at=str(release.get("published_at") or ""),
        notes_url=str(release.get("html_url") or ""), prerelease=bool(release.get("prerelease")),
        certified=False, direction="unknown", compatible=False, installable=False,
        requires_full_installer=False, installer_available=bool(installer_asset_url),
        blocked_reason=reason, update_asset_url=None,
        installer_asset_url=installer_asset_url, manifest=None,
    )


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=3, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return False
        return result.returncode == 0 and f'"{pid}"' in (result.stdout or "")
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError, PermissionError):
        return False
    return True


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
