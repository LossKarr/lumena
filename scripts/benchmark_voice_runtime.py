"""Offline Voice startup benchmark; writes timings and hardware facts, never audio/text."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="data/logs/voice-runtime-benchmark-v3.json")
    parser.add_argument("--model", default=os.getenv("LUMENA_STT_MODEL", "small"))
    args = parser.parse_args()
    os.environ.setdefault("HF_HUB_OFFLINE", "1")

    from src.voice.v2.prewarm import detect_voice_hardware
    from src.voice.v2.supervisor import resolve_voice_runtime_options
    from src.voice.stt import LumenaSTT

    options = resolve_voice_runtime_options()
    engine = LumenaSTT(
        model_size=args.model, device=options["device"],
        compute_type=options["compute"], language=options["language"],
    )
    timings = []
    for _ in range(2):
        started = time.perf_counter()
        ok = bool(engine.load_model())
        timings.append(round((time.perf_counter() - started) * 1000, 3))
        if not ok:
            break
    payload = {
        "schema_version": 1,
        "hardware": detect_voice_hardware(),
        "effective_stt": {
            "model": args.model, "device": engine.device,
            "compute_type": engine.compute_type,
            "fallback_used": engine.runtime_fallback_used,
        },
        "stt_load_ms": timings,
        "success": len(timings) == 2 and engine.model is not None,
        "privacy": {"audio": False, "transcript": False, "prompt": False},
    }
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if payload["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())

