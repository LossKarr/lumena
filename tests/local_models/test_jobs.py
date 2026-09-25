import asyncio

import pytest

from src.local_models.audit import LocalModelAudit
from src.local_models.contracts import InstalledModel, JobState
from src.local_models.identifiers import parse_model_reference
from src.local_models.job_store import LocalModelJobStore
from src.local_models.manager import LocalModelManager, LocalModelManagerError
from src.local_models.state_store import LocalModelStateStore


class FakeClient:
    def __init__(self, *, present=True, pause=False):
        self.present = present
        self.pause = pause

    async def health(self):
        return {"available": True}

    async def list_running(self):
        return []

    async def pull(self, reference):
        yield {"percent": 50.0, "completed_bytes": 5, "total_bytes": 10, "status": "pulling", "done": False}
        if self.pause:
            await asyncio.sleep(0.05)
        yield {"percent": 100.0, "completed_bytes": 10, "total_bytes": 10, "status": "success", "done": True}

    async def list_installed(self):
        if not self.present:
            return []
        return [InstalledModel(parse_model_reference("job-test:1b"), "digest", 10, None)]

    async def show(self, reference):
        return {"capabilities": ["completion"]}

    async def generate_canary(self, reference):
        return "OK"


def manager(tmp_path, client):
    return LocalModelManager(
        client=client,
        state_store=LocalModelStateStore(tmp_path / "state.json"),
        job_store=LocalModelJobStore(tmp_path / "jobs.json"),
        audit=LocalModelAudit(tmp_path / "audit.jsonl"),
    )


@pytest.mark.asyncio
async def test_job_succeeds_only_after_tags_and_show_postcondition(monkeypatch, tmp_path):
    import src.llm.providers as providers

    monkeypatch.setattr(providers, "_probe_ollama_model", lambda *a, **k: {"ok": False})
    service = manager(tmp_path, FakeClient())
    job = service.install("job-test:1b", idempotency_key="same")
    await service._tasks[job.job_id]
    stored = service.job_store.get(job.job_id)
    assert stored.state is JobState.SUCCEEDED
    assert stored.verified is True
    assert stored.proof["kind"] == "ollama_tags_show_canary"
    assert service.install("job-test:1b", idempotency_key="same").job_id == job.job_id
    providers.AVAILABLE_MODELS.pop("job-test-1b", None)
    providers.MODEL_SKILLS.pop("job-test-1b", None)


@pytest.mark.asyncio
async def test_stream_success_without_installed_model_fails(tmp_path):
    service = manager(tmp_path, FakeClient(present=False))
    job = service.install("job-test:1b")
    await service._tasks[job.job_id]
    stored = service.job_store.get(job.job_id)
    assert stored.state is JobState.FAILED
    assert stored.error_code == "install_postcondition_missing"


@pytest.mark.asyncio
async def test_cancellation_is_persisted(tmp_path):
    service = manager(tmp_path, FakeClient(pause=True))
    job = service.install("job-test:1b")
    await asyncio.sleep(0)
    service.cancel_job(job.job_id)
    await service._tasks[job.job_id]
    assert service.job_store.get(job.job_id).state is JobState.CANCELLED


def test_restart_marks_inflight_job_interrupted(tmp_path):
    store = LocalModelJobStore(tmp_path / "jobs.json")
    from src.local_models.contracts import LocalModelJob

    job = LocalModelJob("one", "install", parse_model_reference("qwen3:8b"), state=JobState.RUNNING)
    store.put(job)
    assert store.mark_interrupted() == 1
    assert store.get("one").state is JobState.UNKNOWN_INTERRUPTED


@pytest.mark.asyncio
async def test_active_install_is_deduplicated_and_queue_is_bounded(monkeypatch, tmp_path):
    monkeypatch.setenv("LUMENA_LOCAL_MODEL_MAX_QUEUED", "1")
    service = manager(tmp_path, FakeClient(pause=True))
    first = service.install("job-test:1b")
    assert service.install("job-test:1b").job_id == first.job_id
    with pytest.raises(LocalModelManagerError, match="local_model_queue_full"):
        service.install("other-test:1b")
    service.cancel_job(first.job_id)
    await service._tasks[first.job_id]


@pytest.mark.asyncio
async def test_idempotency_conflict_wins_over_same_model_deduplication(tmp_path):
    service = manager(tmp_path, FakeClient(pause=True))
    other = service.install("other-test:1b", idempotency_key="reused")
    current = service.install("job-test:1b")
    with pytest.raises(LocalModelManagerError, match="idempotency_key_conflict"):
        service.install("job-test:1b", idempotency_key="reused")
    service.cancel_job(other.job_id)
    service.cancel_job(current.job_id)
    await asyncio.gather(service._tasks[other.job_id], service._tasks[current.job_id])
