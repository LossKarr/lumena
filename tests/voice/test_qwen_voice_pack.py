from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path
import zipfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.voice.v2.voice_pack import VoicePackError, canonical_manifest_bytes
from src.voice.v2.voice_pack_service import VoicePackService
from src.voice.v2.voice_profile import VoiceProfile


REVISION = "5d83992436eae1d760afd27aff78a71d676296fc"


def _pack(tmp_path: Path, *, provenance_ok: bool = True, revision: str = REVISION):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    payload = {
        "identity/reference.wav": b"RIFF-fixture",
        "identity/reference.txt": "Bonjour, je suis Lumena.".encode(),
        "identity/voice.json": json.dumps({"schema_version": 1, "temperature": 0.9}).encode(),
        "identity/provenance.json": json.dumps({
            "creation_method": "synthetic fixture",
            "distribution_rights_confirmed": provenance_ok,
        }).encode(),
    }
    manifest = {
        "schema_version": 1, "pack_id": "lumena-qwen", "version": "1.0.0",
        "engine": "qwen3", "languages": ["fr-FR", "en", "es"],
        "qwen": {
            "model_id": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
            "model_revision": revision,
            "reference_path": "identity/reference.wav",
            "transcript_path": "identity/reference.txt",
            "config_path": "identity/voice.json",
            "provenance_path": "identity/provenance.json",
        },
        "files": [
            {"path": name, "size": len(content), "sha256": hashlib.sha256(content).hexdigest()}
            for name, content in payload.items()
        ],
        "licenses": [{"component": "fixture", "license": "CC0", "source": "test"}],
        "voice_consent": {"confirmed": True, "rights_scope": "distribution and commercial use"},
    }
    manifest["signature"] = {
        "algorithm": "ed25519", "key_id": "release",
        "value": base64.b64encode(private.sign(canonical_manifest_bytes(manifest))).decode(),
    }
    archive = tmp_path / "voice.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("manifest.json", json.dumps(manifest))
        for name, content in payload.items():
            package.writestr(name, content)
    return archive, public


def test_qwen_pack_installs_activates_and_projects_identity(tmp_path):
    archive, public = _pack(tmp_path)
    service = VoicePackService(tmp_path / "voice", trusted_keys={"release": public})
    installed = service.install(archive, activate=True)
    assert installed["engine"] == "qwen3"
    profile = service.apply_to_profile(VoiceProfile())
    assert Path(profile.local.qwen_reference).is_file()
    assert profile.local.qwen_reference_text == "Bonjour, je suis Lumena."
    assert profile.local.qwen_model_revision == REVISION
    assert profile.reference_consent_confirmed is True
    assert "droits de distribution" in profile.reference_rights_note
    identity = service.resolve_qwen_identity()
    assert identity and identity["languages"] == ["fr", "en", "es"]


@pytest.mark.parametrize("kwargs,match", [
    ({"revision": "main"}, "épinglé"),
    ({"provenance_ok": False}, "provenance"),
])
def test_qwen_pack_rejects_mutable_model_and_unproven_identity(tmp_path, kwargs, match):
    archive, public = _pack(tmp_path, **kwargs)
    service = VoicePackService(tmp_path / "voice", trusted_keys={"release": public})
    with pytest.raises(VoicePackError, match=match):
        service.install(archive)
    assert service.status()["installed"] == []
