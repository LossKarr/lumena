"""Bounded process/API sampler for the real Voice V3 24-hour gate."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
from typing import Any, Callable, Dict
from urllib.request import Request, urlopen


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _api_sample(base_url: str, token: str, timeout_s: float) -> Dict[str, Any]:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    request = Request(base_url.rstrip("/") + "/api/voice/status", headers=headers)
    with urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - explicit local operator URL
        payload = json.loads(response.read(1024 * 1024).decode("utf-8"))
    return {
        "running": bool(payload.get("running")), "backend": payload.get("backend"),
        "state": payload.get("state"), "restarts": payload.get("restarts"),
        "provider": payload.get("provider"),
        "fallback_used": bool(payload.get("fallback_used")),
        "fallback_reason": str(payload.get("fallback_reason") or "")[:96],
    }


def run_endurance(
    *, duration_s: float, interval_s: float, pid: int,
    base_url: str = "", token: str = "", output: str | Path,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Dict[str, Any]:
    import psutil

    duration = max(1.0, float(duration_s))
    interval = max(0.1, float(interval_s))
    process = psutil.Process(int(pid))
    created_at = _now()
    started = clock()
    samples: list[Dict[str, Any]] = []
    failures: list[Dict[str, str]] = []
    while True:
        elapsed = max(0.0, clock() - started)
        try:
            with process.oneshot():
                item: Dict[str, Any] = {
                    "elapsed_s": round(elapsed, 3),
                    "rss_bytes": process.memory_info().rss,
                    "threads": process.num_threads(),
                    "children": len(process.children(recursive=True)),
                }
            if base_url:
                item["voice"] = _api_sample(base_url, token, min(10.0, interval))
            samples.append(item)
        except Exception as exc:
            failures.append({"elapsed_s": f"{elapsed:.3f}", "error_type": type(exc).__name__})
        if elapsed >= duration:
            break
        sleep(min(interval, max(0.0, duration - elapsed)))
    rss = [int(item["rss_bytes"]) for item in samples]
    voice_samples = [item["voice"] for item in samples if "voice" in item]
    actual = max(0.0, clock() - started)
    result = {
        "schema_version": 1, "started_at": created_at,
        "requested_duration_s": duration, "actual_duration_s": round(actual, 3),
        "pid": int(pid), "api_monitored": bool(base_url),
        "samples": samples, "failures": failures,
        "summary": {
            "sample_count": len(samples), "failure_count": len(failures),
            "rss_start_bytes": rss[0] if rss else None,
            "rss_end_bytes": rss[-1] if rss else None,
            "rss_growth_bytes": (rss[-1] - rss[0]) if len(rss) >= 2 else None,
            "voice_running_ratio": (
                sum(1 for item in voice_samples if item.get("running")) / len(voice_samples)
                if voice_samples else None
            ),
        },
        "certifying_24h": actual >= 86400 and not failures and (
            not voice_samples or all(
                item.get("running")
                and item.get("backend") == "v2"
                and not item.get("fallback_used")
                and int(item.get("restarts") or 0) == 0
                for item in voice_samples
            )
        ),
    }
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, target)
    return result

