import pytest

from src.local_models.audit import LocalModelAudit
from src.local_models.contracts import InstalledModel
from src.local_models.identifiers import parse_model_reference
from src.local_models.job_store import LocalModelJobStore
from src.local_models.manager import LocalModelManager, LocalModelManagerError
from src.local_models.state_store import LocalModelStateStore
from src.local_models.ticket_store import DeleteTicketStore


class Client:
    def __init__(self):
        self.present = True
        self.running = True

    async def list_installed(self):
        return [InstalledModel(parse_model_reference("life-test:1b"), "digest", 10, None)] if self.present else []

    async def list_running(self):
        return [{"name": "life-test:1b"}] if self.running else []

    async def unload(self, ref):
        self.running = False

    async def delete(self, ref):
        self.present = False


def service(tmp_path):
    return LocalModelManager(
        client=Client(),
        state_store=LocalModelStateStore(tmp_path / "state.json"),
        job_store=LocalModelJobStore(tmp_path / "jobs.json"),
        audit=LocalModelAudit(tmp_path / "audit.jsonl"),
        ticket_store=DeleteTicketStore(tmp_path / "tickets.json"),
    )


@pytest.mark.asyncio
async def test_unload_proves_absence_from_ps(tmp_path):
    manager = service(tmp_path)
    result = await manager.unload("life-test:1b")
    assert result["loaded"] is False and result["verified"] is True


@pytest.mark.asyncio
async def test_installed_models_expose_their_assigned_roles(tmp_path):
    manager = service(tmp_path)
    ref = parse_model_reference("life-test:1b")
    manager.state_store.record_installed(ref)
    manager.state_store.assign("primary", ref)
    manager.state_store.assign("code", ref)

    models = await manager.installed()

    assert models[0]["assigned_roles"] == ["code", "primary"]


@pytest.mark.asyncio
async def test_delete_requires_ticket_and_proves_absence(monkeypatch, tmp_path):
    import src.llm.providers as providers

    manager = service(tmp_path)
    ref = parse_model_reference("life-test:1b")
    manager.state_store.record_installed(ref, digest="digest", size_bytes=10)
    prepared = await manager.prepare_delete("life-test:1b")
    result = await manager.delete("life-test:1b", prepared["ticket"])
    assert result["state"] == "absent" and result["verified"] is True
    with pytest.raises(LocalModelManagerError):
        await manager.delete("life-test:1b", prepared["ticket"])
    providers.AVAILABLE_MODELS.pop("life-test-1b", None)
    providers.MODEL_SKILLS.pop("life-test-1b", None)


@pytest.mark.asyncio
async def test_active_assignment_blocks_delete(tmp_path):
    manager = service(tmp_path)
    ref = parse_model_reference("life-test:1b")
    manager.state_store.record_installed(ref)
    manager.state_store.assign("primary", ref)
    with pytest.raises(LocalModelManagerError, match="active_model_cannot_be_deleted"):
        await manager.prepare_delete("life-test:1b")


@pytest.mark.asyncio
async def test_select_verifies_before_switching(tmp_path):
    manager = service(tmp_path)
    ref = parse_model_reference("life-test:1b")
    manager.state_store.record_installed(ref, digest="digest", size_bytes=10)

    async def show(_reference):
        return {"capabilities": ["completion"], "model_info": {"general.architecture": "test"}}

    async def canary(_reference):
        return "OK"

    manager.client.show = show
    manager.client.generate_canary = canary

    class Runtime:
        model_name = ""

        def switch_model(self, key):
            self.model_name = key
            return True

    result = await manager.select("life-test:1b", runtime_llm=Runtime())
    assert result["selected"] is True
    assert manager.state_store.entry(ref)["verified"] is True
