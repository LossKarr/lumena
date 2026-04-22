"""GPU detection, model recommendations, and dependency checking for local fine-tuning."""
from __future__ import annotations

import importlib.util
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any

import httpx

from src.llm.providers import OLLAMA_CATALOG

_log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Fine-tune catalog — enriched from OLLAMA_CATALOG
# ---------------------------------------------------------------------------

_HF_MAP: dict[str, dict[str, Any]] = {
    "qwen3:0.6b":       {"hf_4bit": "unsloth/Qwen3-0.6B-bnb-4bit",                      "hf_full": "Qwen/Qwen3-0.6B",                          "vram_ft": 2},
    "gemma3:1b":         {"hf_4bit": "unsloth/gemma-3-1b-it-bnb-4bit",                    "hf_full": "google/gemma-3-1b-it",                      "vram_ft": 3},
    "qwen3:1.7b":        {"hf_4bit": "unsloth/Qwen3-1.7B-bnb-4bit",                      "hf_full": "Qwen/Qwen3-1.7B",                          "vram_ft": 4},
    "llama3.2:3b":       {"hf_4bit": "unsloth/Llama-3.2-3B-Instruct-bnb-4bit",            "hf_full": "meta-llama/Llama-3.2-3B-Instruct",          "vram_ft": 5},
    "phi4-mini":         {"hf_4bit": "unsloth/Phi-4-mini-instruct-bnb-4bit",              "hf_full": "microsoft/Phi-4-mini-instruct",              "vram_ft": 5},
    "qwen3:4b":          {"hf_4bit": "unsloth/Qwen3-4B-bnb-4bit",                        "hf_full": "Qwen/Qwen3-4B",                             "vram_ft": 6},
    "gemma3:4b":         {"hf_4bit": "unsloth/gemma-3-4b-it-bnb-4bit",                    "hf_full": "google/gemma-3-4b-it",                      "vram_ft": 6},
    "gemma4:e2b":        {"hf_4bit": "unsloth/gemma-4-e2b-it-bnb-4bit",                   "hf_full": "google/gemma-4-e2b-it",                     "vram_ft": 12},
    "mistral:7b":        {"hf_4bit": "unsloth/mistral-7b-instruct-v0.3-bnb-4bit",         "hf_full": "mistralai/Mistral-7B-Instruct-v0.3",         "vram_ft": 8},
    "deepseek-r1:7b":    {"hf_4bit": "unsloth/DeepSeek-R1-Distill-Qwen-7B-bnb-4bit",     "hf_full": "deepseek-ai/DeepSeek-R1-Distill-Qwen-7B",   "vram_ft": 8},
    "llama3.3:8b":       {"hf_4bit": "unsloth/Meta-Llama-3.1-8B-Instruct-bnb-4bit",      "hf_full": "meta-llama/Llama-3.1-8B-Instruct",          "vram_ft": 10},
    "qwen3:8b":          {"hf_4bit": "unsloth/Qwen3-8B-bnb-4bit",                        "hf_full": "Qwen/Qwen3-8B",                             "vram_ft": 10},
    "minicpm-v":         {"hf_4bit": "openbmb/MiniCPM-V-2_6",                             "hf_full": "openbmb/MiniCPM-V-2_6",                     "vram_ft": 10},
    "llava:7b":          {"hf_4bit": "unsloth/llava-1.5-7b-hf-bnb-4bit",                  "hf_full": "llava-hf/llava-1.5-7b-hf",                  "vram_ft": 8},
    "gemma4:e4b":        {"hf_4bit": "unsloth/gemma-4-e4b-it-bnb-4bit",                   "hf_full": "google/gemma-4-e4b-it",                     "vram_ft": 12},
    "gemma3:12b":        {"hf_4bit": "unsloth/gemma-3-12b-it-bnb-4bit",                   "hf_full": "google/gemma-3-12b-it",                     "vram_ft": 14},
    "llava:13b":         {"hf_4bit": "unsloth/llava-1.5-13b-hf-bnb-4bit",                 "hf_full": "llava-hf/llava-1.5-13b-hf",                 "vram_ft": 14},
    "qwen2.5-coder:14b": {"hf_4bit": "unsloth/Qwen2.5-Coder-14B-Instruct-bnb-4bit",     "hf_full": "Qwen/Qwen2.5-Coder-14B-Instruct",           "vram_ft": 16},
    "qwen3:14b":         {"hf_4bit": "unsloth/Qwen3-14B-bnb-4bit",                       "hf_full": "Qwen/Qwen3-14B",                            "vram_ft": 16},
    "deepseek-r1:14b":   {"hf_4bit": "unsloth/DeepSeek-R1-Distill-Qwen-14B-bnb-4bit",   "hf_full": "deepseek-ai/DeepSeek-R1-Distill-Qwen-14B",  "vram_ft": 16},
    "codestral":         {"hf_4bit": "mistralai/Codestral-22B-v0.1",                      "hf_full": "mistralai/Codestral-22B-v0.1",              "vram_ft": 20},
    "mistral-small":     {"hf_4bit": "unsloth/Mistral-Small-24B-Instruct-2501-bnb-4bit", "hf_full": "mistralai/Mistral-Small-24B-Instruct-2501",  "vram_ft": 22},
    "gemma4:26b":        {"hf_4bit": "unsloth/gemma-4-26b-it-bnb-4bit",                   "hf_full": "google/gemma-4-26b-it",                     "vram_ft": 22},
    "gemma3:27b":        {"hf_4bit": "unsloth/gemma-3-27b-it-bnb-4bit",                   "hf_full": "google/gemma-3-27b-it",                     "vram_ft": 24},
    "qwen3:32b":         {"hf_4bit": "unsloth/Qwen3-32B-bnb-4bit",                       "hf_full": "Qwen/Qwen3-32B",                            "vram_ft": 28},
    "deepseek-r1:32b":   {"hf_4bit": "unsloth/DeepSeek-R1-Distill-Qwen-32B-bnb-4bit",   "hf_full": "deepseek-ai/DeepSeek-R1-Distill-Qwen-32B",  "vram_ft": 28},
    "qwen2.5-coder:32b": {"hf_4bit": "unsloth/Qwen2.5-Coder-32B-Instruct-bnb-4bit",     "hf_full": "Qwen/Qwen2.5-Coder-32B-Instruct",           "vram_ft": 28},
    "gemma4:31b":        {"hf_4bit": "unsloth/gemma-4-31b-it-bnb-4bit",                   "hf_full": "google/gemma-4-31b-it",                     "vram_ft": 28},
    "command-r:35b":     {"hf_4bit": "CohereForAI/c4ai-command-r-v01",                    "hf_full": "CohereForAI/c4ai-command-r-v01",            "vram_ft": 30},
    "llama3.3:70b":      {"hf_4bit": "unsloth/Llama-3.3-70B-Instruct-bnb-4bit",           "hf_full": "meta-llama/Llama-3.3-70B-Instruct",         "vram_ft": 50},
}

