from __future__ import annotations

import base64
import importlib.util
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.runtime.component_manifest import canonical_component_manifest_bytes
from src.runtime.component_service import (
    ComponentServiceError, _component_canary, assemble_qwen_model,
    assemble_qwen_runtime, install_release_components, load_component_trusted_keys,
)
from src.runtime.component_store import ActiveComponent, ComponentStore


REVISION = "5d83992436eae1d760afd27aff78a71d676296fc"

_VERIFY_PATH = Path(__file__).resolve().parents[2] / "tools" / "release" / "verify_component_manifest.py"
_VERIFY_SPEC = importlib.util.spec_from_file_location("lumena_component_release_verify", _VERIFY_PATH)
assert _VERIFY_SPEC and _VERIFY_SPEC.loader
_VERIFY_MODULE = importlib.util.module_from_spec(_VERIFY_SPEC)
_VERIFY_SPEC.loader.exec_module(_VERIFY_MODULE)


def _signed_files(tmp_path: Path) -> tuple[Path, Path]:
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    components = []
    for index, (component_id, dependency) in enumerate((
        ("qwen-runtime-shard-01", []),
        ("qwen-model", ["qwen-runtime-shard-01"]),
        ("qwen-tokenizer", ["qwen-model"]),
        ("lumena-voice", ["qwen-tokenizer"]),
    ), start=1):
        filename = f"{component_id}.zip"
        components.append({
            "id": component_id, "version": "1.0.0", "platform": "windows-x64",
            "filename": filename, "install_dir": f"components/{component_id}/1.0.0",
            "size": 100 + index, "unpacked_size": 200 + index,
            "sha256": str(index) * 64,
            "urls": [f"https://github.com/LossKarr/lumena/releases/download/v1.2.3/{filename}"],
            "dependencies": dependency, "required": True, "archive_format": "zip",
            "source_revision": REVISION, "license": "Apache-2.0",
        })
    manifest = {
        "schema_version": 1, "release_version": "1.2.3",
        "platform": "windows-x64", "generated_at": "2026-10-11T12:00:00Z",
        "components": components,
    }
    manifest["signature"] = {
        "algorithm": "ed25519", "key_id": "release",
        "value": base64.b64encode(
            private.sign(canonical_component_manifest_bytes(manifest))
        ).decode(),
    }
    manifest_path = tmp_path / "components.json"
    keys_path = tmp_path / "keys.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    keys_path.write_text(json.dumps({"keys": {"release": base64.b64encode(public).decode()}}), encoding="utf-8")
    return manifest_path, keys_path


def test_trust_bundle_is_explicit_and_fails_closed(tmp_path):
    _manifest, keys = _signed_files(tmp_path)
    assert list(load_component_trusted_keys(keys)) == ["release"]
    keys.write_text('{"keys": {"release": "bad"}}', encoding="utf-8")
    with pytest.raises(ComponentServiceError, match="trust key"):
        load_component_trusted_keys(keys)


def test_release_service_installs_in_dependency_order(monkeypatch, tmp_path):
    manifest, keys = _signed_files(tmp_path)
    seen: list[str] = []

    def fake_download(artifact, _cache):
        seen.append(f"download:{artifact.component_id}")
        return tmp_path / artifact.filename

    def fake_install(artifact, _archive, _store, *, canary):
        seen.append(f"install:{artifact.component_id}")
        return ActiveComponent(
            artifact.component_id, artifact.version, artifact.install_dir,
            artifact.sha256, "2026-10-11T12:00:00Z",
        )

    monkeypatch.setattr("src.runtime.component_service.download_component", fake_download)
    monkeypatch.setattr("src.runtime.component_service.install_component_archive", fake_install)
    monkeypatch.setattr(
        "src.runtime.component_service.assemble_qwen_runtime",
        lambda **_kwargs: ActiveComponent(
            "qwen-runtime", "1.0.0", "runtimes/qwen3-tts/1.0.0", "f" * 64,
            "2026-10-11T12:00:00Z",
        ),
    )
    monkeypatch.setattr(
        "src.runtime.component_service.assemble_qwen_model",
        lambda **_kwargs: ActiveComponent(
            "qwen-model", "1.0.0", "components/qwen-model/1.0.0", "2" * 64,
            "2026-10-11T12:00:00Z",
        ),
    )
    result = install_release_components(
        root=tmp_path / "app", manifest_path=manifest, trusted_keys_path=keys,
        expected_release="1.2.3",
    )
    assert result["components"] == [
        "qwen-runtime-shard-01", "qwen-model", "qwen-tokenizer", "lumena-voice",
    ]
    assert seen == [
        "download:qwen-runtime-shard-01", "install:qwen-runtime-shard-01",
        "download:qwen-model", "install:qwen-model",
        "download:qwen-tokenizer", "install:qwen-tokenizer",
        "download:lumena-voice", "install:lumena-voice",
    ]


