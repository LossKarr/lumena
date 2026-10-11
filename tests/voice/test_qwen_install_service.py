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
from src.utils.file_lock import ProcessFileLock
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


def _materialize_component_fixture(root: Path, component_id: str, version: str) -> Path:
    target = root / "components" / component_id / version
    target.mkdir(parents=True, exist_ok=True)
    if component_id == "qwen-runtime":
        python = target / "venv" / "Scripts" / "python.exe"
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_bytes(b"fixture")
    elif component_id == "qwen-model":
        (target / "config.json").write_text("{}", encoding="utf-8")
        (target / "model.safetensors").write_bytes(b"model")
        tokenizer = target / "speech_tokenizer" / "model.safetensors"
        tokenizer.parent.mkdir(parents=True, exist_ok=True)
        tokenizer.write_bytes(b"tokenizer")
    return target


def _public_recipe(tmp_path: Path) -> Path:
    lock = tmp_path / "qwen.lock"
    lock.write_text("qwen-tts==0.1.1\n", encoding="utf-8")
    recipe = tmp_path / "recipe.json"
    recipe.write_text(json.dumps({
        "schema_version": 1,
        "recipe_id": "qwen3-tts-windows-x64",
        "recipe_revision": "test.1",
        "platform": "windows-x64",
        "python": {"major": 3, "minor": 12},
        "runtime": {
            "requirements": "qwen.lock",
            "index_url": "https://pypi.org/simple",
            "extra_index_urls": ["https://download.pytorch.org/whl/cu128"],
            "required_packages": {
                "qwen-tts": "0.1.1", "torch": "2.10.0+cu128",
                "torchaudio": "2.10.0+cu128", "transformers": "4.57.3",
            },
        },
        "model": {
            "repo_id": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
            "revision": "5d83992436eae1d760afd27aff78a71d676296fc",
            "files": [
                {"path": "config.json", "size": 1},
                {"path": "model.safetensors", "size": 1},
                {"path": "speech_tokenizer/model.safetensors", "size": 1},
            ],
        },
        "estimated_runtime_download_bytes": 104857600,
        "required_disk_bytes": 4294967296,
        "license": "Apache-2.0",
        "source": "test",
    }), encoding="utf-8")
    return recipe


def test_one_click_install_fails_closed_when_all_install_sources_are_absent(tmp_path):
    service = QwenInstallService(
        root=tmp_path / "app", data_dir=tmp_path / "data",
        manifest_path=tmp_path / "missing.json", trusted_keys_path=tmp_path / "keys.json",
        recipe_path=tmp_path / "missing-recipe.json",
        release_version="1.2.3",
    )
    status = service.status()
    assert status["available"] is False
    assert status["unavailable_reason"] == "installation_recipe_invalid"
    assert status["required_disk_bytes"] == 12 * 1024 * 1024 * 1024
    assert "installation_recipe_invalid" in status["blockers"]
    with pytest.raises(ComponentServiceError, match="installation_recipe_invalid"):
        service.start()


def test_public_recipe_makes_one_click_available_without_private_release_assets(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    calls: list[str] = []

    def online_installer(**kwargs):
        calls.append(kwargs["recipe"].recipe_revision)
        kwargs["progress"]("online.model.download")
        kwargs["byte_progress"]("model:model.safetensors", 1, 1)
        store = ComponentStore(root)
        for component_id in ("qwen-runtime", "qwen-model", "lumena-voice"):
            target = _materialize_component_fixture(root, component_id, "test")
            store.activate(ActiveComponent(
                component_id, "test", target.relative_to(root).as_posix(),
                "b" * 64, "2026-10-11T12:00:00Z",
            ))
        return {"source": "public_recipe"}

    service = QwenInstallService(
        root=root, data_dir=tmp_path / "data",
        manifest_path=tmp_path / "missing.json",
        trusted_keys_path=tmp_path / "missing-keys.json",
        recipe_path=_public_recipe(tmp_path), online_installer=online_installer,
        finalizer=lambda _root: {"active": True},
        canary=lambda: {"ok": True, "qualified": True},
    )
    before = service.status()
    assert before["available"] is True
    assert before["install_source"] == "public_recipe"
    assert before["required_download_bytes"] > 100 * 1024 * 1024
    service.start()
    assert service._thread is not None
    service._thread.join(timeout=5)
    assert service.status()["state"] == "ready"
    assert calls == ["test.1"]


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
            target = _materialize_component_fixture(root, component_id, "1.0.0")
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
    assert status["message"] == "Qwen3-TTS est installé et prêt."
    assert service.start()["message"] == "Qwen3-TTS est installé et prêt."
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


def test_second_lumena_instance_does_not_mark_live_install_as_interrupted(tmp_path):
    root = tmp_path / "app"
    root.mkdir()
    state = tmp_path / "data" / "voice" / "qwen-install-state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({
        "schema_version": 1, "state": "downloading", "progress_percent": 42,
    }), encoding="utf-8")
    owner = ProcessFileLock(root / ".qwen-install.lock", "live-qwen-install")
    assert owner.acquire()
    try:
        service = QwenInstallService(
            root=root, data_dir=tmp_path / "data",
            manifest_path=tmp_path / "missing",
            trusted_keys_path=tmp_path / "missing-keys",
            recipe_path=tmp_path / "missing-recipe",
        )
        status = service.status()
        assert status["state"] == "downloading"
        assert status["progress_percent"] == 42
        assert status["error_code"] is None
    finally:
        owner.release()


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
