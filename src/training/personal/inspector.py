"""Bounded, current-state inspection for UI and conversational tools."""

from __future__ import annotations

import os
import importlib.util
from functools import lru_cache
from pathlib import Path
from typing import Any

from .experience_store import ExperienceStore
from .job_store import TrainingJobStore
from .lineage_store import LineageStore
from .policy import PersonalLearningPolicyStore
from .resource_governor import ResourceGovernor, TrainingSettingsStore
from src.utils.persistence import safe_read_json


class PersonalModelInspector:
    def __init__(self, root: Path, *, owner_scope: str = "owner:local") -> None:
        self.root = Path(root)
        self.owner_scope = owner_scope
        self.experiences = ExperienceStore(self.root)
        self.jobs = TrainingJobStore(self.root)
        self.lineages = LineageStore(self.root)
        self.policy_store = PersonalLearningPolicyStore(self.root / "config" / "policy.json")
        self.settings_store = TrainingSettingsStore(self.root / "config" / "training.json")

    def snapshot(self, *, include_resources: bool = True) -> dict[str, Any]:
        policy = self.policy_store.load()
        settings = self.settings_store.load()
        active = self.lineages.active()
        jobs = self.jobs.list()
        active_job = next((run for run in jobs if run.state.value in {"queued", "waiting_idle", "running", "resuming", "pausing", "paused", "cancelling", "unknown_interrupted"}), None)
        resources = None
        if include_resources:
            sample = ResourceGovernor.probe(self.root)
            resources = {
                "cpu_percent": sample.cpu_percent,
                "ram_percent": sample.ram_percent,
                "vram_percent": sample.vram_percent,
                "free_disk_gb": round(sample.free_disk_gb, 2),
                "on_battery": sample.on_battery,
            }
        principal = os.environ.get("LUMENA_DEFAULT_MODEL", "deepseek-flash").strip() or "deepseek-flash"
        effective = active.model_name if active and active.global_default else principal
        return {
            "schema_version": 1,
            "owner_scope": self.owner_scope,
            "principal_model": principal,
            "personal_model": active.to_dict() if active else None,
            "effective_default": effective,
            "fallback_model": principal if active else "",
            "policy": policy.to_dict(),
            "judge": {
                "mode": policy.judge_mode,
                "specific_model": policy.judge_model,
                "cloud_allowed": policy.cloud_judge_enabled,
                "accept_threshold": policy.judge_accept_threshold,
                "disagreement_tolerance": policy.judge_disagreement_tolerance,
            },
            "settings": settings.to_dict(),
            "experiences": self.experiences.stats(self.owner_scope),
            "active_job": self._job_view(active_job) if active_job else None,
            "jobs": [self._job_view(run) for run in jobs[:100]],
            "lineages": self.lineages.snapshot(owner_scope=self.owner_scope),
            "readiness": {"resources": resources, "dependencies": self._dependencies()},
            "backup_available": (self.root / "backups").exists(),
        }

    def _job_view(self, run) -> dict[str, Any]:
        view = run.to_dict()
        progress = safe_read_json(self.root / "runs" / run.run_id / "progress.json", default={})
        if isinstance(progress, dict):
            latest = progress.get("latest")
            view["progress"] = latest if isinstance(latest, dict) else None
            view["progress_event_count"] = int(progress.get("event_count") or 0)
        return view

    @staticmethod
    @lru_cache(maxsize=1)
    def _dependencies() -> dict[str, bool]:
        dependencies = {
            name: importlib.util.find_spec(name) is not None
            for name in ("torch", "datasets", "transformers", "trl", "peft", "unsloth")
        }
        cuda_ready = False
        if dependencies["torch"]:
            try:
                import torch
                cuda_ready = bool(torch.cuda.is_available())
            except Exception:
                cuda_ready = False
        dependencies["torch_cuda"] = cuda_ready
        dependencies["training_backend_ready"] = all(
            dependencies[name] for name in ("torch", "datasets", "transformers", "trl", "peft", "unsloth", "torch_cuda")
        )
        return dependencies
