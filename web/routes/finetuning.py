"""Fine-tuning API routes — GPU detection, job management, SSE progress."""
from __future__ import annotations

import asyncio
import json
import logging
import re
import sys
import threading
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.responses import StreamingResponse

from web.routes import deps

_log = logging.getLogger(__name__)

router = APIRouter(tags=["finetuning"])

# ── Validation constants ──────────────────────────────────────────────────
_MODEL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")
_VALID_QUANT_TYPES = {"Q4_K_M", "Q5_K_M", "Q8_0", "Q2_K"}
_VALID_LORA_R = {8, 16, 32, 64}

# ── Active job state ──────────────────────────────────────────────────────
_active_job: dict[str, Any] | None = None
_active_lock = threading.Lock()
_progress_queue: list[dict[str, Any]] = []
_progress_lock = threading.Lock()


def _push_progress(data: dict) -> None:
    with _progress_lock:
        _progress_queue.append(data)
    # Persist job state for crash resilience
    _persist_job_state()


def _persist_job_state() -> None:
    """Write active job + last progress to data/ops/finetuning_job.json."""
    try:
        from src.utils.persistence import atomic_write_json
        from src.utils.paths import OPS_DIR
        state = {}
        with _active_lock:
            state["active_job"] = _active_job
        with _progress_lock:
            state["last_progress"] = _progress_queue[-1] if _progress_queue else None
        atomic_write_json(OPS_DIR / "finetuning_job.json", state)
    except Exception:
        pass  # best-effort, never block training


# ── Request models ────────────────────────────────────────────────────────

class StartRequest(BaseModel):
    base_model: str
    ollama_tag: str
    output_name: str
    num_epochs: int = Field(default=3, ge=1, le=10)
    learning_rate: float = Field(default=2e-4, ge=1e-6, le=1e-2)
    lora_r: int = 16
    lora_alpha: int = Field(default=32, ge=1, le=128)
    lora_dropout: float = Field(default=0.0, ge=0.0, le=0.5)
    batch_size: int = Field(default=2, ge=1, le=8)
    grad_accumulation: int = Field(default=4, ge=1, le=32)
    max_seq_length: int = Field(default=2048, ge=128, le=8192)
    load_in_4bit: bool = True
    use_unsloth: bool = True
    quant_type: str = "Q4_K_M"
    system_prompt: str = ""
    hf_token: str = ""


# ── Endpoints ─────────────────────────────────────────────────────────────

@router.get("/api/finetuning/status")
async def finetuning_status(_auth=Depends(deps.verify_admin_token)):
    """Global fine-tuning status: GPU, deps, active job."""
    from src.training.gpu_detect import get_full_status
    status = get_full_status()
    with _active_lock:
        status["active_job"] = _active_job
    return status


@router.get("/api/finetuning/models")
async def finetuning_models(_auth=Depends(deps.verify_admin_token)):
    """All fine-tunable models with VRAM fit info + auto-detected Ollama models."""
    from src.training.gpu_detect import detect_gpu_safe, detect_ollama_installed_models, FINETUNE_CATALOG
    gpu = detect_gpu_safe()
    vram = gpu.get("vram_gb", 0)
    ollama = detect_ollama_installed_models()

    # Build flexible installed lookup: exact name + base (without quant suffix)
    installed_set: set[str] = set(ollama)
    installed_bases: set[str] = set()
    for m in ollama:
        installed_bases.add(m.split(":")[0])

    def _is_installed(oid: str) -> bool:
        if oid in installed_set:
            return True
        base = oid.split(":")[0]
        if base in installed_bases:
            return True
        # Match e.g. "qwen3:8b" against "qwen3:8b-q4_0"
        for inst in installed_set:
            if inst.startswith(oid):
                return True
        return False

    models = []
    catalog_bases: set[str] = set()
    for entry in FINETUNE_CATALOG:
        if not entry["finetune_ok"]:
            continue
        oid = entry["ollama_id"]
        catalog_bases.add(oid)
        catalog_bases.add(oid.split(":")[0])
        already = _is_installed(oid)
        fits = entry["vram_ft_min_gb"] <= vram if vram > 0 else False
        models.append({**entry, "already_installed": already, "fits_vram": fits})

    # Auto-detect: add installed Ollama models NOT in catalog
    for name in ollama:
        base = name.split(":")[0]
        if name in catalog_bases or base in catalog_bases:
            continue
        # Infer category from name
        cat = "llm"
        lower = name.lower()
        if any(v in lower for v in ("llava", "bakllava", "moondream", "minicpm-v", "vision")):
            cat = "vision"
        elif any(c in lower for c in ("coder", "codestral", "starcoder", "deepseek-coder")):
            cat = "code"
        models.append({
            "ollama_id": name,
            "params": "?",
            "category": cat,
            "desc": "Modèle Ollama détecté (installé localement)",
            "hf_id_4bit": None,
            "hf_id_full": None,
            "vram_ft_min_gb": 0,
            "finetune_ok": True,
            "already_installed": True,
            "fits_vram": True,
            "auto_detected": True,
        })

    # Sort: installed first, then fits, then by vram ascending
    models.sort(key=lambda x: (-x["already_installed"], -x["fits_vram"], x["vram_ft_min_gb"]))
    return {"models": models, "gpu": gpu, "ollama_installed": ollama}


