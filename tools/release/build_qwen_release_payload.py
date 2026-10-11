"""Build sub-2-GiB Qwen component archives from a qualified Windows runtime.

This tool never downloads floating dependencies. Its inputs are the already
qualified venv, SoX tree, pinned model tree, exact lock/SBOM and signed Voice Pack.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
from typing import Iterable
import zipfile


MAX_RUNTIME_SHARD_BYTES = 1536 * 1024 * 1024
MAX_RELEASE_ASSET_BYTES = 2 * 1024 * 1024 * 1024 - 1024 * 1024
QWEN_REVISION = "5d83992436eae1d760afd27aff78a71d676296fc"


class QwenPayloadBuildError(RuntimeError):
    pass


def _files(root: Path) -> list[Path]:
    ignored = {"__pycache__", ".git", ".pytest_cache"}
    return sorted(
        path for path in root.rglob("*")
        if path.is_file() and not any(part in ignored for part in path.parts)
        and path.suffix.lower() not in {".pyc", ".pyo"}
    )


def _partition(entries: list[tuple[Path, str]], limit: int) -> list[list[tuple[Path, str]]]:
    shards: list[list[tuple[Path, str]]] = []
    sizes: list[int] = []
    for source, archive_name in sorted(entries, key=lambda item: item[0].stat().st_size, reverse=True):
        size = source.stat().st_size
        if size > limit:
            raise QwenPayloadBuildError(f"runtime file exceeds shard limit: {archive_name}")
        target = next((i for i, total in enumerate(sizes) if total + size <= limit), None)
        if target is None:
            shards.append([])
            sizes.append(0)
            target = len(shards) - 1
        shards[target].append((source, archive_name))
        sizes[target] += size
    return shards


def _write_zip(path: Path, entries: Iterable[tuple[Path, str]]) -> Path:
    if path.exists():
        raise QwenPayloadBuildError(f"output already exists: {path.name}")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as package:
        for source, name in sorted(entries, key=lambda item: item[1]):
            package.write(source, name)
    if not path.is_file() or path.stat().st_size >= MAX_RELEASE_ASSET_BYTES:
        path.unlink(missing_ok=True)
        raise QwenPayloadBuildError(f"release asset exceeds GitHub limit: {path.name}")
    return path


def build_qwen_payload(
    *, venv_dir: Path, sox_dir: Path, model_dir: Path, voice_pack: Path,
    lock_file: Path, sbom_file: Path, license_file: Path, output_dir: Path,
    release_version: str, runtime_version: str,
) -> Path:
    inputs = (venv_dir, sox_dir, model_dir)
    if any(not Path(item).is_dir() for item in inputs):
        raise QwenPayloadBuildError("qualified runtime/model input is missing")
    if any(not Path(item).is_file() for item in (voice_pack, lock_file, sbom_file, license_file)):
        raise QwenPayloadBuildError("release metadata or signed Voice Pack is missing")
    if not (model_dir / "model.safetensors").is_file() \
            or not (model_dir / "speech_tokenizer/model.safetensors").is_file():
        raise QwenPayloadBuildError("pinned Qwen model payload is incomplete")
    sox_candidates = sorted(sox_dir.rglob("sox.exe"))
    if len(sox_candidates) != 1:
        raise QwenPayloadBuildError("qualified SoX payload must contain exactly one sox.exe")
    sox_payload_root = sox_candidates[0].parent
    output_dir.mkdir(parents=True, exist_ok=True)
    if any(output_dir.iterdir()):
        raise QwenPayloadBuildError("Qwen component output directory must be empty")

    runtime_entries = [
        *((path, f"venv/{path.relative_to(venv_dir).as_posix()}") for path in _files(venv_dir)),
        *((path, f"sox/{path.relative_to(sox_payload_root).as_posix()}") for path in _files(sox_payload_root)),
        (lock_file, "runtime-metadata/requirements-lock.txt"),
        (sbom_file, "runtime-metadata/sbom-pip-inspect.json"),
        (license_file, "runtime-metadata/license-inventory.json"),
    ]
    runtime_shards = _partition(runtime_entries, MAX_RUNTIME_SHARD_BYTES)
    components = []
    runtime_ids = []
    for index, entries in enumerate(runtime_shards, start=1):
        component_id = f"qwen-runtime-shard-{index:02d}"
        runtime_ids.append(component_id)
        archive = _write_zip(
            output_dir / f"lumena-{component_id}-v{release_version}.zip", entries,
        )
        components.append({
            "id": component_id, "version": runtime_version,
            "platform": "windows-x64", "archive_path": str(archive),
            "install_dir": f"components/qwen-runtime-shards/{runtime_version}/{index:02d}",
            "urls": [f"https://github.com/LossKarr/lumena/releases/download/v{release_version}/{archive.name}"],
            "dependencies": [], "required": True,
            "source_revision": QWEN_REVISION, "license": "MULTIPLE; see runtime SBOM",
        })

    model_entries = [
        (path, path.relative_to(model_dir).as_posix())
        for path in _files(model_dir)
        if "speech_tokenizer" not in path.relative_to(model_dir).parts
    ]
    model_archive = _write_zip(
        output_dir / f"lumena-qwen-model-v{release_version}.zip", model_entries,
    )
    components.append({
        "id": "qwen-model", "version": "0.6b-12hz-" + QWEN_REVISION[:12],
        "platform": "windows-x64", "archive_path": str(model_archive),
        "install_dir": f"components/qwen-model/{QWEN_REVISION}",
        "urls": [f"https://github.com/LossKarr/lumena/releases/download/v{release_version}/{model_archive.name}"],
        "dependencies": runtime_ids, "required": True,
        "source_revision": QWEN_REVISION, "license": "Apache-2.0",
    })

    tokenizer_dir = model_dir / "speech_tokenizer"
    tokenizer_archive = _write_zip(
        output_dir / f"lumena-qwen-tokenizer-v{release_version}.zip",
        ((path, path.relative_to(tokenizer_dir).as_posix()) for path in _files(tokenizer_dir)),
    )
    components.append({
        "id": "qwen-tokenizer", "version": "12hz-" + QWEN_REVISION[:12],
        "platform": "windows-x64", "archive_path": str(tokenizer_archive),
        "install_dir": f"components/qwen-tokenizer/{QWEN_REVISION}",
        "urls": [f"https://github.com/LossKarr/lumena/releases/download/v{release_version}/{tokenizer_archive.name}"],
        "dependencies": ["qwen-model"], "required": True,
        "source_revision": QWEN_REVISION, "license": "Apache-2.0",
    })

    voice_archive = _write_zip(
        output_dir / f"lumena-voice-v{release_version}.zip",
        [(voice_pack, "lumena.voicepack.zip")],
    )
    components.append({
        "id": "lumena-voice", "version": release_version,
        "platform": "windows-x64", "archive_path": str(voice_archive),
        "install_dir": f"components/lumena-voice/{release_version}",
        "urls": [f"https://github.com/LossKarr/lumena/releases/download/v{release_version}/{voice_archive.name}"],
        "dependencies": ["qwen-tokenizer"], "required": True,
        "source_revision": None, "license": "Lumena Voice Pack provenance",
    })

    descriptor = output_dir / "component-descriptor.json"
    descriptor.write_text(json.dumps({
        "release_version": release_version, "platform": "windows-x64",
        "components": components,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return descriptor


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--venv", type=Path, required=True)
    parser.add_argument("--sox", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--voice-pack", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--licenses", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--release-version", required=True)
    parser.add_argument("--runtime-version", required=True)
    args = parser.parse_args()
    descriptor = build_qwen_payload(
        venv_dir=args.venv, sox_dir=args.sox, model_dir=args.model,
        voice_pack=args.voice_pack, lock_file=args.lock, sbom_file=args.sbom,
        license_file=args.licenses, output_dir=args.output,
        release_version=args.release_version, runtime_version=args.runtime_version,
    )
    print(json.dumps({"descriptor": str(descriptor)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
