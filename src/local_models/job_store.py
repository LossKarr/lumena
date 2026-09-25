"""Durable local-model jobs with atomic updates and restart reconciliation."""

from __future__ import annotations

from pathlib import Path
from threading import RLock
from typing import Any

from filelock import FileLock

from src.utils.persistence import atomic_write_json, safe_read_json
from src.utils.paths import DATA_DIR

from .contracts import JobState, LocalModelJob, LocalModelSource, ModelReference, utc_now


class LocalModelJobStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DATA_DIR / "local_models" / "jobs.json"
        self._lock = RLock()
        self._file_lock = FileLock(str(self.path) + ".lock", timeout=5)

    def _load_raw(self) -> dict[str, Any]:
        data = safe_read_json(self.path, default={})
        return (
            data
            if type(data) is dict and data.get("version") == 1 and type(data.get("jobs")) is dict
            else {"version": 1, "jobs": {}}
        )

    def _write(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(self.path, data)

    @staticmethod
    def _restore(data: dict[str, Any]) -> LocalModelJob:
        ref_data = data["reference"]
        reference = ModelReference(
            source=LocalModelSource(ref_data["source"]),
            canonical=ref_data["canonical"],
            pull_reference=ref_data["pull_reference"],
            repository=ref_data.get("repository", ""),
            quantization=ref_data.get("quantization", ""),
        )
        return LocalModelJob(
            job_id=data["job_id"],
            operation=data["operation"],
            reference=reference,
            state=JobState(data["state"]),
            progress_percent=float(data.get("progress_percent", 0)),
            completed_bytes=data.get("completed_bytes"),
            total_bytes=data.get("total_bytes"),
            status_code=data.get("status_code", ""),
            error_code=data.get("error_code"),
            created_at=data.get("created_at", utc_now()),
            updated_at=data.get("updated_at", utc_now()),
            idempotency_key=data.get("idempotency_key", ""),
            verified=bool(data.get("verified", False)),
            proof=data.get("proof") if type(data.get("proof")) is dict else {},
        )

    def put(self, job: LocalModelJob) -> LocalModelJob:
        job.updated_at = utc_now()
        with self._lock, self._file_lock:
            data = self._load_raw()
            data["jobs"][job.job_id] = job.as_dict()
            if len(data["jobs"]) > 1000:
                ordered = sorted(data["jobs"].values(), key=lambda item: item.get("updated_at", ""), reverse=True)[
                    :1000
                ]
                data["jobs"] = {item["job_id"]: item for item in ordered}
            self._write(data)
        return job

    def get(self, job_id: str) -> LocalModelJob | None:
        with self._lock, self._file_lock:
            item = self._load_raw()["jobs"].get(job_id)
        try:
            return self._restore(item) if type(item) is dict else None
        except (KeyError, TypeError, ValueError):
            return None

    def list(self, limit: int = 100) -> list[LocalModelJob]:
        with self._lock, self._file_lock:
            values = list(self._load_raw()["jobs"].values())
        jobs = []
        for value in values:
            try:
                jobs.append(self._restore(value))
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(jobs, key=lambda job: job.updated_at, reverse=True)[: max(1, min(limit, 500))]

    def find_idempotent(self, key: str) -> LocalModelJob | None:
        if not key:
            return None
        return next((job for job in self.list(500) if job.idempotency_key == key), None)

    def mark_interrupted(self) -> int:
        count = 0
        for job in self.list(500):
            if job.state in {JobState.QUEUED, JobState.RUNNING, JobState.CANCELLING}:
                job.state = JobState.UNKNOWN_INTERRUPTED
                job.status_code = "restart_interrupted"
                self.put(job)
                count += 1
        return count