@router.get("/api/finetuning/dataset-stats")
async def finetuning_dataset_stats(_auth=Depends(deps.verify_admin_token)):
    """Stats on available training data."""
    from src.training.data_prep import get_dataset_stats, load_lumena_pool
    from src.utils.paths import TRAINING_POOL_DIR, TRAINING_VALIDATED_DIR
    try:
        raw = load_lumena_pool(TRAINING_POOL_DIR, TRAINING_VALIDATED_DIR, min_conversations=0)
        stats = get_dataset_stats(raw)
        return {"success": True, **stats}
    except Exception as exc:
        return {"success": False, "error": str(exc), "total": 0}


@router.post("/api/finetuning/start")
async def finetuning_start(req: StartRequest, _auth=Depends(deps.verify_admin_token)):
    """Start a fine-tuning job in the background."""
    global _active_job

    # Validate output_name
    if not _MODEL_NAME_RE.match(req.output_name):
        raise HTTPException(422, "output_name invalide (format: a-z0-9._:- max 64)")

    # Validate quant_type
    if req.quant_type not in _VALID_QUANT_TYPES:
        raise HTTPException(422, f"quant_type invalide. Valides: {_VALID_QUANT_TYPES}")

    # Validate lora_r
    if req.lora_r not in _VALID_LORA_R:
        raise HTTPException(422, f"lora_r invalide. Valides: {_VALID_LORA_R}")

    # Validate base_model against catalog
    from src.training.gpu_detect import FINETUNE_CATALOG
    known = {e["hf_id_4bit"] for e in FINETUNE_CATALOG if e.get("hf_id_4bit")}
    known |= {e["hf_id_full"] for e in FINETUNE_CATALOG if e.get("hf_id_full")}
    if req.base_model not in known:
        raise HTTPException(422, f"base_model inconnu dans le catalogue: {req.base_model}")

    with _active_lock:
        if _active_job is not None:
            raise HTTPException(409, "Un job de fine-tuning est déjà en cours")
        _active_job = {
            "output_name": req.output_name,
            "base_model": req.base_model,
            "status": "started",
            "started_at": time.time(),
        }

    # Clear progress
    with _progress_lock:
        _progress_queue.clear()

    # Launch in background thread
    asyncio.get_running_loop().run_in_executor(None, _run_job, req)

    return {"job_id": req.output_name, "status": "started"}


@router.post("/api/finetuning/cancel")
async def finetuning_cancel(_auth=Depends(deps.verify_admin_token)):
    """Cancel the active fine-tuning job."""
    with _active_lock:
        if _active_job is None:
            return {"cancelled": False, "reason": "Aucun job actif"}

    from src.training.pipeline import cancel_training
    cancel_training()
    _push_progress({"phase": "error", "message": "Annulé par l'utilisateur"})
    return {"cancelled": True}


