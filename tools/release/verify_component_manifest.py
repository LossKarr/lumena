"""Fail a release build unless its signed component bundle is complete and exact."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.runtime.component_manifest import ComponentManifest
from src.runtime.component_service import load_component_trusted_keys


REQUIRED_PRODUCT_COMPONENTS = frozenset({
    "qwen-model", "qwen-tokenizer", "lumena-voice",
})


def verify_release_manifest(
    manifest_path: Path, trusted_keys_path: Path, *, release_version: str,
) -> dict:
    manifest = ComponentManifest.from_json(
        manifest_path.read_bytes(),
        trusted_keys=load_component_trusted_keys(trusted_keys_path),
    )
    if manifest.release_version != release_version:
        raise ValueError("component manifest release does not match application")
    present = {item.component_id for item in manifest.components}
    missing = REQUIRED_PRODUCT_COMPONENTS - present
    if missing:
        raise ValueError(f"required product components are missing: {sorted(missing)}")
    runtime_shards = sorted(
        item for item in manifest.components
        if item.component_id.startswith("qwen-runtime-shard-")
    )
    if not runtime_shards:
        raise ValueError("required Qwen runtime shards are missing")
    if any(not item.required for item in manifest.components if item.component_id in REQUIRED_PRODUCT_COMPONENTS):
        raise ValueError("Qwen product components must be required by the installer")
    if any(not item.required for item in runtime_shards):
        raise ValueError("Qwen runtime shards must be required by the installer")
    return {
        "release_version": manifest.release_version,
        "components": [item.component_id for item in manifest.installation_order()],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("manifest", type=Path)
    parser.add_argument("trusted_keys", type=Path)
    parser.add_argument("--release-version", required=True)
    args = parser.parse_args()
    result = verify_release_manifest(
        args.manifest, args.trusted_keys, release_version=args.release_version,
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
