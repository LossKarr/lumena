"""
CLI helper to sync skills-main into Lumena skills.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).parent.parent
    sys.path.insert(0, str(root))

    from src.skills.sync import sync_skills_main

    parser = argparse.ArgumentParser(description="Sync skills-main into lumena/skills")
    parser.add_argument("--source", type=str, default="", help="Optional source skills directory")
    parser.add_argument("--dest", type=str, default="", help="Optional destination skills directory")
    parser.add_argument("--manifest", type=str, default="", help="Optional manifest output path")
    args = parser.parse_args()

    source = Path(args.source).resolve() if args.source else None
    dest = Path(args.dest).resolve() if args.dest else None
    manifest = Path(args.manifest).resolve() if args.manifest else None

    result = sync_skills_main(source_path=source, destination_path=dest, manifest_path=manifest)
    print(json.dumps(result, indent=2))
    return 0 if result.get("errors_count", 0) == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
