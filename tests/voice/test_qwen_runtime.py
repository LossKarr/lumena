from __future__ import annotations

import json
from pathlib import Path
import subprocess

from src.runtime.component_store import ActiveComponent, ComponentStore
from src.voice.v2.qwen_runtime import probe_qwen_runtime, resolve_qwen_paths


def _installed(tmp_path: Path) -> ComponentStore:
    store = ComponentStore(tmp_path)
    runtime = tmp_path / "runtimes" / "qwen" / "1"
    model = tmp_path / "models" / "qwen" / "1"
    (runtime / "venv" / "Scripts").mkdir(parents=True)
    (runtime / "venv" / "Scripts" / "python.exe").write_bytes(b"fixture")
    (runtime / "sox").mkdir()
    (model / "speech_tokenizer").mkdir(parents=True)
    for path in (
        model / "config.json", model / "model.safetensors",
        model / "speech_tokenizer" / "model.safetensors",
    ):
        path.write_bytes(b"fixture")
    store.activate(ActiveComponent(
        "qwen-runtime", "1", "runtimes/qwen/1", "a" * 64, "2026-10-11T00:00:00Z",
    ))
    store.activate(ActiveComponent(
        "qwen-model", "1", "models/qwen/1", "b" * 64, "2026-10-11T00:00:00Z",
    ))
    return store


def test_missing_components_are_reported_without_importing_qwen(tmp_path):
    report = probe_qwen_runtime(root=tmp_path, store=ComponentStore(tmp_path))
    assert report["installed"] is False
    assert report["ready"] is False
    assert report["error"] == "runtime_or_model_component_missing"


def test_probe_runs_only_the_isolated_python_and_persists_bounded_state(tmp_path):
    store = _installed(tmp_path)
    calls = []

    def runner(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(
            command, 0,
            stdout='warning\n{"torch":"2.10.0+cu128","torchaudio":"2.10.0+cu128",'
                   '"transformers":"4.57.3","cuda_available":true,"gpu":"RTX"}\n',
            stderr="",
        )

    report = probe_qwen_runtime(root=tmp_path, store=store, runner=runner)
    assert report["ready"] is True and report["cuda_available"] is True
    command, kwargs = calls[0]
    assert command[0].endswith("venv\\Scripts\\python.exe")
    assert "qwen_tts" in command[2]
    assert str(tmp_path / "runtimes" / "qwen" / "1" / "sox") in kwargs["env"]["PATH"]
    state = json.loads((tmp_path / "runtimes" / "qwen" / "1" / "runtime-state.json").read_text())
    assert state["gpu"] == "RTX"


def test_probe_failure_never_marks_runtime_ready(tmp_path):
    store = _installed(tmp_path)
    report = probe_qwen_runtime(
        root=tmp_path, store=store,
        runner=lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, stdout="", stderr="secret-ish\ntrace",
        ),
    )
    assert report["ready"] is False
    assert report["error"] == "runtime_probe_failed"
    assert "secret-ish trace" in report["detail"]


def test_deep_probe_loads_model_only_when_explicit(tmp_path):
    store = _installed(tmp_path)
    scripts = []

    def runner(command, **kwargs):
        scripts.append(command[2])
        return subprocess.CompletedProcess(
            command, 0,
            stdout='{"torch":"x","torchaudio":"x","transformers":"x",'
                   '"cuda_available":false,"gpu":null}\n', stderr="",
        )

    probe_qwen_runtime(root=tmp_path, store=store, deep=False, runner=runner)
    probe_qwen_runtime(root=tmp_path, store=store, deep=True, runner=runner)
    assert "from_pretrained" not in scripts[0]
    assert "from_pretrained" in scripts[1]


def test_environment_overrides_are_explicit_and_pairwise(tmp_path, monkeypatch):
    runtime = tmp_path / "runtime"
    model = tmp_path / "model"
    monkeypatch.setenv("LUMENA_QWEN_RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("LUMENA_QWEN_MODEL_DIR", str(model))
    paths = resolve_qwen_paths(root=tmp_path)
    assert paths["runtime_dir"] == runtime.resolve()
    assert paths["model_dir"] == model.resolve()
