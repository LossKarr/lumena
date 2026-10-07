"""Canonical business facade shared by REST, UI, and conversational tools."""

from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from .approval_store import ApprovalStore
from .audit import PersonalModelAudit
from .contracts import ModelVersionState, TrainingRunState, TrainingRunV1, new_id, sha256_json, utc_now
from .dataset_builder import DatasetBuilder
from .data_management import PersonalDataManager
from .experience_store import ExperienceStore
from .export_service import PersonalModelExporter
from .evaluation_service import VersionEvaluationService
from .inspector import PersonalModelInspector
from .job_store import TrainingJobStore
from .lineage_store import LineageStore
from .lifecycle import PersonalModelLifecycle
from .migration import HistoricalTrainingMigrator
from .policy import PersonalLearningPolicy, PersonalLearningPolicyStore
from .portability import PersonalModelArchive
from .process_manager import TrainingProcessManager
from .recommendations import build_recommendations
from .resource_governor import ResourceGovernor, TrainingSettings, TrainingSettingsStore


class PersonalModelControlPlane:
    """No raw file mutation escapes this service."""

    SENSITIVE_ACTIONS = frozenset({
        "cancel_training", "activate_version", "set_global_default", "rollback_version",
        "export_data", "import_data", "delete_data", "delete_version", "change_cloud_judge", "export_version",
    })

    def __init__(self, root: Path, *, owner_scope: str = "owner:local") -> None:
        self.root = Path(root).resolve()
        self.owner_scope = owner_scope
        self.experiences = ExperienceStore(self.root)
        self.jobs = TrainingJobStore(self.root)
        self.lineages = LineageStore(self.root)
        self.policy_store = PersonalLearningPolicyStore(self.root / "config" / "policy.json")
        self.settings_store = TrainingSettingsStore(self.root / "config" / "training.json")
        self.inspector = PersonalModelInspector(self.root, owner_scope=owner_scope)
        self.approvals = ApprovalStore(self.root)
        self.audit = PersonalModelAudit(self.root)
        self.processes = TrainingProcessManager(self.root, self.jobs)
        self.archive = PersonalModelArchive(self.root)
        self.data_manager = PersonalDataManager(self.root)

    def status(self) -> dict[str, Any]:
        return self.inspector.snapshot()

    def health(self) -> dict[str, Any]:
        snapshot = self.inspector.snapshot()
        dependencies = snapshot["readiness"]["dependencies"]
        blockers = []
        if not snapshot["policy"]["learning_enabled"]:
            blockers.append("personal_learning_disabled")
        if not snapshot["policy"]["local_capture_enabled"]:
            blockers.append("local_capture_disabled")
        if not dependencies.get("training_backend_ready", False):
            blockers.append("training_dependencies_missing")
        resources = snapshot.get("readiness", {}).get("resources") or {}
        if float(resources.get("free_disk_gb") or 0) < float(snapshot["settings"]["min_free_disk_gb"]):
            blockers.append("disk_reserve_too_low")
        return {"healthy": not blockers, "blockers": blockers, "snapshot": snapshot}

    def experience_stats(self) -> dict[str, Any]:
        return self.experiences.stats(self.owner_scope)

    def training_settings(self) -> dict[str, Any]:
        settings = self.settings_store.load()
        return {"requested": settings.to_dict(), "effective": settings.to_dict(), "provenance": "personal_model_config"}

    def update_training_settings(self, changes: dict[str, Any], *, actor: str) -> dict[str, Any]:
        current = self.settings_store.load().to_dict()
        allowed = set(current)
        unknown = sorted(set(changes) - allowed)
        if unknown:
            raise ValueError(f"training_settings_unknown:{','.join(unknown)}")
        current.update(changes)
        updated = TrainingSettings.from_dict(current)
        self.settings_store.save(updated)
        proof = self.audit.emit("training_settings_updated", actor=actor, owner_scope=self.owner_scope, result="success")
        return {"settings": updated.to_dict(), "proof_id": proof["audit_id"]}

    def update_policy(self, changes: dict[str, Any], *, actor: str, approval_token: str = "") -> dict[str, Any]:
        current = self.policy_store.load().to_dict()
        unknown = sorted(set(changes) - set(current))
        if unknown:
            raise ValueError(f"learning_policy_unknown:{','.join(unknown)}")
        if "cloud_judge_enabled" in changes and bool(changes["cloud_judge_enabled"]) != bool(current["cloud_judge_enabled"]):
            self._consume_approval(approval_token, "change_cloud_judge", "policy")
        current.update(changes)
        updated = PersonalLearningPolicy.from_dict(current)
        self.policy_store.save(updated)
        proof = self.audit.emit("learning_policy_updated", actor=actor, owner_scope=self.owner_scope, result="success")
        return {"policy": updated.to_dict(), "proof_id": proof["audit_id"]}

    def recommendations(self) -> list[dict[str, Any]]:
        return build_recommendations(self.inspector.snapshot())

    def reconcile_training_jobs(self, *, actor: str = "system") -> dict[str, Any]:
        result = self.processes.reconcile_orphans()
        proof_id = ""
        if result["interrupted"]:
            proof = self.audit.emit(
                "training_jobs_reconciled",
                actor=actor,
                owner_scope=self.owner_scope,
                result="interrupted",
                details={"interrupted_run_ids": result["interrupted"]},
            )
            proof_id = proof["audit_id"]
        return {**result, "proof_id": proof_id}

    def approval_preview(self, *, action: str, resource: str, actor: str) -> dict[str, Any]:
        if action not in self.SENSITIVE_ACTIONS:
            raise ValueError("approval_action_not_sensitive")
        ticket = self.approvals.issue(owner_scope=self.owner_scope, action=action, resource=resource)
        self.audit.emit("personal_model_action_requested", actor=actor, owner_scope=self.owner_scope, result="approval_pending", details={"action": action})
        return ticket

    def _consume_approval(self, token: str, action: str, resource: str) -> str:
        return self.approvals.consume(token, owner_scope=self.owner_scope, action=action, resource=resource)

    def ensure_lineage(self, *, display_prefix: str = "lumena") -> str:
        snapshot = self.lineages.snapshot(owner_scope=self.owner_scope)
        if snapshot["lineages"]:
            return sorted(snapshot["lineages"])[0]
        return self.lineages.create(owner_scope=self.owner_scope, display_prefix=display_prefix)

    def prepare_dataset(self) -> dict[str, Any]:
        policy = self.policy_store.load()
        if not policy.learning_enabled:
            raise PermissionError("personal_learning_disabled")
        manifest = DatasetBuilder(self.experiences).build_sft(self.owner_scope, policy)
        proof = self.audit.emit("dataset_sealed", actor="system", owner_scope=self.owner_scope, result="success", proof_id=manifest.manifest_hash(), details={"manifest_id": manifest.manifest_id})
        return {"manifest": manifest.to_dict(), "manifest_hash": manifest.manifest_hash(), "proof_id": proof["audit_id"]}

    def create_training(
        self, *, base_model_id: str, finetune: dict[str, Any], bump: str = "patch",
        display_prefix: str = "lumena", actor: str = "owner",
    ) -> dict[str, Any]:
        settings = self.settings_store.load()
        if not settings.enabled:
            raise PermissionError("personal_training_disabled")
        prepared = self.prepare_dataset()
        manifest_hash = prepared["manifest_hash"]
        lineage_id = self.ensure_lineage(display_prefix=display_prefix)
        reservation = self.lineages.reserve_next(lineage_id, bump=bump)
        run_id = new_id("run")
        now = utc_now()
        config = dict(finetune)
        provenance = {
            "display_prefix": display_prefix,
            "base_model_id": base_model_id,
            "base_revision": str(config.pop("base_revision", "unresolved")),
            "tokenizer_revision": str(config.pop("tokenizer_revision", "unresolved")),
            "adapter_method": str(config.pop("adapter_method", "lora")),
        }
        config["base_model_hf_id"] = base_model_id
        config["output_name"] = reservation["model_name"]
        run = TrainingRunV1(
            run_id=run_id,
            owner_scope=self.owner_scope,
            lineage_id=lineage_id,
            target_version=reservation["version"],
            dataset_manifest_hash=manifest_hash,
            training_config_hash=sha256_json({"finetune": config, "provenance": provenance}),
            state=TrainingRunState.QUEUED,
            created_at=now,
            updated_at=now,
        )
        self.jobs.create(run)
        run_dir = self.root / "runs" / run_id
        from src.utils.persistence import atomic_write_json
        atomic_write_json(run_dir / "reservation.json", reservation)
        atomic_write_json(run_dir / "training-config.json", config)
        atomic_write_json(run_dir / "provenance.json", provenance)
        proof = self.audit.emit("training_queued", actor=actor, owner_scope=self.owner_scope, result="success", details={"run_id": run_id, "lineage_id": lineage_id, "version": reservation["version"]})
        return {"run": run.to_dict(), "reservation": reservation, "proof_id": proof["audit_id"]}

    def launch_training(self, run_id: str, *, actor: str = "owner", manual: bool = True) -> dict[str, Any]:
        run = self.jobs.get(run_id)
        if run is None or run.owner_scope != self.owner_scope:
            raise KeyError("training_run_not_found")
        dependencies = self.inspector.snapshot(include_resources=False)["readiness"]["dependencies"]
        if not dependencies.get("training_backend_ready", False):
            if run.state == TrainingRunState.QUEUED:
                self.jobs.transition(run_id, TrainingRunState.WAITING_IDLE)
            return {"launched": False, "state": "waiting_idle", "reason_codes": ("training_dependencies_missing",)}
        settings = self.settings_store.load()
        snapshot = ResourceGovernor.probe(self.root)
        decision = ResourceGovernor().evaluate(settings, snapshot, manual=manual)
        if not decision.allowed:
            if run.state == TrainingRunState.QUEUED:
                self.jobs.transition(run_id, TrainingRunState.WAITING_IDLE)
            return {"launched": False, "state": "waiting_idle", "reason_codes": decision.reason_codes}
        config = __import__("json").loads((self.root / "runs" / run_id / "training-config.json").read_text(encoding="utf-8"))
        dataset_dir = self.experiences.owner_root(self.owner_scope) / "datasets" / run.dataset_manifest_hash
        result = self.processes.launch(run_id, {"backend": "canonical_sft_v1", "dataset_dir": str(dataset_dir), "finetune": config})
        proof = self.audit.emit("training_started", actor=actor, owner_scope=self.owner_scope, result="success", details={"run_id": run_id})
        return {**result, "proof_id": proof["audit_id"]}

    def pause_training(self, run_id: str, *, actor: str = "owner") -> dict[str, Any]:
        result = self.processes.request_pause(run_id)
        proof = self.audit.emit("training_pause_requested", actor=actor, owner_scope=self.owner_scope, result="success", details={"run_id": run_id})
        return {**result, "proof_id": proof["audit_id"]}

    def resume_training(self, run_id: str, *, actor: str = "owner") -> dict[str, Any]:
        run_dir = self.root / "runs" / run_id
        config = __import__("json").loads((run_dir / "training-config.json").read_text(encoding="utf-8"))
        run = self.jobs.get(run_id)
        if run is None or run.owner_scope != self.owner_scope:
            raise KeyError("training_run_not_found")
        dependencies = self.inspector.snapshot(include_resources=False)["readiness"]["dependencies"]
        if not dependencies.get("training_backend_ready", False):
            return {"launched": False, "state": run.state.value, "reason_codes": ("training_dependencies_missing",)}
        dataset_dir = self.experiences.owner_root(self.owner_scope) / "datasets" / run.dataset_manifest_hash
        result = self.processes.resume(run_id, {"backend": "canonical_sft_v1", "dataset_dir": str(dataset_dir), "finetune": config})
        proof = self.audit.emit("training_resumed", actor=actor, owner_scope=self.owner_scope, result="success", details={"run_id": run_id})
        return {**result, "proof_id": proof["audit_id"]}

    def cancel_training(self, run_id: str, *, approval_token: str, actor: str = "owner") -> dict[str, Any]:
        approval_id = self._consume_approval(approval_token, "cancel_training", run_id)
        result = self.processes.request_cancel(run_id)
        proof = self.audit.emit("training_cancel_requested", actor=actor, owner_scope=self.owner_scope, result="success", proof_id=approval_id, details={"run_id": run_id})
        return {**result, "proof_id": proof["audit_id"]}

    def activate_version(self, lineage_id: str, version: str, *, approval_token: str, actor: str = "owner") -> dict[str, Any]:
        resource = f"{lineage_id}:{version}"
        approval_id = self._consume_approval(approval_token, "activate_version", resource)
        record = self.lineages.activate_personal(lineage_id, version, approval_id=approval_id)
        proof = self.audit.emit("version_promoted", actor=actor, owner_scope=self.owner_scope, result="success", proof_id=approval_id, details={"lineage_id": lineage_id, "version": version})
        return {"version": record.to_dict(), "proof_id": proof["audit_id"]}

    @staticmethod
    def _switch_runtime_model(model_name: str) -> bool:
        try:
            from src.llm.providers import get_model_config, register_ollama_models
            if get_model_config(model_name) is None:
                register_ollama_models([model_name])
            from src.core import get_lumena
            core = get_lumena()
            llm = getattr(core, "llm", None) if core else None
            return True if llm is None else bool(llm.switch_model(model_name))
        except Exception:
            return False

    def set_global_default(self, lineage_id: str, version: str, *, approval_token: str, actor: str = "owner") -> dict[str, Any]:
        resource = f"{lineage_id}:{version}"
        approval_id = self._consume_approval(approval_token, "set_global_default", resource)
        target = self.lineages.get(lineage_id, version)
        if not target.personal_active:
            raise ValueError("global_default_requires_personal_active")
        if not self._switch_runtime_model(target.model_name):
            raise RuntimeError("personal_model_runtime_switch_failed")
        record = self.lineages.set_global_default(lineage_id, version, approval_id=approval_id)
        proof = self.audit.emit("personal_model_default_changed", actor=actor, owner_scope=self.owner_scope, result="success", proof_id=approval_id, details={"target": "personal", "version": version})
        return {"version": record.to_dict(), "effective_default": record.model_name, "proof_id": proof["audit_id"]}

    def use_principal_default(self, *, approval_token: str, actor: str = "owner") -> dict[str, Any]:
        approval_id = self._consume_approval(approval_token, "set_global_default", "principal")
        principal = str(self.inspector.snapshot(include_resources=False)["principal_model"])
        if not self._switch_runtime_model(principal):
            raise RuntimeError("principal_model_runtime_switch_failed")
        self.lineages.clear_global_default(approval_id=approval_id)
        proof = self.audit.emit("personal_model_default_changed", actor=actor, owner_scope=self.owner_scope, result="success", proof_id=approval_id, details={"target": "principal"})
        return {"effective_default": principal, "proof_id": proof["audit_id"]}

    def rollback_version(self, lineage_id: str, version: str, *, approval_token: str, actor: str = "owner") -> dict[str, Any]:
        resource = f"{lineage_id}:{version}"
        approval_id = self._consume_approval(approval_token, "rollback_version", resource)
        record = self.lineages.rollback(lineage_id, version, approval_id=approval_id)
        proof = self.audit.emit("version_rolled_back", actor=actor, owner_scope=self.owner_scope, result="success", proof_id=approval_id, details={"lineage_id": lineage_id, "version": version})
        return {"version": record.to_dict(), "proof_id": proof["audit_id"]}

    def export_version(
        self,
        lineage_id: str,
        version: str,
        *,
        approval_token: str,
        actor: str = "owner",
        quant_type: str = "Q4_K_M",
    ) -> dict[str, Any]:
        resource = f"{lineage_id}:{version}"
        approval_id = self._consume_approval(approval_token, "export_version", resource)
        record = self.lineages.get(lineage_id, version)
        if record.owner_scope != self.owner_scope:
            raise PermissionError("lineage_owner_mismatch")
        if record.status not in {ModelVersionState.CANDIDATE, ModelVersionState.EVALUATED, ModelVersionState.REJECTED}:
            raise ValueError("lineage_version_not_exportable")
        run = next((item for item in self.jobs.list() if item.lineage_id == lineage_id and item.target_version == version and item.state == TrainingRunState.COMPLETED), None)
        if run is None:
            raise KeyError("training_run_for_version_not_found")
        adapter = self.root / "runs" / run.run_id / "artifacts" / "adapter"
        merged = self.root / "versions" / lineage_id / version / "merged"
        from src.training.pipeline import merge_and_save
        merge_and_save(str(adapter), str(merged))
        export = PersonalModelExporter(self.root).export(
            model_name=record.model_name, merged_model_dir=merged, quant_type=quant_type,
        )
        if not export.canary_verified or not export.registered:
            raise RuntimeError("personal_model_export_canary_failed")
        proof_path = self.root / "exports" / record.model_name / "export-proof.json"
        import hashlib
        hashes = {
            **record.artifact_hashes,
            "gguf": export.gguf_sha256,
            "ollama_canary": hashlib.sha256(proof_path.read_bytes()).hexdigest(),
        }
        updated = self.lineages.update_candidate(
            lineage_id, version, status=record.status,
            evaluation_id=record.evaluation_id, artifact_hashes=hashes,
        )
        proof = self.audit.emit(
            "personal_model_export_verified", actor=actor, owner_scope=self.owner_scope,
            result="success", proof_id=approval_id,
            details={"lineage_id": lineage_id, "version": version},
        )
        return {"version": updated.to_dict(), "export": asdict(export), "proof_id": proof["audit_id"]}

    def evaluate_version(self, lineage_id: str, version: str, *, actor: str = "owner") -> dict[str, Any]:
        policy = self.policy_store.load()
        snapshot = self.inspector.snapshot(include_resources=False)
        principal = str(snapshot["principal_model"])
        active = self.lineages.active()
        if policy.judge_mode == "specific":
            judge_model = policy.judge_model
        elif policy.judge_mode in {"personal", "local_only"}:
            judge_model = active.model_name if active and "ollama_canary" in active.artifact_hashes else ""
        else:
            judge_model = policy.judge_model or principal
        updated, report = VersionEvaluationService(PersonalModelLifecycle(self.root)).evaluate(
            lineage_id, version,
            baseline_model=active.model_name if active else principal,
            judge_model=judge_model,
            cloud_allowed=policy.cloud_judge_enabled,
        )
        proof = self.audit.emit(
            "candidate_evaluated", actor=actor, owner_scope=self.owner_scope,
            result="success" if report.passed else "rejected", proof_id=report.evaluation_id,
            details={"lineage_id": lineage_id, "version": version},
        )
        return {"version": updated.to_dict(), "evaluation": report.to_dict(), "proof_id": proof["audit_id"]}

    def audit_trail(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.audit.list(owner_scope=self.owner_scope, limit=limit)

    def list_backups(self) -> list[dict[str, Any]]:
        return self.data_manager.list_backups()

    def migration_sources(self) -> list[dict[str, Any]]:
        return self.data_manager.discover_migration_sources()

    def migration_preview(self, sources: list[Path]) -> dict[str, Any]:
        resolved = self._resolve_import_sources(sources)
        report = HistoricalTrainingMigrator.preview(resolved, owner_scope=self.owner_scope)
        resource = sha256_json([str(path) for path in resolved])[:32]
        return {"dry_run": True, "approval_resource": resource, "report": report.to_dict()}

    def import_historical(self, sources: list[Path], *, approval_token: str, actor: str = "owner") -> dict[str, Any]:
        resolved = self._resolve_import_sources(sources)
        resource = sha256_json([str(path) for path in resolved])[:32]
        approval_id = self._consume_approval(approval_token, "import_data", resource)
        report = HistoricalTrainingMigrator(self.experiences).migrate(resolved, owner_scope=self.owner_scope)
        proof = self.audit.emit("lineage_migrated", actor=actor, owner_scope=self.owner_scope, result="success", proof_id=approval_id)
        return {"report": report.to_dict(), "proof_id": proof["audit_id"]}

    def _resolve_import_sources(self, sources: list[Path]) -> list[Path]:
        if not sources or len(sources) > 256:
            raise ValueError("historical_import_source_count_invalid")
        data_root = self.root.parent.resolve()
        resolved: list[Path] = []
        total_size = 0
        for source in sources:
            candidate = Path(source)
            path = (candidate if candidate.is_absolute() else data_root / candidate).resolve()
            if data_root != path and data_root not in path.parents:
                raise PermissionError("historical_import_outside_data_root")
            if path.suffix.lower() not in {".json", ".jsonl"}:
                raise ValueError("historical_import_type_not_allowed")
            if not path.is_file():
                raise FileNotFoundError("historical_import_source_missing")
            size = path.stat().st_size
            if size > 256 * 1024 * 1024:
                raise ValueError("historical_import_source_too_large")
            total_size += size
            if total_size > 1024 * 1024 * 1024:
                raise ValueError("historical_import_total_too_large")
            resolved.append(path)
        return sorted(set(resolved))

    def create_backup(self, *, approval_token: str, actor: str = "owner") -> dict[str, Any]:
        resource = "personal-model-backup"
        approval_id = self._consume_approval(approval_token, "export_data", resource)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = self.root / "backups" / f"personal-model-{stamp}.lumena-model.zip"
        result = self.archive.create(destination, approval_id=approval_id)
        proof = self.audit.emit("personal_model_backup_created", actor=actor, owner_scope=self.owner_scope, result="success", proof_id=approval_id, details={"file_count": result["file_count"]})
        return {**result, "backup_name": destination.name, "proof_id": proof["audit_id"]}

    def restore_backup(self, backup_name: str, *, approval_token: str, actor: str = "owner") -> dict[str, Any]:
        safe_name = Path(str(backup_name)).name
        if safe_name != backup_name or not safe_name.endswith(".lumena-model.zip"):
            raise ValueError("backup_name_invalid")
        resource = f"personal-model-restore:{safe_name}"
        approval_id = self._consume_approval(approval_token, "import_data", resource)
        result = self.archive.restore(self.root / "backups" / safe_name, approval_id=approval_id)
        proof = self.audit.emit("personal_model_backup_restored", actor=actor, owner_scope=self.owner_scope, result="success", proof_id=approval_id, details={"restored": result["restored"], "skipped": result["skipped"]})
        return {**result, "proof_id": proof["audit_id"]}

    def delete_learning_data(self, *, approval_token: str, actor: str = "owner") -> dict[str, Any]:
        resource = "personal-learning-data"
        active_states = {
            TrainingRunState.QUEUED, TrainingRunState.WAITING_IDLE, TrainingRunState.RUNNING,
            TrainingRunState.RESUMING, TrainingRunState.PAUSING, TrainingRunState.PAUSED,
            TrainingRunState.CANCELLING, TrainingRunState.UNKNOWN_INTERRUPTED,
        }
        if any(run.owner_scope == self.owner_scope and run.state in active_states for run in self.jobs.list()):
            raise ValueError("learning_data_delete_training_active")
        approval_id = self._consume_approval(approval_token, "delete_data", resource)
        result = self.data_manager.delete_learning_data(self.owner_scope, approval_id=approval_id)
        proof = self.audit.emit(
            "personal_learning_data_deleted", actor=actor, owner_scope=self.owner_scope,
            result="success", proof_id=approval_id,
            details={"deleted_files": result["deleted_files"], "deleted_bytes": result["deleted_bytes"]},
        )
        return {**result, "proof_id": proof["audit_id"]}
