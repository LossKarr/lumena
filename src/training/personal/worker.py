"""Subprocess entry point for one personal-model training run."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .contracts import TrainingRunState
from .job_store import TrainingJobStore
from .lifecycle import PersonalModelLifecycle
from .sft_backend import execute_sft_job


def _load_json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("worker_config_invalid")
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    run_dir = root / "runs" / args.run_id
    jobs = TrainingJobStore(root)
    try:
        config = _load_json(run_dir / "worker_config.json")
        def control_signal() -> str | None:
            control_path = run_dir / "control.json"
            if not control_path.is_file():
                return None
            try:
                action = _load_json(control_path).get("action")
            except Exception:
                return "cancel"
            return action if action in {"pause", "cancel"} else None

        result = execute_sft_job(run_dir, config, control_signal=control_signal)
        if result.state == "pause":
            jobs.transition(args.run_id, TrainingRunState.PAUSED, checkpoint_path=result.checkpoint_path)
            return 0
        if result.state == "cancel":
            jobs.transition(args.run_id, TrainingRunState.CANCELLED, checkpoint_path=result.checkpoint_path)
            return 0
        PersonalModelLifecycle(root).finalize_training(args.run_id, adapter_path=Path(result.adapter_path))
        return 0
    except Exception as exc:
        current = jobs.get(args.run_id)
        if current and current.state in {TrainingRunState.RUNNING, TrainingRunState.RESUMING}:
            jobs.transition(args.run_id, TrainingRunState.FAILED, error_code=str(exc)[:128])
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
