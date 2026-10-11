"""Validated public installation recipe for the optional Qwen3-TTS runtime."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import platform
import re
import sys
from typing import Any, Mapping


class QwenRecipeError(RuntimeError):
    pass


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_REVISION = re.compile(r"^[0-9a-f]{40}$")
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,95}$")
_ALLOWED_INDEXES = {
    "https://pypi.org/simple",
    "https://download.pytorch.org/whl/cu128",
    "https://download.pytorch.org/whl/cpu",
}


@dataclass(frozen=True)
class QwenModelFile:
    path: str
    size: int
    sha256: str | None = None


@dataclass(frozen=True)
class QwenInstallRecipe:
    path: Path
    recipe_id: str
    recipe_revision: str
    platform: str
    python_major: int
    python_minor: int
    requirements_path: Path
    index_url: str
    extra_index_urls: tuple[str, ...]
    required_packages: Mapping[str, str]
    model_id: str
    model_revision: str
    model_files: tuple[QwenModelFile, ...]
    estimated_runtime_download_bytes: int
    required_disk_bytes: int
    fingerprint: str

    @property
    def model_download_bytes(self) -> int:
        return sum(item.size for item in self.model_files)

    @property
    def required_download_bytes(self) -> int:
        return self.model_download_bytes + self.estimated_runtime_download_bytes

    def assert_host_compatible(self) -> None:
        machine = platform.machine().lower()
        if self.platform != "windows-x64" or sys.platform != "win32" \
                or machine not in {"amd64", "x86_64"}:
            raise QwenRecipeError("unsupported_platform")
        if sys.version_info[:2] != (self.python_major, self.python_minor):
            raise QwenRecipeError("unsupported_python")


def _safe_relative_path(value: object, *, suffix: str | None = None) -> str:
    raw = str(value or "")
    rel = PurePosixPath(raw)
    if not raw or rel.is_absolute() or "\\" in raw \
            or any(part in {"", ".", ".."} for part in rel.parts):
        raise QwenRecipeError("invalid_recipe_path")
    if suffix and not raw.endswith(suffix):
        raise QwenRecipeError("invalid_recipe_path")
    return rel.as_posix()


def _mapping(value: object, error: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise QwenRecipeError(error)
    return value


def load_qwen_install_recipe(path: Path) -> QwenInstallRecipe:
    """Load a repository-owned recipe and reject ambiguous or mutable inputs."""
    path = Path(path).resolve()
    try:
        raw = path.read_bytes()
        payload = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        raise QwenRecipeError("recipe_missing_or_invalid") from exc
    data = _mapping(payload, "recipe_invalid")
    if data.get("schema_version") != 1:
        raise QwenRecipeError("recipe_schema_unsupported")
    recipe_id = str(data.get("recipe_id", ""))
    recipe_revision = str(data.get("recipe_revision", ""))
    if not _SAFE_ID.fullmatch(recipe_id) or not _SAFE_ID.fullmatch(recipe_revision):
        raise QwenRecipeError("recipe_identity_invalid")
    python = _mapping(data.get("python"), "recipe_python_invalid")
    runtime = _mapping(data.get("runtime"), "recipe_runtime_invalid")
    model = _mapping(data.get("model"), "recipe_model_invalid")
    requirements_rel = _safe_relative_path(runtime.get("requirements"), suffix=".lock")
    requirements_path = (path.parent / requirements_rel).resolve()
    try:
        requirements_path.relative_to(path.parent)
    except ValueError as exc:
        raise QwenRecipeError("invalid_recipe_path") from exc
    if not requirements_path.is_file():
        raise QwenRecipeError("requirements_lock_missing")
    index_url = str(runtime.get("index_url", ""))
    extra_indexes = tuple(str(item) for item in runtime.get("extra_index_urls", []))
    if index_url not in _ALLOWED_INDEXES or any(item not in _ALLOWED_INDEXES for item in extra_indexes):
        raise QwenRecipeError("package_index_not_allowed")
    packages = _mapping(runtime.get("required_packages"), "required_packages_invalid")
    required = {str(name): str(version) for name, version in packages.items()}
    if required != {
        "qwen-tts": "0.1.1", "torch": "2.10.0+cu128",
        "torchaudio": "2.10.0+cu128", "transformers": "4.57.3",
    }:
        raise QwenRecipeError("required_packages_invalid")
    model_id = str(model.get("repo_id", ""))
    revision = str(model.get("revision", "")).lower()
    if model_id != "Qwen/Qwen3-TTS-12Hz-0.6B-Base" or not _REVISION.fullmatch(revision):
        raise QwenRecipeError("model_source_not_pinned")
    files_raw = model.get("files")
    if not isinstance(files_raw, list) or not files_raw or len(files_raw) > 128:
        raise QwenRecipeError("model_file_list_invalid")
    files: list[QwenModelFile] = []
    seen: set[str] = set()
    for item in files_raw:
        value = _mapping(item, "model_file_invalid")
        file_path = _safe_relative_path(value.get("path"))
        if file_path in seen:
            raise QwenRecipeError("model_file_duplicate")
        seen.add(file_path)
        try:
            size = int(value.get("size", 0))
        except (TypeError, ValueError) as exc:
            raise QwenRecipeError("model_file_invalid") from exc
        sha256 = str(value.get("sha256", "")).lower() or None
        if size <= 0 or size > 4 * 1024 * 1024 * 1024 \
                or (sha256 is not None and not _SHA256.fullmatch(sha256)):
            raise QwenRecipeError("model_file_invalid")
        files.append(QwenModelFile(file_path, size, sha256))
    required_model_files = {
        "config.json", "model.safetensors", "speech_tokenizer/model.safetensors",
    }
    if not required_model_files.issubset(seen):
        raise QwenRecipeError("model_file_list_incomplete")
    try:
        runtime_bytes = int(data.get("estimated_runtime_download_bytes", 0))
        disk_bytes = int(data.get("required_disk_bytes", 0))
        python_major = int(python.get("major"))
        python_minor = int(python.get("minor"))
    except (TypeError, ValueError) as exc:
        raise QwenRecipeError("recipe_sizes_invalid") from exc
    if runtime_bytes < 100 * 1024 * 1024 or disk_bytes < 4 * 1024 * 1024 * 1024:
        raise QwenRecipeError("recipe_sizes_invalid")
    if (python_major, python_minor) != (3, 12):
        raise QwenRecipeError("recipe_python_invalid")
    return QwenInstallRecipe(
        path=path, recipe_id=recipe_id, recipe_revision=recipe_revision,
        platform=str(data.get("platform", "")), python_major=python_major,
        python_minor=python_minor, requirements_path=requirements_path,
        index_url=index_url, extra_index_urls=extra_indexes,
        required_packages=required, model_id=model_id, model_revision=revision,
        model_files=tuple(files), estimated_runtime_download_bytes=runtime_bytes,
        required_disk_bytes=disk_bytes, fingerprint=hashlib.sha256(raw).hexdigest(),
    )
