"""Single business service used by Web routes and conversational tools."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
from pathlib import Path
import uuid
from typing import Any

from src.utils.paths import DATA_DIR

from .audit import LocalModelAudit
from .catalog_service import LocalModelCatalogService
from .contracts import JobState, LocalModelJob
from .identifiers import parse_model_reference
from .job_store import LocalModelJobStore
from .ollama_client import OllamaClient, OllamaClientError
from .registry import reconcile_registry, register_local_model, unregister_local_model
from .hardware_service import get_hardware_inventory
from .recommendations import recommend_catalog_models
from .state_store import LocalModelStateStore
from .verification import verify_local_model
from .ticket_store import DeleteTicketError, DeleteTicketStore


class LocalModelManagerError(RuntimeError):
    pass


class LocalModelManager:
    def __init__(
        self,
        *,
        client: OllamaClient | None = None,
        catalog: LocalModelCatalogService | None = None,
        state_store: LocalModelStateStore | None = None,
        job_store: LocalModelJobStore | None = None,
        audit: LocalModelAudit | None = None,
        ticket_store: DeleteTicketStore | None = None,
    ) -> None:
        self.client = client or OllamaClient()
        self.catalog = catalog or LocalModelCatalogService()
        self.state_store = state_store or LocalModelStateStore()
        self.job_store = job_store or LocalModelJobStore()
        self.audit = audit or LocalModelAudit()
        self.ticket_store = ticket_store or DeleteTicketStore()
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancel: dict[str, asyncio.Event] = {}
        self._model_locks: dict[str, asyncio.Lock] = {}
        self._semaphore = asyncio.Semaphore(max(1, min(int(os.getenv("LUMENA_LOCAL_MODEL_MAX_CONCURRENT", "2")), 8)))
        self.job_store.mark_interrupted()

    async def status(self) -> dict[str, Any]:
        health = await self.client.health()
        return {
            **health,
            "active_jobs": sum(
                job.state in {JobState.QUEUED, JobState.RUNNING, JobState.CANCELLING} for job in self.job_store.list()
            ),
        }

    async def installed(self) -> list[dict[str, Any]]:
        installed = await self.client.list_installed()
        # Un ``ollama pull`` exécuté hors de Lumena peut arriver après le boot.
        # La simple ouverture/actualisation du panneau doit alors importer le
        # modèle dans le registre runtime, tout en respectant un choix durable
        # précédemment désactivé.
        for model in installed:
            entry = self.state_store.entry(model.reference)
            if (
                not entry.get("installed")
                or entry.get("digest") != model.digest
                or entry.get("size_bytes") != model.size_bytes
            ):
                self.state_store.record_installed(
                    model.reference,
                    digest=model.digest,
                    size_bytes=model.size_bytes,
                )
        reconcile_registry([model.reference for model in installed], self.state_store)

        running = {str(item.get("name")) for item in await self.client.list_running()}
        assignments = self.state_store.load().get("assignments", {})
        result = []
        for model in installed:
            entry = self.state_store.entry(model.reference)
            model_key = self.state_store.key(model.reference)
            payload = model.as_dict()
            payload.update(
                {
                    "installed": True,
                    "enabled": bool(entry.get("enabled", True)),
                    "verified": bool(entry.get("verified", False)),
                    "loaded": model.reference.pull_reference in running or model.reference.canonical in running,
                    "assigned_roles": sorted(
                        role for role, assigned_model in assignments.items() if assigned_model == model_key
                    ),
                }
            )
            result.append(payload)
        return result

    async def search(self, query: str = "", *, source: str = "all", limit: int = 30, offset: int = 0) -> dict[str, Any]:
        result = await self.catalog.search(query, source=source, limit=limit, offset=offset)
        try:
            installed = await self.client.list_installed()
        except OllamaClientError:
            installed = []
        installed_refs = {item.reference.pull_reference for item in installed} | {
            item.reference.canonical for item in installed
        }
        for item in result["models"]:
            reference = item["reference"]
            parsed = parse_model_reference(reference["canonical"], reference["source"])
            item["installed"] = parsed.pull_reference in installed_refs or parsed.canonical in installed_refs
            item["enabled"] = self.state_store.is_enabled(parsed) if item["installed"] else False
        return result

    async def recommend(
        self, query: str = "", *, intent: str = "general", source: str = "all", limit: int = 5
    ) -> dict[str, Any]:
        catalogue = await self.search(query, source=source, limit=100, offset=0)
        hardware = get_hardware_inventory()
        return {
            "intent": intent,
            "hardware": hardware,
            "recommendations": recommend_catalog_models(catalogue["models"], hardware, intent=intent, limit=limit),
            "sources": catalogue["sources"],
        }

    def _disk_preflight(self, expected_bytes: int | None = None) -> None:
        free = shutil.disk_usage(Path(DATA_DIR).anchor or DATA_DIR).free
        reserve_gb = max(0.0, float(os.getenv("LUMENA_LOCAL_MODEL_MIN_FREE_GB", "2")))
        reserve = int(reserve_gb * 1024**3)
        if free - (expected_bytes or 0) < reserve:
            raise LocalModelManagerError("local_model_disk_insufficient")

    def install(
        self,
        reference: str,
        *,
        source: str | None = None,
        idempotency_key: str = "",
        enable_after_install: bool = True,
        expected_bytes: int | None = None,
        caller_kind: str = "web",
    ) -> LocalModelJob:
        ref = parse_model_reference(reference, source)
        active_states = {JobState.QUEUED, JobState.RUNNING, JobState.CANCELLING}
        active_jobs = [job for job in self.job_store.list(500) if job.state in active_states]
        if idempotency_key:
            existing = self.job_store.find_idempotent(idempotency_key[:128])
            if existing:
                if existing.reference != ref or existing.operation != "install":
                    raise LocalModelManagerError("idempotency_key_conflict")
                return existing
        same_model = next(
            (job for job in active_jobs if job.operation == "install" and job.reference == ref),
            None,
        )
        if same_model is not None:
            return same_model
        max_queued = max(1, min(int(os.getenv("LUMENA_LOCAL_MODEL_MAX_QUEUED", "20")), 200))
        if len(active_jobs) >= max_queued:
            raise LocalModelManagerError("local_model_queue_full")
        self._disk_preflight(expected_bytes)
        job = LocalModelJob(
            job_id=uuid.uuid4().hex,
            operation="install",
            reference=ref,
            idempotency_key=idempotency_key[:128],
            status_code="queued",
        )
        self.job_store.put(job)
        cancel = asyncio.Event()
        self._cancel[job.job_id] = cancel
        task = asyncio.create_task(self._run_install(job, cancel, enable_after_install, caller_kind))
        self._tasks[job.job_id] = task
        task.add_done_callback(lambda _task, job_id=job.job_id: self._tasks.pop(job_id, None))
        self.audit.emit(
            "local_model_install_requested",
            operation_id=job.job_id,
            model=ref.canonical,
            source=ref.source.value,
            caller_kind=caller_kind,
        )
        return job

    async def _run_install(
        self, job: LocalModelJob, cancel: asyncio.Event, enable_after: bool, caller_kind: str
    ) -> None:
        lock = self._model_locks.setdefault(job.reference.pull_reference, asyncio.Lock())
        try:
            async with self._semaphore, lock:
                if cancel.is_set():
                    raise asyncio.CancelledError
                job.state = JobState.RUNNING
                job.status_code = "pulling"
                self.job_store.put(job)
                self.audit.emit(
                    "local_model_install_started",
                    operation_id=job.job_id,
                    model=job.reference.canonical,
                    source=job.reference.source.value,
                    caller_kind=caller_kind,
                )
                async for event in self.client.pull(job.reference.pull_reference):
                    if cancel.is_set():
                        raise asyncio.CancelledError
                    job.progress_percent = float(event["percent"])
                    job.completed_bytes = event["completed_bytes"]
                    job.total_bytes = event["total_bytes"]
                    job.status_code = event["status"] or "pulling"
                    self.job_store.put(job)
                installed = await self.client.list_installed()
                match = next(
                    (
                        item
                        for item in installed
                        if item.reference.pull_reference == job.reference.pull_reference
                        or item.reference.canonical == job.reference.canonical
                    ),
                    None,
                )
                if match is None or not match.digest:
                    raise LocalModelManagerError("install_postcondition_missing")
                verification = await verify_local_model(self.client, job.reference.pull_reference)
                if verification.status == "incompatible":
                    raise LocalModelManagerError(verification.error_code or "model_verification_failed")
                digest = match.digest
                proof_digest = hashlib.sha256(f"{job.reference.pull_reference}\0{digest}".encode()).hexdigest()
                self.state_store.record_installed(job.reference, digest=digest, size_bytes=match.size_bytes)
                if enable_after:
                    register_local_model(job.reference, self.state_store, verified=verification.status == "verified")
                else:
                    self.state_store.set_enabled(job.reference, False, verified=verification.status == "verified")
                job.state = JobState.SUCCEEDED
                job.progress_percent = 100.0
                job.status_code = "installed_verified"
                job.verified = verification.status == "verified"
                job.proof = {
                    "kind": "ollama_tags_show_canary",
                    "digest": digest[:32],
                    "proof_digest": proof_digest,
                    "verification": verification.as_dict(),
                }
                self.job_store.put(job)
                self.audit.emit(
                    "local_model_install_completed",
                    operation_id=job.job_id,
                    model=job.reference.canonical,
                    source=job.reference.source.value,
                    state=job.state.value,
                    digest=digest[:16],
                    verified=True,
                    caller_kind=caller_kind,
                )
        except asyncio.CancelledError:
            job.state = JobState.CANCELLED
            job.status_code = "cancelled"
            job.error_code = "install_cancelled"
            self.job_store.put(job)
        except (OllamaClientError, LocalModelManagerError) as exc:
            job.state = JobState.FAILED
            job.status_code = "failed"
            job.error_code = getattr(exc, "code", str(exc))[:120]
            self.job_store.put(job)
            self.audit.emit(
                "local_model_install_failed",
                operation_id=job.job_id,
                model=job.reference.canonical,
                source=job.reference.source.value,
                error_code=job.error_code,
                caller_kind=caller_kind,
            )
        finally:
            self._cancel.pop(job.job_id, None)

    def cancel_job(self, job_id: str) -> LocalModelJob:
        job = self.job_store.get(job_id)
        if job is None:
            raise LocalModelManagerError("job_not_found")
        if job.state not in {JobState.QUEUED, JobState.RUNNING}:
            return job
        job.state = JobState.CANCELLING
        job.status_code = "cancellation_requested"
        self.job_store.put(job)
        event = self._cancel.get(job_id)
        if event:
            event.set()
        return job

    async def reconcile(self) -> dict[str, int]:
        installed = await self.client.list_installed()
        for item in installed:
            self.state_store.record_installed(item.reference, digest=item.digest, size_bytes=item.size_bytes)
        return reconcile_registry([item.reference for item in installed], self.state_store)

    async def enable(self, reference: str, source: str | None = None) -> dict[str, Any]:
        ref = parse_model_reference(reference, source)
        installed = await self.client.list_installed()
        if not any(
            item.reference.pull_reference == ref.pull_reference or item.reference.canonical == ref.canonical
            for item in installed
        ):
            raise LocalModelManagerError("model_not_installed")
        key = register_local_model(ref, self.state_store, verified=bool(self.state_store.entry(ref).get("verified")))
        self.audit.emit(
            "local_model_enabled", model=ref.canonical, source=ref.source.value, state="enabled", verified=True
        )
        return {"reference": ref.as_dict(), "enabled": True, "lumena_model_key": key, "verified": True}

    def disable(self, reference: str, source: str | None = None, *, current_model_key: str = "") -> dict[str, Any]:
        ref = parse_model_reference(reference, source)
        key = self.state_store.key(ref)
        assignments = self.state_store.load().get("assignments", {})
        from .registry import lumena_model_key

        assigned_roles = [role for role, value in assignments.items() if value == key]
        blocking_roles = [
            role
            for role in assigned_roles
            if role != "primary" or not current_model_key or current_model_key == lumena_model_key(ref)
        ]
        if blocking_roles or current_model_key == lumena_model_key(ref):
            raise LocalModelManagerError("active_model_cannot_be_disabled")
        removed = unregister_local_model(ref, self.state_store)
        self.audit.emit(
            "local_model_disabled", model=ref.canonical, source=ref.source.value, state="disabled", verified=True
        )
        return {
            "reference": ref.as_dict(),
            "enabled": False,
            "registry_removed": removed,
            "disk_preserved": True,
            "verified": True,
        }

    async def select(
        self, reference: str, source: str | None = None, *, runtime_llm: Any = None, role: str = "primary"
    ) -> dict[str, Any]:
        ref = parse_model_reference(reference, source)
        entry = self.state_store.entry(ref)
        if not entry.get("enabled", True):
            raise LocalModelManagerError("model_not_enabled")
        if not entry.get("verified", False):
            verification = await self.verify(reference, source)
            if verification["verification"]["status"] != "verified":
                raise LocalModelManagerError("model_not_verified")
        # Le modèle peut avoir été installé manuellement pendant que Lumena
        # tournait. L'enregistrer ici rend la sélection atomique même si le
        # panneau n'a pas encore déclenché de réconciliation.
        key = register_local_model(ref, self.state_store, verified=True)
        if runtime_llm is None or not hasattr(runtime_llm, "switch_model"):
            raise LocalModelManagerError("runtime_model_switch_unavailable")
        if runtime_llm.switch_model(key) is False:
            raise LocalModelManagerError("runtime_model_switch_failed")
        if getattr(runtime_llm, "model_name", None) != key:
            raise LocalModelManagerError("runtime_model_switch_postcondition_failed")
        self.state_store.assign(role, ref)
        self.audit.emit(
            "local_model_selected", model=ref.canonical, source=ref.source.value, state="selected", verified=True
        )
        return {"reference": ref.as_dict(), "selected": True, "role": role, "lumena_model_key": key, "verified": True}

    async def verify(self, reference: str, source: str | None = None) -> dict[str, Any]:
        ref = parse_model_reference(reference, source)
        installed = await self.client.list_installed()
        if not any(
            item.reference.pull_reference == ref.pull_reference or item.reference.canonical == ref.canonical
            for item in installed
        ):
            raise LocalModelManagerError("model_not_installed")
        result = await verify_local_model(self.client, ref.pull_reference)
        if result.status == "incompatible":
            raise LocalModelManagerError(result.error_code or "model_verification_failed")
        self.state_store.set_enabled(
            ref,
            self.state_store.is_enabled(ref),
            verified=result.status == "verified",
        )
        self.audit.emit(
            "local_model_verification_completed",
            model=ref.canonical,
            source=ref.source.value,
            state=result.status,
            verified=result.status == "verified",
        )
        return {"reference": ref.as_dict(), "verification": result.as_dict()}

    async def unload(self, reference: str, source: str | None = None) -> dict[str, Any]:
        ref = parse_model_reference(reference, source)
        await self.client.unload(ref.pull_reference)
        for _ in range(5):
            running = await self.client.list_running()
            names = {str(item.get("name") or "") for item in running}
            if ref.pull_reference not in names and ref.canonical not in names:
                self.audit.emit(
                    "local_model_unloaded",
                    model=ref.canonical,
                    source=ref.source.value,
                    state="unloaded",
                    verified=True,
                )
                return {"reference": ref.as_dict(), "loaded": False, "verified": True}
            await asyncio.sleep(0.2)
        raise LocalModelManagerError("unload_postcondition_failed")

    async def prepare_delete(
        self, reference: str, source: str | None = None, *, current_model_key: str = ""
    ) -> dict[str, Any]:
        ref = parse_model_reference(reference, source)
        installed = await self.client.list_installed()
        match = next(
            (
                item
                for item in installed
                if item.reference.pull_reference == ref.pull_reference or item.reference.canonical == ref.canonical
            ),
            None,
        )
        if match is None:
            raise LocalModelManagerError("model_not_installed")
        from .registry import lumena_model_key

        assignments = [
            role
            for role, value in self.state_store.load().get("assignments", {}).items()
            if value == self.state_store.key(ref)
        ]
        blocking_assignments = [
            role
            for role in assignments
            if role != "primary" or not current_model_key or current_model_key == lumena_model_key(ref)
        ]
        active = current_model_key == lumena_model_key(ref) or bool(blocking_assignments)
        running = await self.client.list_running()
        loaded = any(str(item.get("name") or "") in {ref.pull_reference, ref.canonical} for item in running)
        impact = {
            "reference": ref.as_dict(),
            "size_bytes": match.size_bytes,
            "enabled": self.state_store.is_enabled(ref),
            "assignments": assignments,
            "active": active,
            "loaded": loaded,
        }
        if active:
            raise LocalModelManagerError("active_model_cannot_be_deleted")
        token, record = self.ticket_store.issue(ref, impact)
        self.audit.emit(
            "local_model_delete_prepared",
            model=ref.canonical,
            source=ref.source.value,
            state="ticket_issued",
            verified=True,
        )
        return {"ticket": token, "expires_at_epoch": record["expires_at_epoch"], "impact": impact}

    async def delete(
        self,
        reference: str,
        ticket: str,
        source: str | None = None,
        *,
        current_model_key: str = "",
        caller_kind: str = "web",
    ) -> dict[str, Any]:
        ref = parse_model_reference(reference, source)
        from .registry import lumena_model_key

        state = self.state_store.load()
        assignments = [
            role for role, value in state.get("assignments", {}).items() if value == self.state_store.key(ref)
        ]
        blocking_assignments = [
            role
            for role in assignments
            if role != "primary" or not current_model_key or current_model_key == lumena_model_key(ref)
        ]
        if current_model_key == lumena_model_key(ref) or blocking_assignments:
            raise LocalModelManagerError("active_model_cannot_be_deleted")
        if any(
            job.reference == ref and job.state in {JobState.QUEUED, JobState.RUNNING, JobState.CANCELLING}
            for job in self.job_store.list(500)
        ):
            raise LocalModelManagerError("model_job_in_progress")
        try:
            self.ticket_store.consume(ticket, ref)
        except DeleteTicketError as exc:
            raise LocalModelManagerError(str(exc)) from None
        await self.unload(ref.canonical, ref.source.value)
        await self.client.delete(ref.pull_reference)
        installed = await self.client.list_installed()
        if any(
            item.reference.pull_reference == ref.pull_reference or item.reference.canonical == ref.canonical
            for item in installed
        ):
            self.audit.emit(
                "local_model_delete_failed",
                model=ref.canonical,
                source=ref.source.value,
                error_code="delete_postcondition_failed",
                caller_kind=caller_kind,
            )
            raise LocalModelManagerError("delete_postcondition_failed")
        unregister_local_model(ref, self.state_store)
        self.state_store.record_absent(ref)
        self.audit.emit(
            "local_model_deleted",
            model=ref.canonical,
            source=ref.source.value,
            state="absent",
            verified=True,
            caller_kind=caller_kind,
        )
        return {"reference": ref.as_dict(), "deleted": True, "state": "absent", "verified": True}


_MANAGER: LocalModelManager | None = None


def get_local_model_manager() -> LocalModelManager:
    global _MANAGER
    if _MANAGER is None:
        _MANAGER = LocalModelManager()
    return _MANAGER
