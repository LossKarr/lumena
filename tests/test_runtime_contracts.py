import pytest

from src.autonomy.scheduler import LumenaScheduler, TaskStatus
from src.channels.base import BaseChannel, ChannelType
from src.channels.manager import ChannelManager
from src.core import LumenaCore
from src.core_services.context_service import ContextService
from src.core_services.contracts import ServiceContext


class _DummyChannel(BaseChannel):
    def __init__(self):
        super().__init__(ChannelType.TELEGRAM)
        self.is_running = True

    @property
    def is_available(self):
        return True

    def get_runtime_status(self):
        return {
            "enabled": True,
            "running": True,
            "state": "running",
            "last_error": None,
            "custom_flag": "ok",
        }

    async def start(self) -> bool:
        self.is_running = True
        return True

    async def stop(self) -> None:
        self.is_running = False

    async def send_message(self, content: str, target_id: str, **kwargs) -> bool:
        return True


def test_channel_manager_runtime_snapshot_normalized():
    manager = ChannelManager()
    manager.register_channel(_DummyChannel())

    snapshot = manager.get_runtime_snapshot()

    assert snapshot["registered_count"] == 1
    assert "telegram" in snapshot["channels"]
    assert snapshot["channels"]["telegram"]["state"] == "running"
    assert snapshot["channels"]["telegram"]["custom_flag"] == "ok"


def test_core_status_contains_channel_contract(monkeypatch):
    monkeypatch.setenv("LUMENA_WEB_ONLY", "1")
    monkeypatch.setenv("LUMENA_DISABLE_TELEGRAM", "0")

    core = LumenaCore.__new__(LumenaCore)
    svc_ctx = ServiceContext.__new__(ServiceContext)
    core._context_svc = ContextService(svc_ctx)
    core.is_initialized = True
    core.personality = type("P", (), {"name": "test"})()
    core.memory = None
    core.emotion_manager = None
    core.tool_system = None
    core.repo_map = None
    core.code_index = None
    core.rules_loader = None
    core.hook_system = None
    core.instinct_system = None
    core.tts = None
    core.context = type("Ctx", (), {"messages": [], "max_messages": 20})()
    core._last_mentioned_url = None
    core._last_search_query = None

    status = LumenaCore.get_status(core)

    assert "channels" in status
    assert status["channels"]["legacy_channel_path_enabled"] in {True, False}
    assert status["channels"]["channels"]["telegram"]["state"] == "disabled_by_config"


@pytest.mark.asyncio
async def test_scheduler_run_task_normalizes_result_contract():
    scheduler = LumenaScheduler()

    async def _handler_false_success():
        return {"success": False, "status": "blocked", "reason": "precondition_failed"}

    scheduler.register_handler("_handler_false_success", _handler_false_success)
    task = scheduler.schedule(
        name="contract",
        description="contract",
        handler_name="_handler_false_success",
    )

    ok = await scheduler.run_task(task)

    assert ok is False
    assert task.status == TaskStatus.FAILED
    assert "last_result" in task.metadata
    assert task.metadata["last_result"]["status"] == "blocked"
    assert task.metadata["last_result"]["reason"] == "precondition_failed"
    assert task.metadata["last_result"]["duration_ms"] >= 0
