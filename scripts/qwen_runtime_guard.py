"""CLI guard for Lumena's separate, offline Qwen3-TTS component runtime."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.voice.v2.qwen_runtime import probe_qwen_runtime


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=None)
    parser.add_argument("--deep", action="store_true")
    parser.add_argument("--timeout", type=float, default=30.0)
    args = parser.parse_args()
    report = probe_qwen_runtime(root=args.root, timeout_s=args.timeout, deep=args.deep)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if report.get("ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
