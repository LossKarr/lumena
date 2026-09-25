"""Pure discovery and validation for Lumena IDE runtimes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
from typing import Callable, Iterable, Mapping, Optional


_MANIFEST_NAME = "lumena-extension.json"
_MAX_JSON_BYTES = 128 * 1024
_SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")


class IDEManifestError(ValueError):
    """Raised when a candidate cannot be trusted as a Lumena IDE runtime."""


@dataclass(frozen=True)
class IDEDiscoveryAttempt:
    source: str
    location: Path
    accepted: bool
    reason: str


@dataclass(frozen=True)
class IDEInstallation:
    mode: str
    source: str
    root: Path
    manifest_path: Path
    version: str
    platform: str
    executable: Optional[Path]
    command: tuple[str, ...]
    manifest_sha256: str
    artifact_sha256: str


@dataclass(frozen=True)
class IDEDiscoveryReport:
    installation: Optional[IDEInstallation]
    attempts: tuple[IDEDiscoveryAttempt, ...]

    @property
    def available(self) -> bool:
        return self.installation is not None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _current_platform_id() -> str:
    system = sys.platform
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x64"
    if system.startswith("win"):
        return f"windows-{arch}"
    if system == "darwin":
        return f"macos-{arch}"
    if system.startswith("linux"):
        return f"linux-{arch}"
    return f"{system}-{arch}"


def _safe_relative(root: Path, value: object, label: str) -> Path:
    if not isinstance(value, str) or not value.strip():
        raise IDEManifestError(f"{label} path is missing")
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        raise IDEManifestError(f"{label} path escapes the runtime root")
    target = (root / relative).resolve()
    resolved_root = root.resolve()
    try:
        target.relative_to(resolved_root)
    except ValueError as exc:
        raise IDEManifestError(f"{label} path escapes the runtime root") from exc
    return target


def _read_json(path: Path, label: str) -> dict:
    try:
        if not path.is_file():
            raise IDEManifestError(f"{label} is missing")
        if path.stat().st_size > _MAX_JSON_BYTES:
            raise IDEManifestError(f"{label} exceeds {_MAX_JSON_BYTES} bytes")
        parsed = json.loads(path.read_text(encoding="utf-8"))
    except IDEManifestError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise IDEManifestError(f"{label} is invalid: {exc}") from exc
    if not isinstance(parsed, dict):
        raise IDEManifestError(f"{label} must be a JSON object")
    return parsed


class IDEDiscoveryService:
    """Resolve one validated IDE runtime without launching or installing it."""

    def __init__(
        self,
        lumena_root: Path,
        *,
        platform_id: Optional[str] = None,
        environ: Optional[Mapping[str, str]] = None,
        which: Optional[Callable[[str], Optional[str]]] = None,
        os_install_roots: Optional[Iterable[Path]] = None,
        portable_roots: Optional[Iterable[Path]] = None,
    ) -> None:
        self.lumena_root = Path(lumena_root).resolve()
        self.platform_id = platform_id or _current_platform_id()
        self.environ = dict(os.environ if environ is None else environ)
        self.which = which or shutil.which
        self.os_install_roots = tuple(
            Path(path) for path in (os_install_roots if os_install_roots is not None else self._default_os_roots())
        )
        self.portable_roots = tuple(
            Path(path) for path in (portable_roots if portable_roots is not None else self._default_portable_roots())
        )

    def _default_os_roots(self) -> tuple[Path, ...]:
        if not self.platform_id.startswith("windows-"):
            return ()
        roots: list[Path] = []
        local = self.environ.get("LOCALAPPDATA", "").strip()
        if local:
            roots.extend([
                Path(local) / "Programs" / "Lumena IDE",
                Path(local) / "Programs" / "lumena-ide",
            ])
        for key in ("ProgramFiles", "ProgramFiles(x86)"):
            base = self.environ.get(key, "").strip()
            if base:
                roots.append(Path(base) / "Lumena IDE")
        return tuple(roots)

    def _default_portable_roots(self) -> tuple[Path, ...]:
        ide_root = self.lumena_root / "ide"
        return (
            ide_root / "release" / "win-unpacked",
            ide_root / "release-production" / "win-unpacked",
            ide_root / "win-unpacked",
        )

    def discover(
        self,
        explicit_path: Optional[Path] = None,
        *,
        expected_sha256: Optional[str] = None,
    ) -> IDEDiscoveryReport:
        attempts: list[IDEDiscoveryAttempt] = []
        configured = explicit_path or self.environ.get("LUMENA_IDE_PATH") or self.environ.get("LUMENA_CURSOR_IDE_PATH")
        if configured:
            installation = self._attempt(Path(configured), "explicit", attempts, True, expected_sha256)
            if installation:
                return IDEDiscoveryReport(installation, tuple(attempts))

        source_root = self.lumena_root / "ide"
        installation = self._attempt(source_root, "bundled-manifest", attempts, False, expected_sha256)
        if installation:
            return IDEDiscoveryReport(installation, tuple(attempts))

        for root in self.os_install_roots:
            installation = self._attempt(root, "os-install", attempts, False, expected_sha256)
            if installation:
                return IDEDiscoveryReport(installation, tuple(attempts))

        for root in self.portable_roots:
            installation = self._attempt(root, "portable", attempts, False, expected_sha256)
            if installation:
                return IDEDiscoveryReport(installation, tuple(attempts))

        installation = self._attempt(source_root, "source", attempts, True, expected_sha256, source_only=True)
        return IDEDiscoveryReport(installation, tuple(attempts))

    def _attempt(
        self,
        location: Path,
        source: str,
        attempts: list[IDEDiscoveryAttempt],
        allow_source: bool,
        expected_sha256: Optional[str],
        *,
        source_only: bool = False,
    ) -> Optional[IDEInstallation]:
        display = location.expanduser()
        try:
            installation = self._inspect(display, source, allow_source, expected_sha256, source_only=source_only)
        except IDEManifestError as exc:
            attempts.append(IDEDiscoveryAttempt(source, display.resolve(), False, str(exc)))
            return None
        attempts.append(IDEDiscoveryAttempt(source, display.resolve(), True, "validated"))
        return installation

    def _inspect(
        self,
        location: Path,
        source: str,
        allow_source: bool,
        expected_sha256: Optional[str],
        *,
        source_only: bool,
    ) -> IDEInstallation:
        root, manifest_path = self._locate(location)
        manifest = self._validate_manifest(root, manifest_path)
        packaged_path = _safe_relative(root, manifest["runtime"]["packaged"]["entrypoint"], "packaged entrypoint")

        if not source_only and packaged_path.is_file():
            external_manifest = _safe_relative(
                root,
                manifest["runtime"]["packaged"]["externalManifest"],
                "external manifest",
            )
            if manifest_path != external_manifest or not external_manifest.is_file():
                raise IDEManifestError("packaged external manifest is missing")
            if packaged_path.stat().st_size <= 0:
                raise IDEManifestError("packaged executable is empty")
            artifact_hash = _sha256(packaged_path)
            self._verify_expected_hash(artifact_hash, expected_sha256)
            return IDEInstallation(
                mode="packaged",
                source=source,
                root=root,
                manifest_path=manifest_path,
                version=manifest["version"],
                platform=self.platform_id,
                executable=packaged_path,
                command=(str(packaged_path),),
                manifest_sha256=_sha256(manifest_path),
                artifact_sha256=artifact_hash,
            )

        if not allow_source:
            raise IDEManifestError("packaged executable is missing")
        return self._validate_source(root, manifest_path, manifest, source, expected_sha256)

    def _locate(self, location: Path) -> tuple[Path, Path]:
        location = location.expanduser()
        if location.is_file():
            if location.name == _MANIFEST_NAME:
                root = location.parent.parent if location.parent.name == "resources" else location.parent
                return root.resolve(), location.resolve()
            root = location.parent.resolve()
            manifest = root / "resources" / _MANIFEST_NAME
            if not manifest.is_file():
                manifest = root / _MANIFEST_NAME
            return root, manifest.resolve()
        root = location.resolve()
        manifest = root / "resources" / _MANIFEST_NAME
        if not manifest.is_file():
            manifest = root / _MANIFEST_NAME
        return root, manifest.resolve()

    def _validate_manifest(self, root: Path, path: Path) -> dict:
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError as exc:
            raise IDEManifestError("IDE manifest path escapes the runtime root") from exc
        manifest = _read_json(path, "IDE manifest")
        if manifest.get("schemaVersion") != 2:
            raise IDEManifestError("unsupported IDE manifest schema")
        if manifest.get("id") != "lumena.ide" or manifest.get("type") != "desktop-extension":
            raise IDEManifestError("unexpected IDE manifest identity")
        version = manifest.get("version")
        if not isinstance(version, str) or not _SEMVER.fullmatch(version):
            raise IDEManifestError("invalid IDE version")
        platforms = manifest.get("platforms")
        if not isinstance(platforms, list) or self.platform_id not in platforms:
            raise IDEManifestError(f"unsupported platform: {self.platform_id}")
        runtime = manifest.get("runtime")
        if (
            not isinstance(runtime, dict)
            or not isinstance(runtime.get("packaged"), dict)
            or not isinstance(runtime.get("source"), dict)
        ):
            raise IDEManifestError("IDE runtime declaration is missing")
        _safe_relative(root, runtime["packaged"].get("entrypoint"), "packaged entrypoint")
        _safe_relative(root, runtime["packaged"].get("externalManifest"), "external manifest")
        if manifest.get("entrypoint") != runtime["packaged"].get("entrypoint"):
            raise IDEManifestError("legacy and packaged entrypoints do not match")
        source = runtime["source"]
        _safe_relative(root, source.get("package"), "source package")
        _safe_relative(root, source.get("dependencyMarker"), "source dependency")
        if source.get("packageName") != "lumena-ide" or not isinstance(source.get("startScript"), str):
            raise IDEManifestError("invalid source runtime declaration")
        required = source.get("requiredExecutables")
        if (
            not isinstance(required, list)
            or not required
            or any(not isinstance(item, str) or not item for item in required)
        ):
            raise IDEManifestError("invalid source executable requirements")
        integrity = manifest.get("integrity")
        if not isinstance(integrity, dict) or integrity.get("algorithm") != "sha256":
            raise IDEManifestError("unsupported integrity algorithm")
        _safe_relative(root, integrity.get("releaseMetadata"), "release metadata")
        return manifest

    def _validate_source(
        self,
        root: Path,
        manifest_path: Path,
        manifest: dict,
        source_label: str,
        expected_sha256: Optional[str],
    ) -> IDEInstallation:
        source = manifest["runtime"]["source"]
        package_path = _safe_relative(root, source["package"], "source package")
        package = _read_json(package_path, "source package")
        if package.get("name") != source["packageName"]:
            raise IDEManifestError("source package name does not match the manifest")
        if package.get("version") != manifest["version"]:
            raise IDEManifestError("source package version does not match the manifest")
        scripts = package.get("scripts")
        if not isinstance(scripts, dict) or source["startScript"] not in scripts:
            raise IDEManifestError("source start script is missing")
        dependency = _safe_relative(root, source["dependencyMarker"], "source dependency")
        if not dependency.is_file():
            raise IDEManifestError("source dependencies are not installed")

        resolved_tools: dict[str, str] = {}
        for executable in source["requiredExecutables"]:
            resolved = self.which(executable)
            if not resolved:
                raise IDEManifestError(f"required source executable is missing: {executable}")
            resolved_tools[executable] = resolved
        package_manager = resolved_tools.get("npm")
        if not package_manager:
            raise IDEManifestError("required source executable is missing: npm")

        artifact_hash = _sha256(manifest_path)
        self._verify_expected_hash(artifact_hash, expected_sha256)
        return IDEInstallation(
            mode="source",
            source=source_label,
            root=root,
            manifest_path=manifest_path,
            version=manifest["version"],
            platform=self.platform_id,
            executable=None,
            command=(package_manager, "run", source["startScript"], "--"),
            manifest_sha256=artifact_hash,
            artifact_sha256=artifact_hash,
        )

    @staticmethod
    def _verify_expected_hash(actual: str, expected: Optional[str]) -> None:
        if expected and actual.lower() != expected.strip().lower():
            raise IDEManifestError("IDE artifact hash does not match the expected hash")
