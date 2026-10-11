"""Resumable online provisioner for the optional isolated Qwen3-TTS runtime."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Any, Callable
import venv

from src.runtime.component_installer import sha256_file
from src.runtime.component_store import ActiveComponent, ComponentStore

from .qwen_recipe import QwenInstallRecipe, QwenRecipeError


class QwenOnlineInstallError(RuntimeError):
    pass


EventCallback = Callable[[str], None]
ByteCallback = Callable[[str, int, int], None]


def _hidden_process_flags() -> int:
    return int(getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _run_checked(
    command: list[str], *, cwd: Path, timeout: float,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update({
        "PIP_DISABLE_PIP_VERSION_CHECK": "1", "PIP_NO_INPUT": "1",
        "PYTHONUTF8": "1", "PYTHONUNBUFFERED": "1",
    })
    try:
        result = runner(
            command, cwd=str(cwd), env=env, capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False,
            creationflags=_hidden_process_flags(),
        )
    except subprocess.TimeoutExpired as exc:
        raise QwenOnlineInstallError("runtime_install_timeout") from exc
    except OSError as exc:
        raise QwenOnlineInstallError("runtime_process_failed") from exc
    if result.returncode != 0:
        raise QwenOnlineInstallError("runtime_command_failed")
    return result


def _runtime_python(runtime_dir: Path) -> Path:
    return runtime_dir / "venv" / "Scripts" / "python.exe"


def _runtime_ready(
    runtime_dir: Path, recipe: QwenInstallRecipe,
    *, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> bool:
    python = _runtime_python(runtime_dir)
    if not python.is_file():
        return False
    expected = json.dumps(dict(recipe.required_packages), sort_keys=True)
    script = (
        "import importlib.metadata as m,json; names=['qwen-tts','torch','torchaudio','transformers']; "
        "print(json.dumps({n:m.version(n) for n in names},sort_keys=True))"
    )
    try:
        result = _run_checked(
            [str(python), "-c", script], cwd=runtime_dir, timeout=60, runner=runner,
        )
        actual = next(line for line in reversed(result.stdout.splitlines()) if line.startswith("{"))
        return actual == expected
    except (QwenOnlineInstallError, StopIteration):
        return False


def _install_runtime(
    recipe: QwenInstallRecipe, runtime_dir: Path, cache_dir: Path,
    *, progress: EventCallback, runner: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    progress("online.runtime.prepare")
    runtime_dir.mkdir(parents=True, exist_ok=True)
    python = _runtime_python(runtime_dir)
    if not python.is_file():
        base_python = Path(getattr(sys, "_base_executable", "") or sys.executable)
        if not base_python.is_file():
            raise QwenOnlineInstallError("python_runtime_missing")
        try:
            venv.EnvBuilder(with_pip=True, clear=False, symlinks=False).create(
                runtime_dir / "venv"
            )
        except Exception as exc:
            raise QwenOnlineInstallError("venv_creation_failed") from exc
    progress("online.runtime.packages")
    cache_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(python), "-m", "pip", "install", "--no-input",
        "--disable-pip-version-check", "--cache-dir", str(cache_dir),
        "--index-url", recipe.index_url,
    ]
    for url in recipe.extra_index_urls:
        command.extend(("--extra-index-url", url))
    command.extend(("--requirement", str(recipe.requirements_path)))
    _run_checked(command, cwd=runtime_dir, timeout=7200, runner=runner)
    if not _runtime_ready(runtime_dir, recipe, runner=runner):
        raise QwenOnlineInstallError("runtime_probe_failed")
    state = {
        "schema_version": 1, "recipe_id": recipe.recipe_id,
        "recipe_revision": recipe.recipe_revision, "recipe_sha256": recipe.fingerprint,
        "python": f"{recipe.python_major}.{recipe.python_minor}",
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    (runtime_dir / "runtime-state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
    )


def _default_hf_download(**kwargs: Any) -> str:
    try:
        from huggingface_hub import hf_hub_download
    except ImportError as exc:
        raise QwenOnlineInstallError("huggingface_client_missing") from exc
    return str(hf_hub_download(**kwargs))


def _download_model(
    recipe: QwenInstallRecipe, model_dir: Path, cache_dir: Path,
    *, progress: EventCallback, byte_progress: ByteCallback,
    downloader: Callable[..., str],
) -> None:
    progress("online.model.download")
    model_dir.mkdir(parents=True, exist_ok=True)
    cache_dir.mkdir(parents=True, exist_ok=True)
    for item in recipe.model_files:
        component_id = f"model:{item.path}"
        target = model_dir.joinpath(*item.path.split("/"))
        valid = target.is_file() and target.stat().st_size == item.size
        if valid and item.sha256:
            valid = sha256_file(target) == item.sha256
        if not valid:
            target.unlink(missing_ok=True)
            try:
                downloaded = Path(downloader(
                    repo_id=recipe.model_id, filename=item.path,
                    revision=recipe.model_revision, repo_type="model",
                    local_dir=str(model_dir), cache_dir=str(cache_dir),
                )).resolve()
            except Exception as exc:
                raise QwenOnlineInstallError("model_download_failed") from exc
            if downloaded != target.resolve() and not target.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(downloaded, target)
        if target.is_symlink():
            # The activated component must remain self-contained even when a
            # Hugging Face client chooses a cache symlink on a developer host.
            source = target.resolve(strict=True)
            temporary = target.with_suffix(target.suffix + ".materialized")
            shutil.copy2(source, temporary)
            target.unlink()
            os.replace(temporary, target)
        try:
            target.resolve(strict=True).relative_to(model_dir.resolve())
        except (OSError, ValueError) as exc:
            raise QwenOnlineInstallError("model_path_escaped") from exc
        if not target.is_file() or target.stat().st_size != item.size:
            raise QwenOnlineInstallError("model_file_size_mismatch")
        if item.sha256 and sha256_file(target) != item.sha256:
            target.unlink(missing_ok=True)
            raise QwenOnlineInstallError("model_checksum_mismatch")
        byte_progress(component_id, item.size, item.size)


def install_qwen_from_recipe(
    *, root: Path, recipe: QwenInstallRecipe,
    progress: EventCallback | None = None,
    byte_progress: ByteCallback | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    downloader: Callable[..., str] = _default_hf_download,
) -> dict[str, Any]:
    """Install and activate the pinned runtime/model while retaining retry caches."""
    progress = progress or (lambda _event: None)
    byte_progress = byte_progress or (lambda _name, _written, _total: None)
    try:
        recipe.assert_host_compatible()
    except QwenRecipeError as exc:
        raise QwenOnlineInstallError(str(exc)) from exc
    root = Path(root).resolve()
    store = ComponentStore(root)
    runtime_dir = root / "runtimes" / "qwen3-tts" / recipe.recipe_revision
    model_dir = root / "components" / "qwen-model" / recipe.model_revision
    caches = root / "downloads" / "qwen3-tts"
    if not _runtime_ready(runtime_dir, recipe, runner=runner):
        _install_runtime(
            recipe, runtime_dir, caches / "pip", progress=progress, runner=runner,
        )
    _download_model(
        recipe, model_dir, caches / "huggingface", progress=progress,
        byte_progress=byte_progress, downloader=downloader,
    )
    progress("online.activate")
    activated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    runtime = ActiveComponent(
        component_id="qwen-runtime", version=recipe.recipe_revision,
        path=runtime_dir.relative_to(root).as_posix(),
        archive_sha256=recipe.fingerprint, activated_at=activated_at,
    )
    model = ActiveComponent(
        component_id="qwen-model", version=recipe.model_revision,
        path=model_dir.relative_to(root).as_posix(),
        archive_sha256=recipe.fingerprint, activated_at=activated_at,
    )
    store.activate(runtime)
    store.activate(model)
    return {
        "source": "public_recipe", "recipe_revision": recipe.recipe_revision,
        "components": [runtime.component_id, model.component_id],
    }
