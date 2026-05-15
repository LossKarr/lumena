"""Ollama integration: Modelfile creation, model import, and registry management."""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

# Regex for valid Ollama model names
_MODEL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")


def _validate_model_name(name: str) -> None:
    """Raise ValueError if model name is not safe for subprocess."""
    if not _MODEL_NAME_RE.match(name):
        raise ValueError(
            f"Invalid model name: {name!r}. "
            "Must match ^[a-z0-9][a-z0-9._:-]{{0,63}}$"
        )


# ---------------------------------------------------------------------------
# Modelfile generation
# ---------------------------------------------------------------------------

def write_modelfile(
    gguf_path: str,
    model_name: str,
    system_prompt: str,
    output_path: str,
) -> str:
    """Write an Ollama Modelfile."""
    lines = [f"FROM {gguf_path}"]
    if system_prompt:
        # Escape quotes in system prompt
        safe_prompt = system_prompt.replace('"', '\\"')
        lines.append(f'SYSTEM "{safe_prompt}"')
    lines.extend([
        "PARAMETER temperature 0.7",
        "PARAMETER top_p 0.9",
        'PARAMETER stop "<|im_end|>"',
    ])
    content = "\n".join(lines) + "\n"

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(content, encoding="utf-8")
    _log.info("Modelfile written to %s", output_path)
    return output_path


# ---------------------------------------------------------------------------
# Ollama create
# ---------------------------------------------------------------------------

def ollama_create(model_name: str, modelfile_path: str, timeout: int = 300) -> bool:
    """Create model in Ollama from a Modelfile. Returns True if success."""
    _validate_model_name(model_name)

    mf = Path(modelfile_path)
    if not mf.exists():
        raise FileNotFoundError(f"Modelfile not found: {modelfile_path}")

    try:
        r = subprocess.run(
            ["ollama", "create", model_name, "-f", str(mf)],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode == 0 or "success" in r.stdout.lower():
            _log.info("Ollama model '%s' created successfully", model_name)
            return True
        _log.error("Ollama create failed (rc=%d): %s", r.returncode, r.stderr[:300])
        return False
    except subprocess.TimeoutExpired:
        _log.error("Ollama create timed out after %ds for '%s'", timeout, model_name)
        return False
    except FileNotFoundError:
        _log.error("ollama command not found — is Ollama installed?")
        return False


# ---------------------------------------------------------------------------
# Lumena registry
# ---------------------------------------------------------------------------

def _registry_path() -> Path:
    from src.utils.paths import FINETUNED_REGISTRY
    return FINETUNED_REGISTRY


def list_finetuned_models() -> list[dict[str, Any]]:
    """Read the finetuned models registry."""
    path = _registry_path()
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_registry(models: list[dict[str, Any]]) -> None:
    from src.utils.persistence import atomic_write_json
    atomic_write_json(_registry_path(), models)


def register_in_lumena(
    model_name: str,
    base_model_tag: str,
    trained_on: str,
    *,
    quant_type: str = "Q4_K_M",
    gguf_path: str = "",
    dataset_size: int = 0,
    epochs: int = 0,
) -> None:
    """Register a fine-tuned model in Lumena's history."""
    _validate_model_name(model_name)

    models = list_finetuned_models()
    entry = {
        "model_name": model_name,
        "base_model": base_model_tag,
        "trained_on": trained_on,
        "quant_type": quant_type,
        "gguf_path": gguf_path,
        "dataset_size": dataset_size,
        "epochs": epochs,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    # Replace if same name exists
    models = [m for m in models if m.get("model_name") != model_name]
    models.append(entry)
    _save_registry(models)

    # Also register in Ollama provider catalog
    try:
        from src.llm.providers import register_ollama_models
        register_ollama_models([model_name])
    except Exception as exc:
        _log.warning("Could not register %s in provider catalog: %s", model_name, exc)

    _log.info("Registered fine-tuned model '%s' in Lumena", model_name)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

def delete_finetuned_model(model_name: str) -> bool:
    """Remove a fine-tuned model from Ollama and registry."""
    _validate_model_name(model_name)

    # Remove from Ollama
    try:
        r = subprocess.run(
            ["ollama", "rm", model_name],
            capture_output=True, text=True, timeout=30,
        )
        if r.returncode != 0:
            _log.warning("ollama rm %s failed: %s", model_name, r.stderr[:200])
    except Exception as exc:
        _log.warning("Failed to remove from Ollama: %s", exc)

    # Remove from registry
    models = list_finetuned_models()
    before = len(models)
    models = [m for m in models if m.get("model_name") != model_name]
    if len(models) < before:
        _save_registry(models)
        _log.info("Removed '%s' from finetuned registry", model_name)
        return True

    return False
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
