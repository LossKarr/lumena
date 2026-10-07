from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import zipfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.voice.v2.voice_pack import (
    VoicePackError, VoicePackManager, canonical_manifest_bytes,
)


def _signed_pack(tmp_path: Path, *, version: str = "1.0.0", bad_hash: bool = False):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    model = b"safe-onnx-fixture"
    digest = "0" * 64 if bad_hash else hashlib.sha256(model).hexdigest()
    manifest = {
        "schema_version": 1,
        "pack_id": "lumena-voice",
        "version": version,
        "engine": "onnx-local",
        "languages": ["fr"],
        "files": [{"path": "model/voice.onnx", "sha256": digest, "size": len(model)}],
        "licenses": [{
            "component": "fixture", "license": "Apache-2.0",
            "commercial_use": True, "source": "https://example.invalid/model",
        }],
        "voice_consent": {
            "confirmed": True,
            "rights_scope": "training, transformation, distribution, commercial use",
        },
    }
    signature = private.sign(canonical_manifest_bytes(manifest))
    manifest["signature"] = {
        "algorithm": "ed25519", "key_id": "release",
        "value": base64.b64encode(signature).decode("ascii"),
    }
    archive_path = tmp_path / f"voice-{version}.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("model/voice.onnx", model)
    return archive_path, public


def _custom_signed_pack(tmp_path: Path, files: dict[str, bytes], *, compression=zipfile.ZIP_STORED):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    manifest = {
        "schema_version": 1, "pack_id": "custom-voice", "version": "1.0.0",
        "engine": "onnx-local", "languages": ["fr"],
        "files": [
            {"path": name, "sha256": hashlib.sha256(payload).hexdigest(), "size": len(payload)}
            for name, payload in files.items()
        ],
        "licenses": [{"component": "fixture", "license": "MIT", "source": "local"}],
        "voice_consent": {"confirmed": True, "rights_scope": "distribution"},
    }
    manifest["signature"] = {
        "algorithm": "ed25519", "key_id": "release",
        "value": base64.b64encode(private.sign(canonical_manifest_bytes(manifest))).decode(),
    }
    archive_path = tmp_path / "custom.zip"
    with zipfile.ZipFile(archive_path, "w", compression=compression) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        for name, payload in files.items():
            archive.writestr(name, payload)
    return archive_path, public


def test_signed_offline_pack_install_activate_rollback_and_uninstall(tmp_path):
    pack1, key = _signed_pack(tmp_path, version="1.0.0")
    pack2, _unused = _signed_pack(tmp_path, version="1.1.0")
    # Re-sign the second manifest with the trusted release key.
    with zipfile.ZipFile(pack2, "r") as source:
        manifest = json.loads(source.read("manifest.json"))
        model = source.read("model/voice.onnx")
    # The helper made another key, so use a second trusted id for this fixture.
    pack2, key2 = _signed_pack(tmp_path, version="1.1.0")
    manager = VoicePackManager(tmp_path / "installed", trusted_keys={
        "release": key,
    })
    target1 = manager.install(pack1)
    assert (target1 / "model" / "voice.onnx").read_bytes() == b"safe-onnx-fixture"

    manager2 = VoicePackManager(tmp_path / "installed", trusted_keys={"release": key2})
    target2 = manager2.install(pack2)
    assert target2.is_dir()
    manager2.rollback()
    active = json.loads(manager2.active_file.read_text(encoding="utf-8"))
    assert (active["pack_id"], active["version"]) == ("lumena-voice", "1.0.0")
    manager2.uninstall("lumena-voice", "1.1.0")
    assert not target2.exists()


def test_hash_mismatch_never_reaches_installation(tmp_path):
    archive, key = _signed_pack(tmp_path, bad_hash=True)
    manager = VoicePackManager(tmp_path / "installed", trusted_keys={"release": key})
    with pytest.raises(VoicePackError, match="empreinte incorrecte"):
        manager.install(archive)
    assert not (tmp_path / "installed" / "packs").exists()


