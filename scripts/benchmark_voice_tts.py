"""Reproducible local TTS benchmark for Voice V3.

The report stores timings, hashes and sizes only. Generated audio is temporary
and no spoken text is written to the report.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import platform
import statistics
import tempfile
import time
import wave
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from src.voice.providers.piper_provider import PiperProvider


CORPUS = (
    "Bonjour, je vérifie cela maintenant.",
    "Le rendez-vous est fixé au vingt-huit septembre à quatorze heures trente.",
    "Attention, l'opération a échoué. Je conserve ton travail et je réessaie.",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _duration_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as stream:
        rate = stream.getframerate()
        return int(stream.getnframes() * 1000 / rate) if rate else 0


async def benchmark_piper(model: str) -> dict:
    provider = PiperProvider(model_name=model)
    model_path, config_path = provider.model_paths(model)
    base = {
        "provider": "piper-tts",
        "provider_version": _package_version("piper-tts"),
        "model": model,
        "available": provider.is_available(model),
        "model_sha256": _sha256(model_path) if model_path.exists() else None,
        "config_sha256": _sha256(config_path) if config_path.exists() else None,
        "model_bytes": model_path.stat().st_size if model_path.exists() else 0,
        "samples": [],
    }
    if not base["available"]:
        return base
    with tempfile.TemporaryDirectory(prefix="lumena_voice_bench_") as raw_dir:
        output_dir = Path(raw_dir)
        for index, text in enumerate(CORPUS):
            output = output_dir / f"sample-{index}.wav"
            started = time.perf_counter()
            ok = await provider.generate(text, output, model_name=model)
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            audio_ms = _duration_ms(output) if ok and output.exists() else 0
            base["samples"].append({
                "corpus_index": index,
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "characters": len(text),
                "synthesis_ms": round(elapsed_ms, 3),
                "audio_ms": audio_ms,
                "real_time_factor": round(elapsed_ms / audio_ms, 4) if audio_ms else None,
                "ok": bool(ok),
            })
    successful = [item for item in base["samples"] if item["ok"]]
    base["summary"] = {
        "successes": len(successful),
        "median_synthesis_ms": round(statistics.median(
            item["synthesis_ms"] for item in successful
        ), 3) if successful else None,
        "median_real_time_factor": round(statistics.median(
            item["real_time_factor"] for item in successful
        ), 4) if successful else None,
    }
    return base


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "not-installed"


async def _run(models: list[str]) -> dict:
    return {
        "schema": "lumena.voice-tts-benchmark.v1",
        "clock": "time.perf_counter",
        "platform": platform.platform(),
        "python": platform.python_version(),
        "corpus_size": len(CORPUS),
        "providers": [await benchmark_piper(model) for model in models],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--models", nargs="+", default=["fr_FR-siwis-low", "fr_FR-siwis-medium"]
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(_run(args.models))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(args.output)
    return 0 if all(item["available"] for item in report["providers"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
