"""Durable state machine for personal-model training jobs."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from threading import RLock
from typing import Any

from filelock import FileLock

from src.utils.persistence import atomic_write_json, safe_read_json

from .contracts import RUN_TRANSITIONS, TrainingRunState, TrainingRunV1, utc_now, validate_transition


class TrainingJobStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.index_path = self.root / "runs" / "index.json"
        self._thread_lock = RLock()
        self._file_lock = FileLock(str(self.index_path) + ".lock", timeout=10)

    @staticmethod
    def _default() -> dict[str, Any]:
        return {"schema_version": 1, "runs": {}, "updated_at": utc_now()}

    def _load(self) -> dict[str, Any]:
        data = safe_read_json(self.index_path, default={})
        return data if isinstance(data, dict) and data.get("schema_version") == 1 and isinstance(data.get("runs"), dict) else self._default()

    def _write(self, data: dict[str, Any]) -> None:
        data["updated_at"] = utc_now()
        atomic_write_json(self.index_path, data)

    def create(self, run: TrainingRunV1) -> TrainingRunV1:
        with self._thread_lock, self._file_lock:
            data = self._load()
            if run.run_id in data["runs"]:
                existing = TrainingRunV1.from_dict(data["runs"][run.run_id])
                if existing == run:
                    return existing
                raise ValueError("training_run_id_collision")
            data["runs"][run.run_id] = run.to_dict()
            self._write(data)
            atomic_write_json(self.root / "runs" / run.run_id / "run.json", run.to_dict())
            return run

    def get(self, run_id: str) -> TrainingRunV1 | None:
        with self._thread_lock, self._file_lock:
            item = self._load()["runs"].get(run_id)
            return TrainingRunV1.from_dict(item) if isinstance(item, dict) else None

    def list(self) -> list[TrainingRunV1]:
        with self._thread_lock, self._file_lock:
            values = list(self._load()["runs"].values())
        return sorted((TrainingRunV1.from_dict(item) for item in values), key=lambda run: run.updated_at, reverse=True)

    def transition(
        self,
        run_id: str,
        target: TrainingRunState,
        *,
        error_code: str = "",
        checkpoint_path: str | None = None,
        artifact_hashes: dict[str, str] | None = None,
    ) -> TrainingRunV1:
        with self._thread_lock, self._file_lock:
            data = self._load()
            item = data["runs"].get(run_id)
            if not isinstance(item, dict):
                raise KeyError("training_run_not_found")
            current = TrainingRunV1.from_dict(item)
            validate_transition(current.state, target, RUN_TRANSITIONS)
            updated = replace(
                current,
                state=target,
                updated_at=utc_now(),
                error_code=str(error_code or "")[:128],
                checkpoint_path=current.checkpoint_path if checkpoint_path is None else str(checkpoint_path),
                artifact_hashes=current.artifact_hashes if artifact_hashes is None else dict(artifact_hashes),
            )
            data["runs"][run_id] = updated.to_dict()
            self._write(data)
            atomic_write_json(self.root / "runs" / run_id / "run.json", updated.to_dict())
            return updated

    def reconcile_after_restart(self) -> list[str]:
        interrupted = []
        for run in self.list():
            if run.state in {TrainingRunState.RUNNING, TrainingRunState.RESUMING, TrainingRunState.PAUSING, TrainingRunState.CANCELLING}:
                self.transition(run.run_id, TrainingRunState.UNKNOWN_INTERRUPTED, error_code="process_restart")
                interrupted.append(run.run_id)
        return interrupted
