from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.tools.ide_discovery import IDEDiscoveryService


def _manifest(version: str = "1.0.0", platforms: list[str] | None = None) -> dict:
    return {
        "schemaVersion": 2,
        "id": "lumena.ide",
        "name": "Lumena IDE",
        "version": version,
        "type": "desktop-extension",
        "platforms": platforms or ["windows-x64"],
        "entrypoint": "Lumena IDE.exe",
        "runtime": {
            "packaged": {
                "entrypoint": "Lumena IDE.exe",
                "externalManifest": "resources/lumena-extension.json",
            },
            "source": {
                "package": "package.json",
                "packageName": "lumena-ide",
                "startScript": "start",
                "dependencyMarker": "node_modules/electron/package.json",
                "requiredExecutables": ["node", "npm"],
            },
        },
        "integrity": {
            "algorithm": "sha256",
            "releaseMetadata": "update-manifest.json",
        },
        "capabilities": ["ide.control.v2"],
        "lumena": {"controlProtocol": 2},
    }


def _write_manifest(path: Path, payload: dict | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload or _manifest()), encoding="utf-8")
    return path


def _packaged(root: Path, payload: dict | None = None) -> tuple[Path, Path]:
    manifest = _write_manifest(root / "resources" / "lumena-extension.json", payload)
    executable = root / "Lumena IDE.exe"
    executable.write_bytes(b"certified-ide-binary")
    return manifest, executable


def _source(root: Path, payload: dict | None = None) -> Path:
    manifest = _write_manifest(root / "lumena-extension.json", payload)
    (root / "node_modules" / "electron").mkdir(parents=True)
    (root / "node_modules" / "electron" / "package.json").write_text("{}", encoding="utf-8")
    (root / "package.json").write_text(
        json.dumps({"name": "lumena-ide", "version": "1.0.0", "scripts": {"start": "electron ."}}),
        encoding="utf-8",
    )
    return manifest


def _service(tmp_path: Path, **kwargs) -> IDEDiscoveryService:
    return IDEDiscoveryService(
        lumena_root=kwargs.pop("lumena_root", tmp_path / "lumena"),
        platform_id="windows-x64",
        environ=kwargs.pop("environ", {}),
        which=kwargs.pop("which", lambda name: f"C:/tools/{name}.cmd"),
        **kwargs,
    )


def test_explicit_packaged_runtime_wins_and_hashes_primary_artifact(tmp_path: Path) -> None:
    explicit = tmp_path / "Lumena IDE produit"
    manifest, executable = _packaged(explicit)
    fallback = tmp_path / "installed"
    _packaged(fallback)

    report = _service(tmp_path, os_install_roots=[fallback]).discover(explicit)

    assert report.installation is not None
    assert report.installation.mode == "packaged"
    assert report.installation.source == "explicit"
    assert report.installation.root == explicit.resolve()
    assert report.installation.manifest_path == manifest.resolve()
    assert report.installation.executable == executable.resolve()
    assert report.installation.artifact_sha256 == hashlib.sha256(executable.read_bytes()).hexdigest()
    assert report.attempts[0].accepted is True


def test_discovery_order_defers_source_behind_os_and_portable(tmp_path: Path) -> None:
    lumena_root = tmp_path / "lumena"
    source_root = lumena_root / "ide"
    _source(source_root)
    installed = tmp_path / "installed"
    _packaged(installed)
    portable = source_root / "release" / "win-unpacked"
    _packaged(portable)

    report = _service(tmp_path, lumena_root=lumena_root, os_install_roots=[installed]).discover()

    assert report.installation is not None
    assert report.installation.source == "os-install"
    assert report.installation.root == installed.resolve()

    report_without_os = _service(tmp_path, lumena_root=lumena_root).discover()
    assert report_without_os.installation is not None
    assert report_without_os.installation.source == "portable"
    assert report_without_os.installation.root == portable.resolve()


def test_source_mode_requires_tools_dependency_and_matching_package(tmp_path: Path) -> None:
    lumena_root = tmp_path / "lumena"
    source_root = lumena_root / "ide"
    _source(source_root)

    report = _service(tmp_path, lumena_root=lumena_root).discover()
    assert report.installation is not None
    assert report.installation.mode == "source"
    assert report.installation.source == "source"
    assert report.installation.executable is None
    assert report.installation.command == ("C:/tools/npm.cmd", "run", "start", "--")

    missing_node = _service(
        tmp_path,
        lumena_root=lumena_root,
        which=lambda name: None if name == "node" else "C:/tools/npm.cmd",
    ).discover()
    assert missing_node.installation is None
    assert any("node" in attempt.reason for attempt in missing_node.attempts)


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda data: data.update(version="v1"), "version"),
        (lambda data: data.update(platforms=["linux-x64"]), "platform"),
        (lambda data: data["runtime"]["packaged"].update(entrypoint="../escape.exe"), "path"),
        (lambda data: data.update(schemaVersion=99), "schema"),
    ],
)
def test_invalid_manifests_fail_closed(tmp_path: Path, mutate, reason: str) -> None:
    payload = _manifest()
    mutate(payload)
    explicit = tmp_path / "invalid"
    _packaged(explicit, payload)

    report = _service(tmp_path).discover(explicit)

    assert report.installation is None
    assert reason in report.attempts[0].reason.lower()


def test_expected_hash_mismatch_is_rejected(tmp_path: Path) -> None:
    explicit = tmp_path / "product"
    _packaged(explicit)

    report = _service(tmp_path).discover(explicit, expected_sha256="0" * 64)

    assert report.installation is None
    assert "hash" in report.attempts[0].reason.lower()


def test_packaged_runtime_supports_spaces_unicode_and_external_manifest(tmp_path: Path) -> None:
    root = tmp_path / "IDE Lumena - equipe francaise"
    _packaged(root)

    report = _service(tmp_path).discover(root)

    assert report.installation is not None
    assert report.installation.root == root.resolve()
    assert report.installation.command == (str((root / "Lumena IDE.exe").resolve()),)


def test_packaged_runtime_requires_external_manifest_and_nonempty_binary(tmp_path: Path) -> None:
    missing_external = tmp_path / "missing-external"
    _write_manifest(missing_external / "lumena-extension.json")
    (missing_external / "Lumena IDE.exe").write_bytes(b"binary")

    missing_report = _service(tmp_path).discover(missing_external)
    assert missing_report.installation is None
    assert "external manifest" in missing_report.attempts[0].reason.lower()

    empty_binary = tmp_path / "empty-binary"
    _write_manifest(empty_binary / "resources" / "lumena-extension.json")
    (empty_binary / "Lumena IDE.exe").write_bytes(b"")

    empty_report = _service(tmp_path).discover(empty_binary)
    assert empty_report.installation is None
    assert "empty" in empty_report.attempts[0].reason.lower()


def test_discovery_layer_never_executes_or_depends_on_react_source() -> None:
    source = Path("src/tools/ide_discovery.py").read_text(encoding="utf-8")
    assert "subprocess" not in source
    assert "reasoning.react" not in source
    assert "Popen" not in source