@router.get("/api/finetuning/progress")
async def finetuning_progress(_auth=Depends(deps.verify_admin_token)):
    """SSE stream of fine-tuning progress."""
    async def stream():
        idx = 0
        while True:
            with _progress_lock:
                new = _progress_queue[idx:]
                idx = len(_progress_queue)
            for item in new:
                yield f"data: {json.dumps(item, ensure_ascii=False)}\n\n"
                if item.get("phase") in ("done", "error"):
                    return
            await asyncio.sleep(2)

    return StreamingResponse(stream(), media_type="text/event-stream")


@router.get("/api/finetuning/jobs")
async def finetuning_jobs(_auth=Depends(deps.verify_admin_token)):
    """List all fine-tuned models."""
    from src.training.ollama_import import list_finetuned_models
    return {"models": list_finetuned_models()}


@router.delete("/api/finetuning/jobs/{model_name}")
async def finetuning_delete(model_name: str, _auth=Depends(deps.verify_admin_token)):
    """Delete a fine-tuned model."""
    if not _MODEL_NAME_RE.match(model_name):
        raise HTTPException(422, "Nom de modèle invalide")

    from src.training.ollama_import import delete_finetuned_model
    deleted = delete_finetuned_model(model_name)
    if not deleted:
        raise HTTPException(404, f"Modèle '{model_name}' non trouvé dans le registre")
    return {"deleted": True, "model_name": model_name}


