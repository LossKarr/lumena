from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.runtime.component_manifest import canonical_component_manifest_bytes
from src.runtime.component_service import ComponentServiceError
from src.runtime.component_store import ActiveComponent, ComponentStore
from src.voice.v2.qwen_install_service import QwenInstallService


@pytest.fixture(autouse=True)
def _isolate_component_root(monkeypatch):
    """Keep install tests independent from the operator's real .env."""
    monkeypatch.delenv("LUMENA_COMPONENT_ROOT", raising=False)


def _signed_release(tmp_path: Path, release: str = "1.2.3") -> tuple[Path, Path]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    specs = (
        ("qwen-runtime-shard-01", []),
        ("qwen-model", ["qwen-runtime-shard-01"]),
        ("qwen-tokenizer", ["qwen-model"]),
        ("lumena-voice", ["qwen-tokenizer"]),
    )
    components = []
    for index, (component_id, dependencies) in enumerate(specs, 1):
        filename = f"{component_id}.zip"
        components.append({
            "id": component_id, "version": "1.0.0", "platform": "windows-x64",
            "filename": filename, "install_dir": f"components/{component_id}/1.0.0",
            "size": 100 + index, "unpacked_size": 200 + index,
            "sha256": str(index) * 64,
            "urls": [f"https://github.com/LossKarr/lumena/releases/download/v{release}/{filename}"],
            "dependencies": dependencies, "required": True, "archive_format": "zip",
            "source_revision": "5d83992436eae1d760afd27aff78a71d676296fc",
            "license": "Apache-2.0",
        })
    manifest = {
        "schema_version": 1, "release_version": release,
        "platform": "windows-x64", "generated_at": "2026-10-11T12:00:00Z",
        "components": components,
    }
    manifest["signature"] = {
        "algorithm": "ed25519", "key_id": "release",
        "value": base64.b64encode(
            private.sign(canonical_component_manifest_bytes(manifest))
        ).decode(),
    }
    manifest_path, keys_path = tmp_path / "manifest.json", tmp_path / "keys.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    keys_path.write_text(json.dumps({
        "keys": {"release": base64.b64encode(public).decode()},
    }), encoding="utf-8")
    return manifest_path, keys_path


def test_one_click_install_fails_closed_when_release_assets_are_absent(tmp_path):
    service = QwenInstallService(
        root=tmp_path / "app", data_dir=tmp_path / "data",
        manifest_path=tmp_path / "missing.json", trusted_keys_path=tmp_path / "keys.json",
        release_version="1.2.3",
    )
    status = service.status()
    assert status["available"] is False
    assert status["unavailable_reason"] == "release_not_published"
    assert status["required_disk_bytes"] == 12 * 1024 * 1024 * 1024
    assert "release_not_published" in status["blockers"]
    with pytest.raises(ComponentServiceError, match="release_assets_missing"):
        service.start()


def test_component_storage_can_move_to_another_local_volume(tmp_path, monkeypatch):
    app = tmp_path / "app"
    storage = tmp_path / "large-components"
    monkeypatch.setenv("LUMENA_COMPONENT_ROOT", str(storage))
    service = QwenInstallService(
        root=app, data_dir=tmp_path / "data",
        manifest_path=tmp_path / "missing.json",
        trusted_keys_path=tmp_path / "keys.json", release_version="1.2.3",
    )
    status = service.status()
    assert service.root == storage.resolve()
    assert status["free_disk_bytes"] > 0


def test_one_click_install_runs_once_tracks_progress_and_finishes_ready(tmp_path):
    manifest, keys = _signed_release(tmp_path)
    root = tmp_path / "app"
    root.mkdir()
    calls: list[str] = []

    def installer(**kwargs):
        calls.append("install")
        kwargs["progress"]("component.download:qwen-runtime-shard-01")
        kwargs["byte_progress"]("qwen-runtime-shard-01", 50, 100)
        kwargs["progress"]("component.verify:qwen-runtime-shard-01")
        store = ComponentStore(root)
        for component_id in ("qwen-runtime", "qwen-model", "lumena-voice"):
            target = root / "components" / component_id / "1.0.0"
            target.mkdir(parents=True, exist_ok=True)
            store.activate(ActiveComponent(
                component_id, "1.0.0", target.relative_to(root).as_posix(),
                "a" * 64, "2026-10-11T12:00:00Z",
            ))
        return {"components": []}

    service = QwenInstallService(
        root=root, data_dir=tmp_path / "data", manifest_path=manifest,
        trusted_keys_path=keys, release_version="1.2.3", installer=installer,
        finalizer=lambda _root: calls.append("voice-pack") or {"active": True},
        canary=lambda: calls.append("canary") or {"ok": True, "qualified": True},
    )
    started = service.start()
    assert started["state"] in {"preparing", "downloading", "verifying", "ready"}
    assert service._thread is not None
    service._thread.join(timeout=5)
    assert not service._thread.is_alive()
    status = service.status()
    assert status["state"] == "ready"
    assert status["installed"] is True
    assert status["progress_percent"] == 100
    assert calls == ["install", "voice-pack", "canary"]


def test_interrupted_install_is_recoverable_after_restart(tmp_path):
    state = tmp_path / "data" / "voice" / "qwen-install-state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"schema_version": 1, "state": "downloading"}), encoding="utf-8")
    service = QwenInstallService(
        root=tmp_path / "app", data_dir=tmp_path / "data",
        manifest_path=tmp_path / "missing", trusted_keys_path=tmp_path / "missing-keys",
        release_version="1.2.3",
    )
    status = service.status()
    assert status["state"] == "failed"
    assert status["error_code"] == "interrupted"
    assert status["retryable"] is True


def test_progress_never_moves_backwards_between_components(tmp_path):
    service = QwenInstallService(
        root=tmp_path / "app", data_dir=tmp_path / "data",
        manifest_path=tmp_path / "missing", trusted_keys_path=tmp_path / "missing-keys",
        release_version="1.2.3",
    )
    service._download_totals = {"first": 100, "second": 100}
    service._write_state(state="downloading", progress_percent=4)
    service._on_bytes("first", 100, 100)
    first = service.status()["progress_percent"]
    service._on_event("component.download:second")
    assert service.status()["progress_percent"] == first
