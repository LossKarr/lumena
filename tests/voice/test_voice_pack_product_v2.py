from __future__ import annotations

import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import zipfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.voice.v2.voice_pack import VoicePackError, canonical_manifest_bytes
from src.voice.v2.voice_pack_service import (
    VoicePackService,
    load_trusted_voice_pack_keys,
)
from src.voice.v2.voice_profile import VoiceProfile


def _piper_pack(
    tmp_path: Path,
    *,
    private: Ed25519PrivateKey | None = None,
    pack_id: str = "lumena-multilingual",
    version: str = "1.0.0",
    language: str = "en",
    model_name: str = "en_GB-test-medium",
    engine: str = "piper",
) -> tuple[Path, bytes]:
    private = private or Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    model = b"safe-onnx-product-fixture"
    config = json.dumps({"audio": {"sample_rate": 22050}, "language": language}).encode()
    model_path = f"models/{model_name}.onnx"
    config_path = f"models/{model_name}.onnx.json"
    manifest = {
        "schema_version": 1,
        "pack_id": pack_id,
        "label": "Lumena multilingual",
        "version": version,
        "engine": engine,
        "languages": [language],
        "models": [{
            "language": language,
            "model_name": model_name,
            "model_path": model_path,
            "config_path": config_path,
        }],
        "files": [
            {"path": model_path, "sha256": hashlib.sha256(model).hexdigest(), "size": len(model)},
            {"path": config_path, "sha256": hashlib.sha256(config).hexdigest(), "size": len(config)},
        ],
        "licenses": [{
            "component": "product fixture", "license": "CC0",
            "commercial_use": True, "source": "https://example.invalid/voice",
        }],
        "voice_consent": {
            "confirmed": True,
            "rights_scope": "training, transformation, distribution, commercial use",
        },
    }
    manifest["signature"] = {
        "algorithm": "ed25519",
        "key_id": "release",
        "value": base64.b64encode(private.sign(canonical_manifest_bytes(manifest))).decode(),
    }
    archive = tmp_path / f"{pack_id}-{version}.zip"
    with zipfile.ZipFile(archive, "w") as output:
        output.writestr("manifest.json", json.dumps(manifest))
        output.writestr(model_path, model)
        output.writestr(config_path, config)
    return archive, public


def test_product_pack_install_activation_profile_routing_and_uninstall(tmp_path):
    archive, public = _piper_pack(tmp_path)
    service = VoicePackService(tmp_path / "voice", trusted_keys={"release": public})

    installed = service.install(archive)
    assert installed == {
        "pack_id": "lumena-multilingual", "version": "1.0.0",
        "languages": ["en"], "active": False,
    }
    activated = service.activate("lumena-multilingual", "1.0.0")
    assert activated["active"] is True
    status = service.status()
    assert status["active"]["pack_id"] == "lumena-multilingual"
    assert status["trusted_signers"] == 1

    profile = service.apply_to_profile(VoiceProfile())
    assert profile.local.piper_model_for("en-GB") == "en_GB-test-medium"
    paths = service.resolve_piper_model("en_GB-test-medium")
    assert paths is not None and paths[0].is_file() and paths[1].is_file()

    with pytest.raises(VoicePackError, match="actif"):
        service.uninstall("lumena-multilingual", "1.0.0")
    service.manager.clear_active()
    service.uninstall("lumena-multilingual", "1.0.0")
    assert service.status()["installed"] == []


def test_activation_rehashes_installed_payload_and_rejects_tampering(tmp_path):
    archive, public = _piper_pack(tmp_path)
    service = VoicePackService(tmp_path / "voice", trusted_keys={"release": public})
    service.install(archive)
    model = next((tmp_path / "voice" / "packs").rglob("*.onnx"))
    model.write_bytes(b"tampered-product-fixture")

    with pytest.raises(VoicePackError, match="corrompu"):
        service.activate("lumena-multilingual", "1.0.0")
    assert service.status()["active"] is None


def test_active_pack_cache_is_invalidated_when_payload_changes(tmp_path):
    archive, public = _piper_pack(tmp_path)
    service = VoicePackService(tmp_path / "voice", trusted_keys={"release": public})
    service.install(archive, activate=True)
    assert service.resolve_piper_model("en_GB-test-medium") is not None
    model = next((tmp_path / "voice" / "packs").rglob("*.onnx"))
    original = model.read_bytes()
    model.write_bytes(b"x" * len(original))

    assert service.resolve_piper_model("en_GB-test-medium") is None
    status = service.status()
    assert status["active"] is None
    assert all(not item["active"] for item in status["installed"])


def test_product_contract_rejects_unsupported_engine_and_cleans_install(tmp_path):
    archive, public = _piper_pack(tmp_path, engine="onnx-local")
    service = VoicePackService(tmp_path / "voice", trusted_keys={"release": public})
    with pytest.raises(VoicePackError, match="moteur"):
        service.install(archive)
    assert service.status()["installed"] == []


def test_pack_identifiers_cannot_escape_product_root(tmp_path):
    service = VoicePackService(tmp_path / "voice", trusted_keys={"release": b"0" * 32})
    with pytest.raises(VoicePackError, match="pack_id"):
        service.activate("../outside", "1.0.0")
    with pytest.raises(VoicePackError, match="pack_id"):
        service.uninstall("../outside", "1.0.0")


def test_profile_is_projected_without_mutating_base_identity(tmp_path):
    archive, public = _piper_pack(tmp_path, language="es", model_name="es_ES-test-low")
    service = VoicePackService(tmp_path / "voice", trusted_keys={"release": public})
    service.install(archive, activate=True)
    base = VoiceProfile()
    before = deepcopy(base.to_dict())
    projected = service.apply_to_profile(base)
    assert base.to_dict() == before
    assert projected.local.piper_model_for("es") == "es_ES-test-low"


def test_trust_store_accepts_only_valid_public_keys(tmp_path, monkeypatch):
    raw = bytes(range(32))
    root = tmp_path / "root"
    data = tmp_path / "data"
    (root / "config").mkdir(parents=True)
    (data / "voice").mkdir(parents=True)
    (root / "config" / "voice_pack_trusted_keys.json").write_text(
        json.dumps({"keys": {"release": base64.b64encode(raw).decode(), "bad": "x"}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "LUMENA_VOICE_PACK_TRUSTED_KEYS_JSON",
        json.dumps({"operator": base64.b64encode(raw[::-1]).decode()}),
    )
    keys = load_trusted_voice_pack_keys(root_dir=root, data_dir=data)
    assert set(keys) == {"release", "operator"}
    assert "bad" not in keys


def test_no_trusted_signer_disables_import_honestly(tmp_path):
    archive, _public = _piper_pack(tmp_path)
    service = VoicePackService(tmp_path / "voice", trusted_keys={})
    assert service.status()["can_import"] is False
    with pytest.raises(VoicePackError, match="aucune clé"):
        service.install(archive)