# Models NOT fine-tunable locally
_NOT_FINETUNE = {"nomic-embed-text", "lumena-v1", "qwen3:235b", "deepseek-r1:671b"}


def _build_finetune_catalog() -> list[dict[str, Any]]:
    """Build enriched catalog from OLLAMA_CATALOG + HF mapping."""
    result = []
    for entry in OLLAMA_CATALOG:
        mid = entry["id"]
        hf = _HF_MAP.get(mid)
        ft_ok = mid not in _NOT_FINETUNE and hf is not None
        result.append({
            "ollama_id": mid,
            "params": entry.get("params", "?"),
            "category": entry.get("category", "llm"),
            "desc": entry.get("desc", ""),
            "hf_id_4bit": hf["hf_4bit"] if hf else None,
            "hf_id_full": hf["hf_full"] if hf else None,
            "vram_ft_min_gb": hf["vram_ft"] if hf else 999,
            "finetune_ok": ft_ok,
        })
    return result


FINETUNE_CATALOG: list[dict[str, Any]] = _build_finetune_catalog()


# ---------------------------------------------------------------------------
# GPU Detection — safe (no torch import)
# ---------------------------------------------------------------------------

def detect_gpu_safe() -> dict[str, Any]:
    """Detect GPU without importing torch. Never raises."""
    try:
        # nvidia-smi is preferred on all platforms (accurate VRAM, no uint32 cap)
        try:
            result = _detect_gpu_nvidia_smi()
            if result.get("available"):
                return result
        except Exception:
            pass
        # Fallback: WMI on Windows (VRAM capped at ~4 Go due to uint32)
        if os.name == "nt":
            return _detect_gpu_wmi()
        return {"available": False, "name": None, "vram_gb": 0, "reason": "nvidia-smi not found"}
    except Exception as exc:
        return {"available": False, "name": None, "vram_gb": 0, "reason": str(exc)}


