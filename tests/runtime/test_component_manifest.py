from __future__ import annotations

import base64
import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from src.runtime.component_manifest import (
    ComponentManifest, ComponentManifestError,
    canonical_component_manifest_bytes,
)


def _signed_manifest(*, components=None):
    private = Ed25519PrivateKey.generate()
    public = private.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw,
    )
    value = {
        "schema_version": 1,
        "release_version": "1.2.3",
        "platform": "windows-x64",
        "generated_at": "2026-10-11T12:00:00Z",
        "components": components or [
            {
                "id": "qwen-runtime", "version": "0.1.1-py312-cu128",
                "platform": "windows-x64", "filename": "qwen-runtime.zip",
                "install_dir": "runtimes/qwen3-tts/0.1.1-py312-cu128",
                "size": 1234, "unpacked_size": 4321, "sha256": "a" * 64,
                "urls": [
                    "https://github.com/LossKarr/lumena/releases/download/v1.2.3/qwen-runtime.zip"
                ],
                "dependencies": [], "required": True, "archive_format": "zip",
                "source_revision": "5d83992436eae1d760afd27aff78a71d676296fc",
                "license": "Apache-2.0",
            },
            {
                "id": "qwen-model", "version": "0.6b-12hz-r1",
                "platform": "windows-x64", "filename": "qwen-model.zip",
                "install_dir": "models/qwen3-tts/0.6b-12hz-r1",
                "size": 5678, "unpacked_size": 8765, "sha256": "b" * 64,
                "urls": [
                    "https://github.com/LossKarr/lumena/releases/download/v1.2.3/qwen-model.zip"
                ],
                "dependencies": ["qwen-runtime"], "required": True,
                "archive_format": "zip",
                "source_revision": "5d83992436eae1d760afd27aff78a71d676296fc",
                "license": "Apache-2.0",
            },
        ],
    }
    value["signature"] = {
        "algorithm": "ed25519", "key_id": "release",
        "value": base64.b64encode(private.sign(canonical_component_manifest_bytes(value))).decode(),
    }
    return value, public, private


def test_signed_manifest_is_verified_and_topologically_ordered():
    value, public, _private = _signed_manifest()
    parsed = ComponentManifest.from_json(json.dumps(value), trusted_keys={"release": public})
    assert [item.component_id for item in parsed.installation_order()] == [
        "qwen-runtime", "qwen-model",
    ]
    assert parsed.components[1].source_revision == "5d83992436eae1d760afd27aff78a71d676296fc"


@pytest.mark.parametrize("mutation,match", [
    (lambda value: value.pop("signature"), "schema|signature"),
    (lambda value: value["components"][0].update(size=0), "size"),
    (lambda value: value["components"][0].update(sha256="bad"), "sha256"),
    (lambda value: value["components"][0].update(filename="../runtime.zip"), "filename"),
    (lambda value: value["components"][0].update(install_dir="C:/runtime"), "install"),
    (lambda value: value["components"][0]["urls"].__setitem__(0, "http://example.com/runtime.zip"), "URL"),
    (lambda value: value["components"][0].update(source_revision="main"), "immutable"),
])
def test_manifest_rejects_tampering_and_unsafe_values(mutation, match):
    value, public, private = _signed_manifest()
    mutation(value)
    if "signature" in value:
        value["signature"]["value"] = base64.b64encode(
            private.sign(canonical_component_manifest_bytes(value))
        ).decode()
    with pytest.raises(ComponentManifestError, match=match):
        ComponentManifest.from_mapping(value, trusted_keys={"release": public})


def test_manifest_rejects_unknown_signer_and_unsigned_tampering():
    value, public, _private = _signed_manifest()
    with pytest.raises(ComponentManifestError, match="not trusted"):
        ComponentManifest.from_mapping(value, trusted_keys={"other": public})
    value["components"][0]["size"] += 1
    with pytest.raises(ComponentManifestError, match="signature"):
        ComponentManifest.from_mapping(value, trusted_keys={"release": public})

    value, public, _private = _signed_manifest()
    value["signature"]["value"] = "AAAA"
    with pytest.raises(ComponentManifestError, match="signature"):
        ComponentManifest.from_mapping(value, trusted_keys={"release": public})


def test_manifest_rejects_unknown_and_cyclic_dependencies():
    value, public, private = _signed_manifest()
    value["components"][0]["dependencies"] = ["qwen-model"]
    value["signature"]["value"] = base64.b64encode(
        private.sign(canonical_component_manifest_bytes(value))
    ).decode()
    with pytest.raises(ComponentManifestError, match="cyclic"):
        ComponentManifest.from_mapping(value, trusted_keys={"release": public})

    value, public, private = _signed_manifest()
    value["components"][1]["dependencies"] = ["missing"]
    value["signature"]["value"] = base64.b64encode(
        private.sign(canonical_component_manifest_bytes(value))
    ).decode()
    with pytest.raises(ComponentManifestError, match="unknown"):
        ComponentManifest.from_mapping(value, trusted_keys={"release": public})


def test_manifest_rejects_duplicate_ids_and_unknown_fields():
    value, public, private = _signed_manifest()
    value["components"][1]["id"] = "qwen-runtime"
    value["signature"]["value"] = base64.b64encode(
        private.sign(canonical_component_manifest_bytes(value))
    ).decode()
    with pytest.raises(ComponentManifestError, match="duplicate"):
        ComponentManifest.from_mapping(value, trusted_keys={"release": public})

    value, public, private = _signed_manifest()
    value["components"][0]["surprise"] = True
    value["signature"]["value"] = base64.b64encode(
        private.sign(canonical_component_manifest_bytes(value))
    ).decode()
    with pytest.raises(ComponentManifestError, match="unknown"):
        ComponentManifest.from_mapping(value, trusted_keys={"release": public})
