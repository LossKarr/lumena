"""Discovery and bounded health probes for the isolated Qwen3-TTS runtime."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import time
from typing import Any, Callable, Mapping

from src.runtime.component_store import ComponentStore
from src.utils.paths import DATA_DIR
from src.utils.persistence import atomic_write_json, safe_read_json


QWEN_RUNTIME_COMPONENT = "qwen-runtime"
QWEN_MODEL_COMPONENT = "qwen-model"
QWEN_RUNTIME_SCHEMA = 1


def component_root() -> Path:
    configured = os.getenv("LUMENA_COMPONENT_ROOT", "").strip()
    return (
        Path(configured).expanduser().resolve()
        if configured else (DATA_DIR / "components").resolve()
    )


def resolve_qwen_paths(
    *, root: Path | None = None, store: ComponentStore | None = None,
) -> dict[str, Path | None]:
    root = Path(root or component_root()).resolve()
    runtime_override = os.getenv("LUMENA_QWEN_RUNTIME_DIR", "").strip()
    model_override = os.getenv("LUMENA_QWEN_MODEL_DIR", "").strip()
    if runtime_override or model_override:
        runtime_dir = Path(runtime_override).expanduser().resolve() if runtime_override else None
        model_dir = Path(model_override).expanduser().resolve() if model_override else None
    else:
        store = store or ComponentStore(root)
        runtime = store.active(QWEN_RUNTIME_COMPONENT)
        model = store.active(QWEN_MODEL_COMPONENT)
        runtime_dir = (root / runtime.path).resolve() if runtime else None
        model_dir = (root / model.path).resolve() if model else None
    python = runtime_dir / "venv" / "Scripts" / "python.exe" if runtime_dir else None
    sox_dir = runtime_dir / "sox" if runtime_dir else None
    state = runtime_dir / "runtime-state.json" if runtime_dir else None
    return {
        "root": root, "runtime_dir": runtime_dir, "model_dir": model_dir,
        "python": python, "sox_dir": sox_dir, "state": state,
    }


def _bounded_text(value: object, limit: int = 1000) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ")[:limit]


def probe_qwen_runtime(
    *, root: Path | None = None, store: ComponentStore | None = None,
    timeout_s: float = 30.0, deep: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    paths = resolve_qwen_paths(root=root, store=store)
    python = paths["python"]
    runtime_dir = paths["runtime_dir"]
    model_dir = paths["model_dir"]
    sox_dir = paths["sox_dir"]
    base: dict[str, Any] = {
        "schema_version": QWEN_RUNTIME_SCHEMA,
        "installed": bool(runtime_dir and model_dir),
        "ready": False,
        "deep_probe": bool(deep),
        "runtime_dir": str(runtime_dir) if runtime_dir else None,
        "model_dir": str(model_dir) if model_dir else None,
        "python": str(python) if python else None,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "error": None,
    }
    if not runtime_dir or not model_dir:
        base["error"] = "runtime_or_model_component_missing"
        return base
    if not python or not python.is_file():
        base["error"] = "runtime_python_missing"
        return base
    required_model_files = (
        model_dir / "config.json", model_dir / "model.safetensors",
        model_dir / "speech_tokenizer" / "model.safetensors",
    )
    if not all(path.is_file() for path in required_model_files):
        base["error"] = "model_payload_incomplete"
        return base
    probe = (
        "import json,sys; import torch,torchaudio,transformers,qwen_tts; "
        "from qwen_tts import Qwen3TTSModel; "
        + (
            "m=Qwen3TTSModel.from_pretrained(sys.argv[1],device_map='cpu',dtype=torch.float32,attn_implementation='eager'); "
            if deep else ""
        )
        + "print(json.dumps({'torch':torch.__version__,'torchaudio':torchaudio.__version__,"
        "'transformers':transformers.__version__,'cuda_available':torch.cuda.is_available(),"
        "'gpu':torch.cuda.get_device_name(0) if torch.cuda.is_available() else None}))"
    )
    env = os.environ.copy()
    if sox_dir and sox_dir.is_dir():
        env["PATH"] = str(sox_dir) + os.pathsep + env.get("PATH", "")
    try:
        result = runner(
            [str(python), "-c", probe, str(model_dir)], cwd=str(runtime_dir), env=env,
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=max(1.0, float(timeout_s)), check=False,
        )
    except subprocess.TimeoutExpired:
        base["error"] = "runtime_probe_timeout"
        return base
    except OSError as exc:
        base["error"] = f"runtime_probe_start_failed:{_bounded_text(exc, 200)}"
        return base
    if result.returncode != 0:
        base["error"] = "runtime_probe_failed"
        base["detail"] = _bounded_text(result.stderr or result.stdout)
        return base
    try:
        payload = json.loads(next(
            line for line in reversed((result.stdout or "").splitlines()) if line.strip().startswith("{")
        ))
    except (StopIteration, json.JSONDecodeError, TypeError):
        base["error"] = "runtime_probe_invalid_output"
        return base
    base.update({
        "ready": True, "torch": str(payload.get("torch", "")),
        "torchaudio": str(payload.get("torchaudio", "")),
        "transformers": str(payload.get("transformers", "")),
        "cuda_available": bool(payload.get("cuda_available")),
        "gpu": payload.get("gpu"),
    })
    state_path = paths.get("state")
    if isinstance(state_path, Path):
        try:
            atomic_write_json(state_path, base)
        except OSError:
            pass
    return base


def cached_qwen_runtime_status(*, root: Path | None = None) -> dict[str, Any]:
    paths = resolve_qwen_paths(root=root)
    state = paths.get("state")
    if isinstance(state, Path):
        value = safe_read_json(state, default={})
        if isinstance(value, Mapping) and value.get("schema_version") == QWEN_RUNTIME_SCHEMA:
            return dict(value)
    return probe_qwen_runtime(root=root, timeout_s=10.0, deep=False)


def qwen_conversation_qualified(*, root: Path | None = None) -> bool:
    status = cached_qwen_runtime_status(root=root)
    performance = status.get("performance")
    return bool(isinstance(performance, Mapping) and performance.get("qualified") is True)


def record_qwen_performance(
    *, qualified: bool, first_audio_ms: int, rtf: float,
    root: Path | None = None,
) -> dict[str, Any]:
    paths = resolve_qwen_paths(root=root)
    state_path = paths.get("state")
    if not isinstance(state_path, Path):
        raise RuntimeError("Qwen runtime state path is unavailable")
    current = safe_read_json(state_path, default={})
    if not isinstance(current, Mapping):
        current = {}
    result = dict(current)
    result.setdefault("schema_version", QWEN_RUNTIME_SCHEMA)
    result["performance"] = {
        "qualified": bool(qualified),
        "first_audio_ms": max(0, int(first_audio_ms)),
        "rtf": max(0.0, float(rtf)),
        "measured_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    atomic_write_json(state_path, result)
    return result
