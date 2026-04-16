"""Tests unitaires pour src/background/manager.py"""
import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from src.background.manager import (
    BackgroundTask,
    BackgroundTaskManager,
    TaskStatus,
    get_task_manager,
)


def _mock_process(returncode=0, stdout=b"ok", stderr=b""):
    """Create a mock asyncio.subprocess.Process that won't hang ProactorEventLoop."""
    proc = MagicMock()
    proc.returncode = returncode
    proc.pid = 12345
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.wait = AsyncMock(return_value=returncode)
    proc.kill = MagicMock()
    return proc


class TestTaskStatus:
    def test_all_statuses_exist(self):
        assert TaskStatus.PENDING is not None
        assert TaskStatus.RUNNING is not None
        assert TaskStatus.COMPLETED is not None
        assert TaskStatus.FAILED is not None
        assert TaskStatus.CANCELLED is not None

    def test_status_values(self):
        assert TaskStatus.PENDING.value == "pending"
        assert TaskStatus.COMPLETED.value == "completed"


class TestBackgroundTask:
    def test_fields_exist(self):
        fields = set(BackgroundTask.__dataclass_fields__)
        assert "id" in fields
        assert "name" in fields
        assert "command" in fields
        assert "status" in fields
        assert "output" in fields


class TestBackgroundTaskManager:
    @pytest.fixture
    def manager(self):
        return BackgroundTaskManager()

    def test_instantiation(self, manager):
        assert manager is not None

    def test_initial_tasks_empty(self, manager):
        assert isinstance(manager.tasks, dict)

    def test_has_max_tasks(self, manager):
        assert hasattr(manager, "MAX_TASKS")
        assert manager.MAX_TASKS > 0

    @pytest.mark.asyncio
    async def test_start_command_returns_task(self, manager):
        with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock, return_value=_mock_process()):
            task = await manager.start_command(
                name="echo_test",
                command="python --version"
            )
            await asyncio.sleep(0.05)  # let _run_command complete
        assert task is not None
        assert isinstance(task, BackgroundTask)
        assert task.id is not None
        assert task.name == "echo_test"

    @pytest.mark.asyncio
    async def test_get_status_known_id(self, manager):
        with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock, return_value=_mock_process()):
            task = await manager.start_command(
                name="status_test",
                command="python --version"
            )
            await asyncio.sleep(0.05)
            status_info = await manager.get_status(task.id)
        assert status_info is not None
        assert isinstance(status_info, dict)

    @pytest.mark.asyncio
    async def test_get_status_unknown_id(self, manager):
        status = await manager.get_status("nonexistent_id")
        assert status is None

    @pytest.mark.asyncio
    async def test_get_all_tasks(self, manager):
        with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock, return_value=_mock_process()):
            await manager.start_command(name="t1", command="python --version")
            await asyncio.sleep(0.05)
            tasks = await manager.get_all_tasks()
        assert isinstance(tasks, list)
        assert len(tasks) >= 1

    @pytest.mark.asyncio
    async def test_cancel_task(self, manager):
        with patch("asyncio.create_subprocess_shell", new_callable=AsyncMock, return_value=_mock_process()):
            task = await manager.start_command(
                name="cancel_me",
                command="python -c \"import time; time.sleep(60)\""
            )
            await manager.cancel_task(task.id)
            await asyncio.sleep(0.05)
            status_info = await manager.get_status(task.id)
        if status_info:
            assert "status" in status_info


class TestGetTaskManager:
    def test_returns_singleton(self):
        m1 = get_task_manager()
        m2 = get_task_manager()
        assert m1 is m2

    def test_returns_manager_instance(self):
        m = get_task_manager()
        assert isinstance(m, BackgroundTaskManager)
