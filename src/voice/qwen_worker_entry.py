"""Isolated JSONL worker for Qwen3-TTS.

This module runs under the dedicated Qwen Python environment. It deliberately
imports no Lumena server modules and never opens a network socket.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
import traceback


def _emit(value: dict) -> None:
    sys.stdout.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n")
    sys.stdout.flush()


def _language(value: object) -> str:
    code = str(value or "fr").strip().lower().split("-")[0]
    return {
        "fr": "French", "en": "English", "es": "Spanish", "de": "German",
        "it": "Italian", "pt": "Portuguese", "ru": "Russian", "zh": "Chinese",
        "ja": "Japanese", "ko": "Korean",
    }.get(code, "Auto")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--device", default="auto")
    parser.add_argument("--dtype", default="auto")
    args = parser.parse_args()
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    os.environ["HF_DATASETS_OFFLINE"] = "1"
    if not args.model.is_dir():
        _emit({"event": "fatal", "error": "model_missing"})
        return 2
    try:
        import numpy as np
        import soundfile as sf
        import torch
        from qwen_tts import Qwen3TTSModel

        cuda = bool(torch.cuda.is_available())
        device = "cuda:0" if args.device == "auto" and cuda else args.device
        if device == "auto":
            device = "cpu"
        dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
        if args.dtype == "float32":
            dtype = torch.float32
        elif args.dtype == "bfloat16":
            dtype = torch.bfloat16
        attention = "sdpa" if device.startswith("cuda") else "eager"
        started = time.perf_counter()
        model = Qwen3TTSModel.from_pretrained(
            str(args.model), device_map=device, dtype=dtype,
            attn_implementation=attention, local_files_only=True,
        )
        if device.startswith("cuda"):
            torch.cuda.synchronize()
        _emit({
            "event": "ready", "device": device, "cuda": cuda,
            "load_ms": int((time.perf_counter() - started) * 1000),
        })
    except BaseException:
        traceback.print_exc(file=sys.stderr)
        _emit({"event": "fatal", "error": "model_load_failed"})
        return 3

    prompt_cache: dict[tuple[str, int, int, str], object] = {}
    for raw in sys.stdin:
        try:
            request = json.loads(raw)
        except json.JSONDecodeError:
            _emit({"id": "", "ok": False, "error": "invalid_json"})
            continue
        request_id = str(request.get("id", ""))
        action = request.get("action")
        if action == "shutdown":
            _emit({"id": request_id, "ok": True, "event": "shutdown"})
            return 0
        if action == "health":
            _emit({"id": request_id, "ok": True, "event": "health", "device": device})
            continue
        if action != "synthesize":
            _emit({"id": request_id, "ok": False, "error": "unknown_action"})
            continue
        try:
            text = str(request.get("text", "")).strip()
            reference = Path(str(request.get("reference_path", ""))).resolve()
            reference_text = str(request.get("reference_text", "")).strip()
            output = Path(str(request.get("output_path", ""))).resolve()
            if not text or len(text) > 800 or not any(ch.isalnum() for ch in text):
                raise ValueError("invalid_text")
            if not reference.is_file() or reference.suffix.lower() != ".wav":
                raise ValueError("invalid_reference")
            if not reference_text or len(reference_text) > 10_000:
                raise ValueError("invalid_reference_text")
            if output.suffix.lower() != ".wav" or not output.parent.is_dir():
                raise ValueError("invalid_output")
            stat = reference.stat()
            prompt_key = (str(reference), int(stat.st_size), int(stat.st_mtime_ns), reference_text)
            prompt = prompt_cache.get(prompt_key)
            if prompt is None:
                prompt = model.create_voice_clone_prompt(
                    ref_audio=str(reference), ref_text=reference_text,
                    x_vector_only_mode=False,
                )
                prompt_cache.clear()
                prompt_cache[prompt_key] = prompt
            start = time.perf_counter()
            wavs, sample_rate = model.generate_voice_clone(
                text=text, language=_language(request.get("language")),
                voice_clone_prompt=prompt, non_streaming_mode=True,
                max_new_tokens=min(2048, max(128, int(request.get("max_new_tokens", 768)))),
            )
            if device.startswith("cuda"):
                torch.cuda.synchronize()
            waveform = np.asarray(wavs[0], dtype=np.float32)
            duration_s = len(waveform) / int(sample_rate)
            max_duration_s = min(60.0, max(6.0, len(text) * 0.22 + 2.0))
            if (
                waveform.ndim != 1 or not len(waveform)
                or not np.isfinite(waveform).all() or float(np.max(np.abs(waveform))) <= 0
                or duration_s > max_duration_s
            ):
                raise ValueError("anomalous_audio")
            temporary = output.with_suffix(".tmp.wav")
            sf.write(str(temporary), waveform, int(sample_rate), subtype="PCM_16")
            os.replace(temporary, output)
            _emit({
                "id": request_id, "ok": True, "path": str(output),
                "sample_rate": int(sample_rate), "channels": 1,
                "duration_ms": int(duration_s * 1000),
                "elapsed_ms": int((time.perf_counter() - start) * 1000),
                "device": device,
            })
        except BaseException as exc:
            code = str(exc) if isinstance(exc, ValueError) else "synthesis_failed"
            traceback.print_exc(file=sys.stderr)
            _emit({"id": request_id, "ok": False, "error": code[:64]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