@router.post("/api/finetuning/install-deps")
async def finetuning_install_deps(_auth=Depends(deps.verify_admin_token)):
    """Install fine-tuning dependencies via pip + SSE streaming."""
    async def stream():
        yield f"data: {json.dumps({'phase': 'installing', 'message': 'Installation des dépendances fine-tuning...'})}\n\n"

        from src.utils.paths import ROOT_DIR
        req_lock = ROOT_DIR / "requirements-finetuning-lock.txt"
        req_file = req_lock if req_lock.exists() else ROOT_DIR / "requirements-finetuning.txt"
        if not req_file.exists():
            yield f"data: {json.dumps({'phase': 'error', 'message': 'requirements-finetuning.txt non trouvé'})}\n\n"
            return

        import subprocess
        proc = subprocess.Popen(
            [sys.executable, "-m", "pip", "install", "-r", str(req_file)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        for line in iter(proc.stdout.readline, ""):
            line = line.strip()
            if line:
                yield f"data: {json.dumps({'phase': 'installing', 'message': line})}\n\n"
                await asyncio.sleep(0)  # yield control

        proc.wait()
        if proc.returncode == 0:
            yield f"data: {json.dumps({'phase': 'installing', 'message': 'Dépendances de base OK. Installation de llama-cpp-python (wheels CUDA)...'})}\n\n"
            await asyncio.sleep(0)

            # llama-cpp-python : installation séparée avec wheels CUDA précompilés
            import os as _os
            _env = _os.environ.copy()
            _env["CMAKE_ARGS"] = "-DGGML_CUDA=on"
            proc2 = subprocess.Popen(
                [sys.executable, "-m", "pip", "install", "llama-cpp-python>=0.3.0",
                 "--extra-index-url", "https://abetlen.github.io/llama-cpp-python/whl/cu121",
                 "--prefer-binary"],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                env=_env,
            )
            for line2 in iter(proc2.stdout.readline, ""):
                line2 = line2.strip()
                if line2:
                    yield f"data: {json.dumps({'phase': 'installing', 'message': line2})}\n\n"
                    await asyncio.sleep(0)
            proc2.wait()

            if proc2.returncode == 0:
                yield f"data: {json.dumps({'phase': 'done', 'message': 'Installation terminée avec succès (toutes les dépendances + llama-cpp-python)'})}\n\n"
            else:
                yield f"data: {json.dumps({'phase': 'error', 'message': f'llama-cpp-python a échoué (code {proc2.returncode}). Les autres dépendances sont OK.'})}\n\n"
        else:
            yield f"data: {json.dumps({'phase': 'error', 'message': f'Échec pip (code {proc.returncode})'})}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


# ── Background job runner ─────────────────────────────────────────────────

def _run_job(req: StartRequest) -> None:
    """Run the full fine-tuning pipeline in a background thread."""
    global _active_job
    try:
        from src.training.pipeline import FinetuneConfig, ProgressCallback, run_finetuning, merge_and_save
        from src.training.data_prep import load_lumena_pool, convert_to_trl_format, split_dataset
        from src.training.export_gguf import convert_to_gguf, quantize_gguf, get_gguf_size_gb, cleanup_intermediate
        from src.training.ollama_import import write_modelfile, ollama_create, register_in_lumena
        from src.utils.paths import TRAINING_POOL_DIR, TRAINING_VALIDATED_DIR, FINETUNED_MODELS_DIR

        cb = ProgressCallback(on_progress=_push_progress)

        # Phase: preparing data
        _push_progress({"phase": "preparing", "message": "Chargement et préparation des données..."})
        raw = load_lumena_pool(TRAINING_POOL_DIR, TRAINING_VALIDATED_DIR)
        trl_data = convert_to_trl_format(raw, system_prompt=req.system_prompt)
        ds_train, ds_eval = split_dataset(trl_data)
        _push_progress({"phase": "preparing", "message": f"{len(trl_data)} conversations prêtes"})

        # Phase: training
        config = FinetuneConfig(
            base_model_hf_id=req.base_model,
            output_name=req.output_name,
            lora_r=req.lora_r,
            lora_alpha=req.lora_alpha,
            lora_dropout=req.lora_dropout,
            learning_rate=req.learning_rate,
            num_epochs=req.num_epochs,
            batch_size=req.batch_size,
            grad_accumulation=req.grad_accumulation,
            max_seq_length=req.max_seq_length,
            load_in_4bit=req.load_in_4bit,
            use_unsloth=req.use_unsloth,
            system_prompt=req.system_prompt,
            hf_token=req.hf_token,
        )

        lora_path = run_finetuning(config, ds_train, ds_eval, progress_cb=cb)

        # Phase: merging
        _push_progress({"phase": "merging", "message": "Fusion LoRA → modèle complet..."})
        merged_dir = str(FINETUNED_MODELS_DIR / f"{req.output_name}_merged")
        merge_and_save(lora_path, merged_dir)

        # Phase: converting
        _push_progress({"phase": "converting", "message": "Conversion HF → GGUF F16..."})
        gguf_f16 = str(FINETUNED_MODELS_DIR / f"{req.output_name}-f16.gguf")
        convert_to_gguf(merged_dir, gguf_f16)

        # Phase: quantizing
        _push_progress({"phase": "quantizing", "message": f"Quantization {req.quant_type}..."})
        gguf_final = str(FINETUNED_MODELS_DIR / f"{req.output_name}-{req.quant_type}.gguf")
        quantize_gguf(gguf_f16, gguf_final, quant_type=req.quant_type)

        # Cleanup F16
        cleanup_intermediate([gguf_f16])

        # Phase: importing into Ollama
        _push_progress({"phase": "importing", "message": "Import dans Ollama..."})
        mf_path = str(FINETUNED_MODELS_DIR / f"{req.output_name}.Modelfile")
        write_modelfile(gguf_final, req.output_name, req.system_prompt, mf_path)
        success = ollama_create(req.output_name, mf_path)

        if success:
            register_in_lumena(
                model_name=req.output_name,
                base_model_tag=req.ollama_tag,
                trained_on=f"lumena-pool ({len(trl_data)} convs)",
                quant_type=req.quant_type,
                gguf_path=gguf_final,
                dataset_size=len(trl_data),
                epochs=req.num_epochs,
            )
            size_gb = get_gguf_size_gb(gguf_final)
            _push_progress({
                "phase": "done",
                "message": f"Fine-tuning terminé ! Modèle '{req.output_name}' ({size_gb} Go) prêt.",
                "pct_done": 100,
            })
        else:
            _push_progress({"phase": "error", "message": "Ollama create a échoué"})

    except Exception as exc:
        _log.exception("Fine-tuning job failed")
        _push_progress({"phase": "error", "message": str(exc)})
    finally:
        with _active_lock:
            _active_job = None
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