def test_untrusted_signature_is_rejected(tmp_path):
    archive, _key = _signed_pack(tmp_path)
    other = Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    manager = VoicePackManager(tmp_path / "installed", trusted_keys={"release": other})
    with pytest.raises(VoicePackError, match="signature Voice Pack invalide"):
        manager.inspect(archive)


def test_archive_path_traversal_is_rejected(tmp_path):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )
    payload = b"escape"
    manifest = {
        "schema_version": 1, "pack_id": "lumena-voice", "version": "1.0.0",
        "engine": "onnx-local", "languages": ["fr"],
        "files": [{
            "path": "../escape.onnx", "size": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
        }],
        "licenses": [{"component": "x", "license": "MIT", "source": "local"}],
        "voice_consent": {"confirmed": True, "rights_scope": "distribution"},
    }
    manifest["signature"] = {
        "algorithm": "ed25519", "key_id": "release",
        "value": base64.b64encode(private.sign(canonical_manifest_bytes(manifest))).decode(),
    }
    archive = tmp_path / "traversal.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("manifest.json", json.dumps(manifest))
        output.writestr("../escape.onnx", payload)
    manager = VoicePackManager(tmp_path / "installed", trusted_keys={"release": public})
    with pytest.raises(VoicePackError, match="chemin de pack interdit"):
        manager.inspect(archive)
    assert not (tmp_path / "escape.onnx").exists()


def test_active_or_in_use_pack_cannot_be_removed(tmp_path):
    archive, key = _signed_pack(tmp_path)
    manager = VoicePackManager(
        tmp_path / "installed", trusted_keys={"release": key},
        in_use=lambda pack, version: pack == "lumena-voice" and version == "0.9.0",
    )
    manager.install(archive)
    with pytest.raises(VoicePackError, match="actif"):
        manager.uninstall("lumena-voice", "1.0.0")
    with pytest.raises(VoicePackError, match="utilisé"):
        manager.uninstall("lumena-voice", "0.9.0")


def test_executable_payload_is_rejected_even_when_signed(tmp_path):
    archive, key = _custom_signed_pack(tmp_path, {"model/setup.exe": b"MZ"})
    manager = VoicePackManager(tmp_path / "installed", trusted_keys={"release": key})
    with pytest.raises(VoicePackError, match="type de fichier interdit"):
        manager.inspect(archive)


def test_compression_bomb_is_rejected_before_extraction(tmp_path):
    archive, key = _custom_signed_pack(
        tmp_path, {"model/voice.bin": b"0" * 100_000}, compression=zipfile.ZIP_DEFLATED
    )
    manager = VoicePackManager(
        tmp_path / "installed", trusted_keys={"release": key}, max_compression_ratio=5
    )
    with pytest.raises(VoicePackError, match="ratio de compression"):
        manager.install(archive)
    assert not (tmp_path / "installed" / "packs").exists()


def test_file_count_limit_is_enforced_before_extraction(tmp_path):
    archive, key = _custom_signed_pack(
        tmp_path, {f"model/{index}.bin": bytes([index]) for index in range(4)}
    )
    manager = VoicePackManager(
        tmp_path / "installed", trusted_keys={"release": key}, max_files=2
    )
    with pytest.raises(VoicePackError, match="trop de fichiers"):
        manager.inspect(archive)


def test_symbolic_link_entry_is_rejected(tmp_path):
    archive, key = _custom_signed_pack(tmp_path, {"model/voice.onnx": b"target"})
    with zipfile.ZipFile(archive, "r") as source:
        manifest = source.read("manifest.json")
    link = zipfile.ZipInfo("model/voice.onnx")
    link.create_system = 3
    link.external_attr = 0o120777 << 16
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("manifest.json", manifest)
        output.writestr(link, b"target")
    manager = VoicePackManager(tmp_path / "installed", trusted_keys={"release": key})
    with pytest.raises(VoicePackError, match="liens symboliques"):
        manager.inspect(archive)
