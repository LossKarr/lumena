"""Product orchestration for signed, versioned Lumena release components."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Mapping

from src.runtime.component_installer import (
    download_component, install_component_archive,
)
from src.runtime.component_manifest import ComponentArtifact, ComponentManifest
from src.runtime.component_store import ActiveComponent, ComponentStore
from src.utils.paths import data_dir_for_root


_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_MAX_KEYS = 32


class ComponentServiceError(RuntimeError):
    pass


def load_component_trusted_keys(path: Path) -> dict[str, str]:
    """Load an explicit public-key bundle. Invalid bundles fail closed."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as exc:
        raise ComponentServiceError("component trust bundle is unreadable") from exc
    if isinstance(payload, Mapping) and isinstance(payload.get("keys"), Mapping):
        payload = payload["keys"]
    if not isinstance(payload, Mapping) or not 1 <= len(payload) <= _MAX_KEYS:
        raise ComponentServiceError("component trust bundle is empty or invalid")
    result: dict[str, str] = {}
    for key_id, encoded in payload.items():
        key_id = str(key_id or "").strip()
        encoded = str(encoded or "").strip()
        try:
            raw = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ComponentServiceError("component trust key is invalid") from exc
        if not _KEY_ID.fullmatch(key_id) or len(raw) != 32:
            raise ComponentServiceError("component trust key is invalid")
        result[key_id] = encoded
    return result


def _component_canary(artifact: ComponentArtifact, staging: Path) -> bool:
    files = [item for item in staging.rglob("*") if item.is_file()]
    if not files:
        return False
    if artifact.component_id.startswith("qwen-runtime-shard-"):
        return (staging / "venv").is_dir() or (staging / "sox").is_dir()
    if artifact.component_id == "qwen-model":
        return all(path.is_file() for path in (
            staging / "config.json", staging / "model.safetensors",
        ))
    if artifact.component_id == "qwen-tokenizer":
        return all(path.is_file() for path in (
            staging / "config.json", staging / "model.safetensors",
        ))
    if artifact.component_id == "lumena-voice":
        return len(list(staging.glob("*.voicepack.zip"))) == 1
    return True


def _link_tree(source: Path, destination: Path, *, seen: set[str]) -> None:
    for item in sorted(source.rglob("*")):
        if not item.is_file():
            continue
        rel = item.relative_to(source)
        key = rel.as_posix().lower()
        if key in seen:
            raise ComponentServiceError(f"duplicate component payload path: {rel.as_posix()}")
        seen.add(key)
        target = destination / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(item, target)
        except OSError:
            shutil.copy2(item, target)


