"""GGUF export & quantization for Ollama import."""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

_log = logging.getLogger(__name__)

SUPPORTED_QUANT_TYPES = frozenset({"Q4_K_M", "Q5_K_M", "Q8_0", "Q2_K"})


# ---------------------------------------------------------------------------
# HF → GGUF F16
# ---------------------------------------------------------------------------

def convert_to_gguf(merged_model_dir: str, gguf_f16_path: str) -> str:
    """Convert a merged HF model to GGUF F16.

    Primary: Unsloth save_pretrained_gguf.
    Fallback: llama.cpp convert_hf_to_gguf.py subprocess.
    """
    merged = Path(merged_model_dir)
    if not merged.exists():
        raise ValueError(f"Merged model directory does not exist: {merged_model_dir}")

    out = Path(gguf_f16_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Try Unsloth native export
    try:
        from unsloth import FastLanguageModel
        model, tokenizer = FastLanguageModel.from_pretrained(
            model_name=str(merged), max_seq_length=2048,
        )
        model.save_pretrained_gguf(str(out.parent), tokenizer, quantization_method="f16")
        # Unsloth names the file; find it
        candidates = list(out.parent.glob("*f16*.gguf")) + list(out.parent.glob("*F16*.gguf"))
        if candidates:
            best = candidates[0]
            if best != out:
                best.rename(out)
            _log.info("GGUF F16 created via Unsloth: %s", out)
            return str(out)
    except ImportError:
        _log.info("Unsloth not available for GGUF export, trying fallback")
    except Exception as exc:
        _log.warning("Unsloth GGUF export failed: %s, trying fallback", exc)

    # Fallback: llama.cpp convert script
    script = _find_convert_script()
    if not script:
        raise RuntimeError(
            "Cannot convert to GGUF: neither Unsloth nor convert_hf_to_gguf.py available"
        )

    r = subprocess.run(
        ["python", str(script), str(merged), "--outtype", "f16", "--outfile", str(out)],
        capture_output=True, text=True, timeout=600,
    )
    if r.returncode != 0:
        raise RuntimeError(f"GGUF conversion failed: {r.stderr[:500]}")

    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(f"GGUF file not created or empty: {out}")

    _log.info("GGUF F16 created via subprocess: %s", out)
    return str(out)


def _find_convert_script() -> Path | None:
    """Find convert_hf_to_gguf.py in known locations."""
    from src.utils.paths import ROOT_DIR
    candidates = [
        ROOT_DIR / "models" / "scripts" / "convert_hf_to_gguf.py",
        Path.home() / "llama.cpp" / "convert_hf_to_gguf.py",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


# ---------------------------------------------------------------------------
# GGUF Quantization
# ---------------------------------------------------------------------------

def quantize_gguf(f16_path: str, q_path: str, quant_type: str = "Q4_K_M") -> str:
    """Quantize a GGUF F16 file to a smaller format."""
    if quant_type not in SUPPORTED_QUANT_TYPES:
        raise ValueError(f"Unsupported quant type: {quant_type}. Supported: {SUPPORTED_QUANT_TYPES}")

    f16 = Path(f16_path)
    if not f16.exists():
        raise ValueError(f"F16 GGUF file does not exist: {f16_path}")

    out = Path(q_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    # Try llama_cpp Python binding
    try:
        import llama_cpp
        llama_cpp.llama_model_quantize(str(f16), str(out), ftype=_quant_ftype(quant_type))
        if out.exists() and out.stat().st_size > 0:
            _log.info("Quantized GGUF (%s) created: %s", quant_type, out)
            return str(out)
    except (ImportError, AttributeError):
        _log.info("llama_cpp quantize not available, trying subprocess")
    except Exception as exc:
        _log.warning("llama_cpp quantize failed: %s", exc)

    # Fallback: llama-quantize subprocess
    r = subprocess.run(
        ["llama-quantize", str(f16), str(out), quant_type],
        capture_output=True, text=True, timeout=600,
    )
    if r.returncode != 0:
        raise RuntimeError(f"Quantization failed: {r.stderr[:500]}")

    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError(f"Quantized GGUF not created: {out}")

    _log.info("Quantized GGUF (%s) created via subprocess: %s", quant_type, out)
    return str(out)


def _quant_ftype(quant_type: str) -> int:
    """Map quant type name to llama.cpp numeric ftype."""
    mapping = {"Q4_K_M": 15, "Q5_K_M": 17, "Q8_0": 7, "Q2_K": 10}
    return mapping.get(quant_type, 15)


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def get_gguf_size_gb(path: str) -> float:
    """Return file size in GB."""
    p = Path(path)
    if not p.exists():
        return 0.0
    return round(p.stat().st_size / (1024 ** 3), 2)


def cleanup_intermediate(paths: list[str]) -> None:
    """Delete intermediate files (e.g. F16 after quantization)."""
    for p in paths:
        try:
            fp = Path(p)
            if fp.exists():
                fp.unlink()
                _log.info("Cleaned up: %s", p)
        except Exception as exc:
            _log.warning("Failed to clean up %s: %s", p, exc)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