def _detect_gpu_wmi() -> dict[str, Any]:
    """Windows: detect via wmic."""
    r = subprocess.run(
        ["wmic", "path", "win32_VideoController", "get", "Name,AdapterRAM", "/format:csv"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        return {"available": False, "name": None, "vram_gb": 0, "reason": "wmic failed"}

    best_name, best_vram = None, 0
    for line in r.stdout.strip().splitlines():
        parts = line.strip().split(",")
        if len(parts) < 3:
            continue
        try:
            ram = int(parts[1]) if parts[1].strip().isdigit() else 0
        except (ValueError, IndexError):
            ram = 0
        name = parts[2].strip() if len(parts) > 2 else ""
        if ram > best_vram and ("nvidia" in name.lower() or "geforce" in name.lower() or "rtx" in name.lower()):
            best_vram = ram
            best_name = name

    if not best_name:
        return {"available": False, "name": None, "vram_gb": 0, "reason": "No NVIDIA GPU found"}

    vram_gb = round(best_vram / (1024 ** 3), 1)
    return {"available": True, "name": best_name, "vram_gb": vram_gb, "reason": None}


def _detect_gpu_nvidia_smi() -> dict[str, Any]:
    """Linux/macOS: detect via nvidia-smi."""
    r = subprocess.run(
        ["nvidia-smi", "--query-gpu=name,memory.total,memory.free,driver_version", "--format=csv,noheader,nounits"],
        capture_output=True, text=True, timeout=10,
    )
    if r.returncode != 0:
        return {"available": False, "name": None, "vram_gb": 0, "reason": "nvidia-smi not found or failed"}

    lines = [l.strip() for l in r.stdout.strip().splitlines() if l.strip()]
    if not lines:
        return {"available": False, "name": None, "vram_gb": 0, "reason": "No GPU reported by nvidia-smi"}

    parts = lines[0].split(",")
    name = parts[0].strip() if len(parts) > 0 else "Unknown"
    total_mb = float(parts[1].strip()) if len(parts) > 1 else 0
    free_mb = float(parts[2].strip()) if len(parts) > 2 else 0
    driver = parts[3].strip() if len(parts) > 3 else "?"

    return {
        "available": True,
        "name": name,
        "vram_gb": round(total_mb / 1024, 1),
        "vram_free_gb": round(free_mb / 1024, 1),
        "driver_version": driver,
        "reason": None,
    }


# ---------------------------------------------------------------------------
# GPU Detection — full (with torch)
# ---------------------------------------------------------------------------

def detect_gpu() -> dict[str, Any]:
    """Full GPU detection using torch.cuda. Falls back to detect_gpu_safe()."""
    try:
        import torch
    except ImportError:
        return {**detect_gpu_safe(), "torch_available": False}

    if not torch.cuda.is_available():
        base = detect_gpu_safe()
        base["torch_available"] = True
        base["cuda_available"] = False
        return base

    device_count = torch.cuda.device_count()
    name = torch.cuda.get_device_name(0)
    total = torch.cuda.get_device_properties(0).total_mem
    free = total - torch.cuda.memory_allocated(0)

    result: dict[str, Any] = {
        "available": True,
        "name": name,
        "vram_gb": round(total / (1024 ** 3), 1),
        "vram_free_gb": round(free / (1024 ** 3), 1),
        "vram_used_gb": round(torch.cuda.memory_allocated(0) / (1024 ** 3), 1),
        "cuda_version": torch.version.cuda or "?",
        "device_count": device_count,
        "torch_available": True,
        "cuda_available": True,
        "reason": None,
    }

    # ROCm detection
    hip = getattr(torch.version, "hip", None)
    if hip:
        result["rocm_version"] = hip

    # Compute capability
    try:
        cc = torch.cuda.get_device_capability(0)
        result["compute_capability"] = f"{cc[0]}.{cc[1]}"
    except Exception:
        pass

    return result


# ---------------------------------------------------------------------------
# Ollama installed models
# ---------------------------------------------------------------------------

def detect_ollama_installed_models() -> list[str]:
    """Query Ollama for installed model tags."""
    host = os.getenv("LUMENA_OLLAMA_HOST", os.getenv("OLLAMA_HOST", "http://localhost:11434"))
    try:
        r = httpx.get(f"{host}/api/tags", timeout=3)
        if r.status_code == 200:
            data = r.json()
            return [m["name"] for m in data.get("models", [])]
    except Exception:
        pass
    return []


# ---------------------------------------------------------------------------
# Model recommendations
# ---------------------------------------------------------------------------

def recommend_models(vram_free_gb: float, ollama_installed: list[str] | None = None) -> list[dict[str, Any]]:
    """Return models that fit in the available VRAM, sorted by quality."""
    installed_set = set(ollama_installed) if ollama_installed else set()
    # Also match without tag (e.g. "qwen3:8b" matches "qwen3:8b" from installed "qwen3:8b-q4_0")
    installed_base = set()
    for m in installed_set:
        installed_base.add(m.split(":")[0] if ":" in m else m)

    results = []
    for entry in FINETUNE_CATALOG:
        if not entry["finetune_ok"]:
            continue
        if entry["vram_ft_min_gb"] > vram_free_gb:
            continue
        oid = entry["ollama_id"]
        already = oid in installed_set or oid.split(":")[0] in installed_base or oid in installed_base
        results.append({**entry, "already_installed": already})

    # Sort: installed first, then by vram_ft_min desc (bigger = better quality)
    results.sort(key=lambda x: (-x["already_installed"], -x["vram_ft_min_gb"]))
    return results


# ---------------------------------------------------------------------------
# Dependency checking
# ---------------------------------------------------------------------------

_FINETUNING_DEPS = [
    "unsloth", "trl", "peft", "transformers", "datasets",
    "llama_cpp", "bitsandbytes", "accelerate", "huggingface_hub",
]


def check_finetuning_deps() -> dict[str, Any]:
    """Check which fine-tuning packages are installed."""
    installed = []
    missing = []
    for dep in _FINETUNING_DEPS:
        if importlib.util.find_spec(dep):
            installed.append(dep)
        else:
            missing.append(dep)

    torch_ok = importlib.util.find_spec("torch") is not None
    cuda_ok = False
    if torch_ok:
        try:
            import torch
            cuda_ok = torch.cuda.is_available()
        except Exception:
            pass

    return {
        "all_ok": len(missing) == 0 and torch_ok,
        "missing": missing,
        "installed": installed,
        "torch_ok": torch_ok,
        "cuda_ok": cuda_ok,
    }


# ---------------------------------------------------------------------------
# Training time estimation
# ---------------------------------------------------------------------------

def estimate_training_time(vram_gb: float, model_params_b: float, dataset_size: int, epochs: int) -> dict[str, Any]:
    """Empirical estimate of training duration."""
    steps = dataset_size * epochs
    # Rough: bigger model + less VRAM = slower
    time_per_step = max(0.1, model_params_b * 0.3 / max(1, vram_gb / 8))
    total_seconds = steps * time_per_step
    samples_per_sec = 1.0 / time_per_step if time_per_step > 0 else 0
    return {
        "minutes_estimate": round(total_seconds / 60, 1),
        "steps_total": steps,
        "samples_per_second_estimate": round(samples_per_sec, 2),
    }


# ---------------------------------------------------------------------------
# Full status (single call)
# ---------------------------------------------------------------------------

def get_full_status() -> dict[str, Any]:
    """One-call detection: GPU, deps, models, dataset availability."""
    gpu = detect_gpu_safe()
    deps = check_finetuning_deps()
    ollama = detect_ollama_installed_models()
    vram = gpu.get("vram_gb", 0)
    recommended = recommend_models(vram, ollama)

    # Check dataset availability
    from src.utils.paths import TRAINING_POOL_DIR, TRAINING_VALIDATED_DIR
    dataset_count = 0
    for d in [TRAINING_POOL_DIR, TRAINING_VALIDATED_DIR]:
        if d.exists():
            for f in d.glob("*.jsonl"):
                try:
                    dataset_count += sum(1 for line in f.read_text(encoding="utf-8", errors="replace").splitlines() if line.strip())
                except Exception:
                    pass

    return {
        "gpu": gpu,
        "deps": deps,
        "ollama_installed": ollama,
        "recommended_models": recommended,
        "dataset_available": dataset_count >= 50,
        "dataset_count": dataset_count,
        "ready_to_train": gpu.get("available", False) and deps.get("all_ok", False) and dataset_count >= 50,
    }
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
