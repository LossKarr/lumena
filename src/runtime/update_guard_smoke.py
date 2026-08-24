"""Small deterministic post-update smoke profile shipped with Lumena."""
from __future__ import annotations

from pathlib import Path
from typing import Any
import argparse
import json

from src.reasoning.final_guards import apply_mission_truth_lock
from src.version import get_build_identity


def run_update_guard_smoke(root: Path, *, expected_version: str, expected_commit: str) -> dict[str, Any]:
    identity = get_build_identity(root)
    git_checkout = (root / ".git").exists()
    checks: dict[str, bool] = {
        "version": identity.version == expected_version,
        "commit": identity.commit == expected_commit,
        "managed_manifest": bool(identity.managed_manifest_sha256) or git_checkout,
    }
    locked, info = apply_mission_truth_lock(
        "Mission terminee: 12 tests verts.", has_green_test=False,
        last_test_outcome=None, has_browser_proof=True,
    )
    checks["mission_truth_lock"] = bool(info.get("changed")) and "non" in locked.lower()
    return {
        "ok": all(checks.values()), "checks": checks,
        "installation_type": "git" if git_checkout else "managed",
        "identity": identity.as_dict(),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    result = run_update_guard_smoke(args.root, expected_version=args.version, expected_commit=args.commit)
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