def test_release_service_rejects_cross_release_manifest(tmp_path):
    manifest, keys = _signed_files(tmp_path)
    with pytest.raises(ComponentServiceError, match="does not match"):
        install_release_components(
            root=tmp_path / "app", manifest_path=manifest,
            trusted_keys_path=keys, expected_release="9.9.9",
        )


def test_release_verifier_requires_split_runtime_and_product_components(tmp_path):
    manifest, keys = _signed_files(tmp_path)
    result = _VERIFY_MODULE.verify_release_manifest(
        manifest, keys, release_version="1.2.3",
    )
    assert result["components"][-3:] == ["qwen-model", "qwen-tokenizer", "lumena-voice"]


def test_component_canaries_validate_product_payloads(tmp_path):
    manifest, keys = _signed_files(tmp_path)
    from src.runtime.component_manifest import ComponentManifest
    parsed = ComponentManifest.from_json(
        manifest.read_bytes(), trusted_keys=load_component_trusted_keys(keys),
    )
    by_id = {item.component_id: item for item in parsed.components}
    runtime = tmp_path / "runtime"
    (runtime / "venv/Scripts").mkdir(parents=True)
    (runtime / "venv/Scripts/python.exe").write_bytes(b"python")
    assert _component_canary(by_id["qwen-runtime-shard-01"], runtime)
    model = tmp_path / "model"
    model.mkdir()
    for path in (model / "config.json", model / "model.safetensors"):
        path.write_bytes(b"x")
    assert _component_canary(by_id["qwen-model"], model)
    tokenizer = tmp_path / "tokenizer"
    tokenizer.mkdir()
    for path in (tokenizer / "config.json", tokenizer / "model.safetensors"):
        path.write_bytes(b"x")
    assert _component_canary(by_id["qwen-tokenizer"], tokenizer)
    voice = tmp_path / "voice"
    voice.mkdir()
    (voice / "lumena.voicepack.zip").write_bytes(b"zip")
    assert _component_canary(by_id["lumena-voice"], voice)


def test_runtime_shards_are_assembled_and_activated(monkeypatch, tmp_path):
    root = tmp_path / "app"
    store = ComponentStore(root)
    first = root / "components/runtime/01"
    second = root / "components/runtime/02"
    (first / "venv/Scripts").mkdir(parents=True)
    (second / "venv/Lib/site-packages/qwen_tts").mkdir(parents=True)
    (second / "sox").mkdir(parents=True)
    (first / "venv/Scripts/python.exe").write_bytes(b"python")
    (first / "venv/pyvenv.cfg").write_text("old", encoding="utf-8")
    (second / "venv/Lib/site-packages/qwen_tts/__init__.py").write_text("", encoding="utf-8")
    (second / "sox/sox.exe").write_bytes(b"sox")
    for index, path in enumerate((first, second), start=1):
        store.activate(ActiveComponent(
            f"qwen-runtime-shard-{index:02d}", "runtime-1", path.relative_to(root).as_posix(),
            str(index) * 64, "2026-10-11T12:00:00Z",
        ))
    monkeypatch.setattr(
        "src.runtime.component_service.subprocess.run",
        lambda *_args, **_kwargs: type("Done", (), {"returncode": 0})(),
    )
    active = assemble_qwen_runtime(root=root)
    target = root / active.path
    assert active.component_id == "qwen-runtime"
    assert (target / "sox/sox.exe").is_file()
    assert "include-system-site-packages = false" in (
        target / "venv/pyvenv.cfg"
    ).read_text(encoding="utf-8")
    assert store.active("qwen-runtime") == active


def test_model_and_tokenizer_are_assembled_without_copying_contract(tmp_path):
    root = tmp_path / "app"
    store = ComponentStore(root)
    model_dir = root / "components/model"
    tokenizer_dir = root / "components/tokenizer"
    model_dir.mkdir(parents=True)
    tokenizer_dir.mkdir(parents=True)
    (model_dir / "config.json").write_bytes(b"{}")
    (model_dir / "model.safetensors").write_bytes(b"model")
    (tokenizer_dir / "config.json").write_bytes(b"{}")
    (tokenizer_dir / "model.safetensors").write_bytes(b"tokenizer")
    model = ActiveComponent(
        "qwen-model", "model-1", model_dir.relative_to(root).as_posix(),
        "a" * 64, "2026-10-11T12:00:00Z",
    )
    store.activate(model)
    store.activate(ActiveComponent(
        "qwen-tokenizer", "tokenizer-1", tokenizer_dir.relative_to(root).as_posix(),
        "b" * 64, "2026-10-11T12:00:00Z",
    ))
    assert assemble_qwen_model(root=root) == model
    assert (model_dir / "speech_tokenizer/model.safetensors").read_bytes() == b"tokenizer"
