from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess

from src.runtime.component_store import ComponentStore
from src.voice.v2.qwen_online_installer import install_qwen_from_recipe
from src.voice.v2.qwen_recipe import (
    QwenInstallRecipe,
    QwenModelFile,
    load_qwen_install_recipe,
)


def test_repository_recipe_is_pinned_and_self_contained():
    recipe = load_qwen_install_recipe(
        Path("config/components/qwen3_tts_windows_x64.json")
    )
    assert recipe.model_revision == "5d83992436eae1d760afd27aff78a71d676296fc"
    assert recipe.requirements_path.name == "qwen3_tts_windows_x64.lock"
    assert recipe.required_download_bytes > 5 * 1024 * 1024 * 1024
    protected = {item.path: item.sha256 for item in recipe.model_files if item.sha256}
    assert protected["model.safetensors"] == (
        "180b3b10eb1c9f1b4db7806d5475bae3071c0243c299d49926bab1da3b6946f6"
    )


def test_online_installer_resumes_files_and_activates_only_verified_components(tmp_path):
    payloads = {
        "config.json": b"{}\n",
        "model.safetensors": b"model-fixture",
        "speech_tokenizer/model.safetensors": b"tokenizer-fixture",
    }
    files = tuple(QwenModelFile(
        path=name, size=len(content), sha256=hashlib.sha256(content).hexdigest(),
    ) for name, content in payloads.items())
    lock = tmp_path / "qwen.lock"
    lock.write_text("qwen-tts==0.1.1\n", encoding="utf-8")
    recipe = QwenInstallRecipe(
        path=tmp_path / "recipe.json", recipe_id="qwen-test",
        recipe_revision="test.1", platform="windows-x64",
        python_major=3, python_minor=12, requirements_path=lock,
        index_url="https://pypi.org/simple",
        extra_index_urls=("https://download.pytorch.org/whl/cu128",),
        required_packages={
            "qwen-tts": "0.1.1", "torch": "2.10.0+cu128",
            "torchaudio": "2.10.0+cu128", "transformers": "4.57.3",
        },
        model_id="Qwen/Qwen3-TTS-12Hz-0.6B-Base",
        model_revision="5d83992436eae1d760afd27aff78a71d676296fc",
        model_files=files, estimated_runtime_download_bytes=100_000_000,
        required_disk_bytes=4 * 1024 * 1024 * 1024, fingerprint="a" * 64,
    )
    root = tmp_path / "components"
    python = root / "runtimes" / "qwen3-tts" / "test.1" / "venv" / "Scripts" / "python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"fixture")
    expected = json.dumps(dict(recipe.required_packages), sort_keys=True)

    def runner(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, stdout=expected + "\n", stderr="")

    downloads: list[str] = []

    def downloader(**kwargs):
        name = kwargs["filename"]
        downloads.append(name)
        target = Path(kwargs["local_dir"]).joinpath(*name.split("/"))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(payloads[name])
        return str(target)

    events: list[str] = []
    result = install_qwen_from_recipe(
        root=root, recipe=recipe, progress=events.append,
        downloader=downloader, runner=runner,
    )
    store = ComponentStore(root)
    assert result["source"] == "public_recipe"
    assert store.active("qwen-runtime") is not None
    assert store.active("qwen-model") is not None
    assert downloads == list(payloads)
    assert events[-1] == "online.activate"

    # A retry reuses every verified file instead of downloading it again.
    downloads.clear()
    install_qwen_from_recipe(
        root=root, recipe=recipe, progress=events.append,
        downloader=downloader, runner=runner,
    )
    assert downloads == []