def assemble_qwen_runtime(*, root: Path) -> ActiveComponent:
    """Assemble signed sub-2-GiB runtime shards into one validated local runtime."""
    root = Path(root).resolve()
    store = ComponentStore(root)
    active_state = store.state()["active"]
    shard_ids = sorted(
        key for key in active_state if str(key).startswith("qwen-runtime-shard-")
    )
    shards = [store.active(key) for key in shard_ids]
    if not shards or any(item is None for item in shards):
        raise ComponentServiceError("Qwen runtime shards are incomplete")
    resolved = [item for item in shards if item is not None]
    versions = {item.version for item in resolved}
    if len(versions) != 1:
        raise ComponentServiceError("Qwen runtime shard versions do not match")
    version = next(iter(versions))
    digest_source = "".join(item.archive_sha256 for item in resolved).encode("ascii")
    import hashlib
    assembled_sha = hashlib.sha256(digest_source).hexdigest()
    target = root / "runtimes" / "qwen3-tts" / version
    existing = store.active("qwen-runtime")
    if existing and existing.version == version and existing.archive_sha256 == assembled_sha:
        return existing
    if target.exists():
        raise ComponentServiceError("untracked Qwen runtime target already exists")
    staging_parent = root / ".staging"
    staging_parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix="qwen-runtime-assemble-", dir=staging_parent))
    try:
        seen: set[str] = set()
        for item in resolved:
            _link_tree(root / item.path, staging, seen=seen)
        python = staging / "venv" / "Scripts" / "python.exe"
        sox = staging / "sox" / "sox.exe"
        if not python.is_file() or not sox.is_file():
            raise ComponentServiceError("assembled Qwen runtime is incomplete")
        base = Path(getattr(sys, "_base_executable", sys.executable)).resolve()
        pyvenv = staging / "venv" / "pyvenv.cfg"
        pyvenv.write_text(
            f"home = {base.parent}\ninclude-system-site-packages = false\n"
            f"version = {sys.version.split()[0]}\nexecutable = {base}\n",
            encoding="utf-8", newline="\n",
        )
        env = os.environ.copy()
        env.update({"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
        env["PATH"] = str(staging / "sox") + os.pathsep + env.get("PATH", "")
        result = subprocess.run(
            [str(python), "-c", "import torch,torchaudio,transformers,qwen_tts,soundfile; print(torch.__version__)"],
            cwd=staging, env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=60, check=False,
        )
        if result.returncode != 0:
            raise ComponentServiceError("assembled Qwen runtime import canary failed")
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staging, target)
        component = ActiveComponent(
            "qwen-runtime", version, target.relative_to(root).as_posix(),
            assembled_sha, time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        )
        store.activate(component)
        return component
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def assemble_qwen_model(*, root: Path) -> ActiveComponent:
    """Attach the separately published speech tokenizer to the model atomically."""
    root = Path(root).resolve()
    store = ComponentStore(root)
    model = store.active("qwen-model")
    tokenizer = store.active("qwen-tokenizer")
    if model is None or tokenizer is None:
        raise ComponentServiceError("Qwen model components are incomplete")
    model_dir = root / model.path
    tokenizer_dir = root / tokenizer.path
    target = model_dir / "speech_tokenizer"
    if target.is_dir() and (target / "model.safetensors").is_file():
        return model
    staging = model_dir / ".speech-tokenizer.building"
    if staging.exists():
        shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)
    try:
        _link_tree(tokenizer_dir, staging, seen=set())
        if not (staging / "config.json").is_file() or not (staging / "model.safetensors").is_file():
            raise ComponentServiceError("Qwen speech tokenizer is incomplete")
        os.replace(staging, target)
        return model
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def install_release_components(
    *, root: Path, manifest_path: Path, trusted_keys_path: Path,
    expected_release: str | None = None,
    progress: Callable[[str], None] | None = None,
    byte_progress: Callable[[str, int, int], None] | None = None,
) -> dict[str, Any]:
    """Verify, download, canary and atomically activate every manifest component."""
    emit = progress or (lambda _message: None)
    keys = load_component_trusted_keys(trusted_keys_path)
    try:
        manifest = ComponentManifest.from_json(
            Path(manifest_path).read_bytes(), trusted_keys=keys,
        )
    except Exception as exc:
        raise ComponentServiceError("signed component manifest is invalid") from exc
    if expected_release and manifest.release_version != str(expected_release):
        raise ComponentServiceError("component manifest release does not match Lumena")
    root = Path(root).resolve()
    store = ComponentStore(root)
    cache = data_dir_for_root(root) / "components" / "downloads"
    installed: list[ActiveComponent] = []
    for artifact in manifest.installation_order():
        emit(f"component.download:{artifact.component_id}")
        if byte_progress is None:
            archive = download_component(artifact, cache)
        else:
            archive = download_component(
                artifact, cache,
                progress=(lambda written, total, component_id=artifact.component_id:
                          byte_progress(component_id, written, total)),
            )
        emit(f"component.verify:{artifact.component_id}")
        active = install_component_archive(
            artifact, archive, store,
            canary=lambda staging, item=artifact: _component_canary(item, staging),
        )
        installed.append(active)
        emit(f"component.active:{artifact.component_id}:{artifact.version}")
    runtime = assemble_qwen_runtime(root=root)
    model = assemble_qwen_model(root=root)
    emit(f"component.active:{runtime.component_id}:{runtime.version}")
    emit(f"component.assembled:{model.component_id}:{model.version}")
    return {
        "schema_version": 1,
        "release_version": manifest.release_version,
        "components": [item.component_id for item in installed],
        "active": {
            **{item.component_id: item.version for item in installed},
            runtime.component_id: runtime.version,
        },
    }


