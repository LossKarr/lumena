"""Bounded subprocess ownership for canonical personal-model training."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from threading import RLock
from typing import Any, Callable

from src.utils.persistence import atomic_write_json, safe_read_json

from .contracts import TrainingRunState
from .job_store import TrainingJobStore


PopenFactory = Callable[..., subprocess.Popen]
WorkerAliveProbe = Callable[[int, str], bool]


class TrainingProcessManager:
    """Launch only Lumena's fixed worker module; never execute generated code."""

    def __init__(
        self,
        root: Path,
        jobs: TrainingJobStore,
        *,
        popen_factory: PopenFactory = subprocess.Popen,
        worker_alive_probe: WorkerAliveProbe | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.jobs = jobs
        self.popen_factory = popen_factory
        self.worker_alive_probe = worker_alive_probe or self._worker_is_alive
        self._processes: dict[str, subprocess.Popen] = {}
        self._lock = RLock()

    def _run_dir(self, run_id: str) -> Path:
        if not run_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for char in run_id):
            raise ValueError("training_run_id_invalid")
        return self.root / "runs" / run_id

    def launch(self, run_id: str, worker_config: dict[str, Any]) -> dict[str, Any]:
        run = self.jobs.get(run_id)
        if run is None:
            raise KeyError("training_run_not_found")
        if run.state not in {TrainingRunState.QUEUED, TrainingRunState.WAITING_IDLE, TrainingRunState.RESUMING}:
            raise ValueError("training_run_not_launchable")
        run_dir = self._run_dir(run_id)
        run_dir.mkdir(parents=True, exist_ok=True)
        config_path = run_dir / "worker_config.json"
        atomic_write_json(config_path, worker_config)
        command = [sys.executable, "-m", "src.training.personal.worker", "--run-id", run_id, "--root", str(self.root)]
        kwargs: dict[str, Any] = {
            "cwd": str(Path(__file__).resolve().parents[3]),
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "shell": False,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        process = self.popen_factory(command, **kwargs)
        with self._lock:
            self._processes[run_id] = process
        atomic_write_json(run_dir / "process.json", {"schema_version": 1, "pid": int(process.pid), "command_kind": "personal_training_worker"})
        try:
            self.jobs.transition(run_id, TrainingRunState.RUNNING)
        except Exception:
            try:
                process.terminate()
            except Exception:
                pass
            raise
        return {"run_id": run_id, "pid": int(process.pid), "state": "running"}

    @staticmethod
    def _worker_is_alive(pid: int, run_id: str) -> bool:
        if pid <= 0:
            return False
        try:
            import psutil

            process = psutil.Process(pid)
            if not process.is_running() or process.status() == psutil.STATUS_ZOMBIE:
                return False
            try:
                command = process.cmdline()
            except (psutil.AccessDenied, psutil.ZombieProcess):
                return True
            joined = " ".join(command)
            return "src.training.personal.worker" in joined and "--run-id" in command and run_id in command
        except ImportError:
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False
        except Exception:
            return False

    def _recorded_worker_is_alive(self, run_id: str) -> bool:
        with self._lock:
            managed = self._processes.get(run_id)
        if managed is not None:
            return managed.poll() is None
        metadata = safe_read_json(self._run_dir(run_id) / "process.json", default={})
        pid = int(metadata.get("pid") or 0) if isinstance(metadata, dict) else 0
        kind = metadata.get("command_kind") if isinstance(metadata, dict) else None
        return bool(kind == "personal_training_worker" and self.worker_alive_probe(pid, run_id))

    def reconcile_orphans(self) -> dict[str, list[str]]:
        """Reconcile persisted jobs without interrupting a verified live worker."""
        active_states = {
            TrainingRunState.RUNNING,
            TrainingRunState.RESUMING,
            TrainingRunState.PAUSING,
            TrainingRunState.CANCELLING,
        }
        alive: list[str] = []
        interrupted: list[str] = []
        for run in self.jobs.list():
            if run.state not in active_states:
                continue
            if self._recorded_worker_is_alive(run.run_id):
                alive.append(run.run_id)
                continue
            self.jobs.transition(
                run.run_id,
                TrainingRunState.UNKNOWN_INTERRUPTED,
                error_code="worker_process_missing_after_restart",
            )
            interrupted.append(run.run_id)
        return {"alive": alive, "interrupted": interrupted}

    def resume(self, run_id: str, worker_config: dict[str, Any]) -> dict[str, Any]:
        run = self.jobs.get(run_id)
        if run is None:
            raise KeyError("training_run_not_found")
        if run.state not in {TrainingRunState.PAUSED, TrainingRunState.UNKNOWN_INTERRUPTED, TrainingRunState.FAILED}:
            raise ValueError("training_run_not_resumable")
        if not run.checkpoint_path:
            raise ValueError("training_checkpoint_missing")
        payload = dict(worker_config)
        payload.setdefault("finetune", {})
        payload["finetune"] = dict(payload["finetune"])
        payload["finetune"]["resume_from_checkpoint"] = run.checkpoint_path
        control_path = self._run_dir(run_id) / "control.json"
        if control_path.exists():
            control_path.unlink()
        self.jobs.transition(run_id, TrainingRunState.RESUMING)
        return self.launch(run_id, payload)

    def _signal(self, run_id: str, action: str) -> dict[str, Any]:
        run = self.jobs.get(run_id)
        if run is None:
            raise KeyError("training_run_not_found")
        target = TrainingRunState.PAUSING if action == "pause" else TrainingRunState.CANCELLING
        updated = self.jobs.transition(run_id, target)
        atomic_write_json(self._run_dir(run_id) / "control.json", {"schema_version": 1, "action": action})
        return {"run_id": run_id, "state": updated.state.value, "action": action}

    def request_pause(self, run_id: str) -> dict[str, Any]:
        return self._signal(run_id, "pause")

    def request_cancel(self, run_id: str) -> dict[str, Any]:
        run = self.jobs.get(run_id)
        if run is None:
            raise KeyError("training_run_not_found")
        if run.state in {
            TrainingRunState.QUEUED,
            TrainingRunState.WAITING_IDLE,
            TrainingRunState.PAUSED,
            TrainingRunState.UNKNOWN_INTERRUPTED,
        }:
            updated = self.jobs.transition(run_id, TrainingRunState.CANCELLED)
            return {"run_id": run_id, "state": updated.state.value, "action": "cancel"}
        if run.state in {
            TrainingRunState.RUNNING,
            TrainingRunState.RESUMING,
            TrainingRunState.PAUSING,
            TrainingRunState.CANCELLING,
        } and not self._recorded_worker_is_alive(run_id):
            if run.state is not TrainingRunState.CANCELLING:
                self.jobs.transition(
                    run_id,
                    TrainingRunState.UNKNOWN_INTERRUPTED,
                    error_code="worker_process_missing_during_cancel",
                )
            else:
                self.jobs.transition(
                    run_id,
                    TrainingRunState.UNKNOWN_INTERRUPTED,
                    error_code="worker_process_missing_during_cancel",
                )
            updated = self.jobs.transition(run_id, TrainingRunState.CANCELLED)
            return {"run_id": run_id, "state": updated.state.value, "action": "cancel"}
        if run.state is TrainingRunState.CANCELLING:
            return {"run_id": run_id, "state": run.state.value, "action": "cancel"}
        return self._signal(run_id, "cancel")

    def control_signal(self, run_id: str) -> str | None:
        data = safe_read_json(self._run_dir(run_id) / "control.json", default={})
        action = data.get("action") if isinstance(data, dict) else None
        return action if action in {"pause", "cancel"} else None

    def poll(self, run_id: str) -> dict[str, Any]:
        with self._lock:
            process = self._processes.get(run_id)
        if process is None:
            return {"run_id": run_id, "running": False, "returncode": None}
        returncode = process.poll()
        return {"run_id": run_id, "running": returncode is None, "returncode": returncode}
