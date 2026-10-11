"""Build an unsigned, reproducible manifest for large Lumena release components.

The descriptor is private release input. Archive sizes and hashes are always read
from disk; they are never trusted from the descriptor.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping
import zipfile


class ComponentManifestBuildError(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_unsigned_manifest(descriptor: Path, output: Path) -> dict[str, Any]:
    if output.exists():
        raise ComponentManifestBuildError("le manifeste de sortie existe déjà")
    try:
        value = json.loads(descriptor.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ComponentManifestBuildError("descripteur de composants invalide") from exc
    if not isinstance(value, Mapping) or not isinstance(value.get("components"), list):
        raise ComponentManifestBuildError("liste de composants absente")
    components: list[dict[str, Any]] = []
    for source in value["components"]:
        if not isinstance(source, Mapping):
            raise ComponentManifestBuildError("entrée composant invalide")
        archive = Path(str(source.get("archive_path", ""))).expanduser().resolve()
        if not archive.is_file() or archive.suffix.lower() != ".zip":
            raise ComponentManifestBuildError(f"archive composant absente: {archive.name}")
        try:
            with zipfile.ZipFile(archive) as package:
                unpacked_size = sum(
                    int(info.file_size) for info in package.infolist() if not info.is_dir()
                )
        except zipfile.BadZipFile as exc:
            raise ComponentManifestBuildError(f"archive composant invalide: {archive.name}") from exc
        item = {key: source.get(key) for key in (
            "id", "version", "platform", "install_dir", "urls", "dependencies",
            "required", "source_revision", "license",
        )}
        item.update({
            "filename": archive.name,
            "size": archive.stat().st_size,
            "unpacked_size": unpacked_size,
            "sha256": _sha256(archive),
            "archive_format": "zip",
        })
        components.append(item)
    manifest = {
        "schema_version": 1,
        "release_version": value.get("release_version"),
        "platform": value.get("platform"),
        "generated_at": value.get("generated_at") or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "components": components,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8", newline="\n",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("descriptor", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = build_unsigned_manifest(args.descriptor, args.output)
    print(json.dumps({"output": str(args.output), "components": len(result["components"])}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
