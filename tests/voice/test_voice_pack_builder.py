from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import zipfile

import pytest

from src.voice.v2.voice_pack import VoicePackManager


_BUILDER_PATH = Path(__file__).resolve().parents[2] / "tools" / "release" / "build_voice_pack.py"
_SPEC = importlib.util.spec_from_file_location("lumena_voice_pack_builder", _BUILDER_PATH)
assert _SPEC is not None and _SPEC.loader is not None
_BUILDER = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = _BUILDER
_SPEC.loader.exec_module(_BUILDER)
VoicePackBuildError = _BUILDER.VoicePackBuildError
build_voice_pack = _BUILDER.build_voice_pack
generate_release_key = _BUILDER.generate_release_key


def _fixture(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TEST_VOICE_PACK_PASSWORD", "correct horse battery staple")
    secrets = tmp_path / "release-secrets"
    private = secrets / "release.pem"
    public = tmp_path / "release-public.json"
    key = generate_release_key(
        private, public, key_id="release-2026", password_env="TEST_VOICE_PACK_PASSWORD",
    )
    assets = tmp_path / "assets"
    (assets / "models").mkdir(parents=True)
    (assets / "models" / "lumena.onnx").write_bytes(b"safe-model")
    (assets / "models" / "lumena.onnx.json").write_text(
        json.dumps({"audio": {"sample_rate": 22050}, "language": "fr"}),
        encoding="utf-8",
    )
    (assets / "CONSENT.md").write_text("Fixture consent evidence", encoding="utf-8")
    descriptor = tmp_path / "descriptor.json"
    descriptor.write_text(json.dumps({
        "pack_id": "lumena-voice",
        "version": "1.0.0",
        "label": "Lumena Voice",
        "engine": "piper",
        "models": [{
            "language": "fr-FR",
            "model_name": "lumena_fr_medium",
            "model_path": "models/lumena.onnx",
            "config_path": "models/lumena.onnx.json",
        }],
        "payload_files": ["CONSENT.md"],
        "licenses": [{
            "component": "Lumena Voice fixture",
            "license": "Proprietary-test",
            "source": "CONSENT.md",
        }],
        "voice_consent": {
            "confirmed": True,
            "rights_scope": "training, transformation, distribution, commercial use",
            "evidence": "CONSENT.md",
        },
    }), encoding="utf-8")
    return key, private, public, assets, descriptor


def test_builder_creates_reproducible_product_verified_pack(tmp_path, monkeypatch):
    key, private, _public, assets, descriptor = _fixture(tmp_path, monkeypatch)
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    result = build_voice_pack(
        descriptor, assets, private, first,
        key_id="release-2026", password_env="TEST_VOICE_PACK_PASSWORD",
    )
    build_voice_pack(
        descriptor, assets, private, second,
        key_id="release-2026", password_env="TEST_VOICE_PACK_PASSWORD",
    )
    assert first.read_bytes() == second.read_bytes()
    assert result["payload_files"] == 3
    manager = VoicePackManager(
        tmp_path / "installed", trusted_keys={"release-2026": key["public_key_base64"]},
    )
    audit = manager.inspect(first)
    assert audit["manifest"]["languages"] == ["fr-FR"]
    with zipfile.ZipFile(first) as archive:
        assert sorted(archive.namelist()) == [
            "CONSENT.md", "manifest.json", "models/lumena.onnx",
            "models/lumena.onnx.json",
        ]
        assert b"PRIVATE KEY" not in b"".join(archive.read(name) for name in archive.namelist())


def test_key_generation_refuses_repo_and_overwrite(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_VOICE_PACK_PASSWORD", "correct horse battery staple")
    monkeypatch.setattr(_BUILDER, "ROOT_DIR", tmp_path)
    with pytest.raises(VoicePackBuildError, match="hors du dépôt"):
        generate_release_key(
            tmp_path / "secret.pem", tmp_path.parent / "public.json",
            key_id="release", password_env="TEST_VOICE_PACK_PASSWORD",
        )
    monkeypatch.setattr(_BUILDER, "ROOT_DIR", tmp_path / "different-repo")
    private = tmp_path / "outside.pem"
    public = tmp_path / "public.json"
    generate_release_key(
        private, public, key_id="release", password_env="TEST_VOICE_PACK_PASSWORD",
    )
    with pytest.raises(VoicePackBuildError, match="aucun écrasement"):
        generate_release_key(
            private, tmp_path / "other.json", key_id="release",
            password_env="TEST_VOICE_PACK_PASSWORD",
        )


def test_builder_rejects_traversal_missing_payload_and_unencrypted_password(
    tmp_path, monkeypatch,
):
    key, private, _public, assets, descriptor = _fixture(tmp_path, monkeypatch)
    payload = json.loads(descriptor.read_text(encoding="utf-8"))
    payload["payload_files"] = ["../secret.txt"]
    descriptor.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(VoicePackBuildError, match="chemin de payload"):
        build_voice_pack(
            descriptor, assets, private, tmp_path / "bad.zip",
            key_id="release-2026", password_env="TEST_VOICE_PACK_PASSWORD",
        )
    monkeypatch.setenv("SHORT_PASSWORD", "short")
    with pytest.raises(VoicePackBuildError, match="au moins 16"):
        generate_release_key(
            tmp_path / "new.pem", tmp_path / "new.json",
            key_id="release", password_env="SHORT_PASSWORD",
        )


def test_builder_never_overwrites_release_archive(tmp_path, monkeypatch):
    _key, private, _public, assets, descriptor = _fixture(tmp_path, monkeypatch)
    output = tmp_path / "voice.zip"
    output.write_bytes(b"existing-release")
    with pytest.raises(VoicePackBuildError, match="déjà présente"):
        build_voice_pack(
            descriptor, assets, private, output,
            key_id="release-2026", password_env="TEST_VOICE_PACK_PASSWORD",
        )
    assert output.read_bytes() == b"existing-release"


def test_builder_requires_consent_evidence_inside_signed_payload(tmp_path, monkeypatch):
    _key, private, _public, assets, descriptor = _fixture(tmp_path, monkeypatch)
    payload = json.loads(descriptor.read_text(encoding="utf-8"))
    payload["payload_files"] = []
    descriptor.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(VoicePackBuildError, match="preuve de consentement"):
        build_voice_pack(
            descriptor, assets, private, tmp_path / "voice.zip",
            key_id="release-2026", password_env="TEST_VOICE_PACK_PASSWORD",
        )


def test_builder_creates_a_product_verified_qwen_pack(tmp_path, monkeypatch):
    monkeypatch.setenv("TEST_VOICE_PACK_PASSWORD", "correct horse battery staple")
    private = tmp_path / "secrets" / "release.pem"
    public = tmp_path / "release-public.json"
    key = generate_release_key(
        private, public, key_id="release-2026", password_env="TEST_VOICE_PACK_PASSWORD",
    )
    assets = tmp_path / "qwen-assets"
    (assets / "identity").mkdir(parents=True)
    (assets / "identity/reference.wav").write_bytes(b"RIFF-fixture")
    (assets / "identity/reference.txt").write_text("Bonjour, je suis Lumena.", encoding="utf-8")
    (assets / "identity/voice.json").write_text('{"schema_version": 1}', encoding="utf-8")
    (assets / "identity/provenance.json").write_text(json.dumps({
        "creation_method": "synthetic fixture",
        "distribution_rights_confirmed": True,
    }), encoding="utf-8")
    (assets / "CONSENT.md").write_text("Fixture consent evidence", encoding="utf-8")
    descriptor = tmp_path / "qwen-descriptor.json"
    descriptor.write_text(json.dumps({
        "pack_id": "lumena-qwen", "version": "1.0.0",
        "label": "Lumena Voice", "engine": "qwen3",
        "languages": ["fr-FR", "en", "es"],
        "qwen": {
            "model_id": "Qwen/Qwen3-TTS-12Hz-0.6B-Base",
            "model_revision": "5d83992436eae1d760afd27aff78a71d676296fc",
            "reference_path": "identity/reference.wav",
            "transcript_path": "identity/reference.txt",
            "config_path": "identity/voice.json",
            "provenance_path": "identity/provenance.json",
        },
        "payload_files": ["CONSENT.md"],
        "licenses": [{"component": "fixture", "license": "test", "source": "CONSENT.md"}],
        "voice_consent": {
            "confirmed": True,
            "rights_scope": "training, transformation, distribution, commercial use",
            "evidence": "CONSENT.md",
        },
    }), encoding="utf-8")
    output = tmp_path / "qwen-voice.zip"
    result = build_voice_pack(
        descriptor, assets, private, output,
        key_id="release-2026", password_env="TEST_VOICE_PACK_PASSWORD",
    )
    assert result["payload_files"] == 5
    service = __import__(
        "src.voice.v2.voice_pack_service", fromlist=["VoicePackService"]
    ).VoicePackService(tmp_path / "installed-qwen", trusted_keys={
        "release-2026": key["public_key_base64"],
    })
    assert service.install(output, activate=True)["engine"] == "qwen3"
