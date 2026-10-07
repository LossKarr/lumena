"""Monitor a real Lumena process for the Voice V3 endurance gate."""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.voice.v2.endurance import run_endurance  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Soak réel Voice V3")
    parser.add_argument("--pid", type=int, required=True, help="PID du processus Lumena installé")
    parser.add_argument("--duration-hours", type=float, default=24.0)
    parser.add_argument("--interval-s", type=float, default=30.0)
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--output", default="data/logs/voice-v3-endurance.json")
    args = parser.parse_args()
    result = run_endurance(
        duration_s=args.duration_hours * 3600, interval_s=args.interval_s,
        pid=args.pid, base_url=args.base_url,
        token=os.getenv("LUMENA_ADMIN_TOKEN", ""), output=args.output,
    )
    print(f"Rapport: {args.output}")
    print("Gate 24 h: PASS" if result["certifying_24h"] else "Gate 24 h: NON CERTIFIÉE")
    return 0 if result["certifying_24h"] else 2


if __name__ == "__main__":
    raise SystemExit(main())

