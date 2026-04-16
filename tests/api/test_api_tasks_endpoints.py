import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tests._server_compat import server_module
from src.runtime.task_orchestrator import TaskOrchestrator


@pytest.mark.asyncio
async def test_task_endpoints_start_get_cancel_and_session(monkeypatch):
    orchestrator = TaskOrchestrator()
    monkeypatch.setattr(server_module, "TASK_ORCHESTRATOR_V1_ENABLED", True)
    monkeypatch.setattr(server_module, "_TASK_ORCHESTRATOR", orchestrator)

    start_payload = await server_module.start_task(
        server_module.TaskStartRequest(
            conversation_id="conv_api_tasks",
            channel="ide",
            message_preview="create app",
            metadata={"request_id": "req_1"},
            task_id="task_manual",
        )
    )
    assert start_payload["success"] is True
    task_id = start_payload["task"]["task_id"]
    assert task_id == "task_manual"

    get_payload = await server_module.get_task(task_id)
    assert get_payload["success"] is True
    assert get_payload["task"]["conversation_id"] == "conv_api_tasks"

    session_payload = await server_module.get_session("conv_api_tasks", limit=10)
    assert session_payload["count"] == 1
    assert session_payload["tasks"][0]["task_id"] == task_id

    cancel_response = await server_module.cancel_task(task_id)
    cancel_payload = json.loads(cancel_response.body.decode("utf-8"))
    assert cancel_payload["success"] is True
    assert cancel_payload["task"]["state"] == "cancelled"

    resume_response = await server_module.resume_task(task_id)
    resume_payload = json.loads(resume_response.body.decode("utf-8"))
    assert resume_payload["success"] is False


def test_get_task_meta_reflects_orchestrator_stats(monkeypatch):
    orchestrator = TaskOrchestrator()
    task = orchestrator.start_task(
        conversation_id="conv_meta",
        channel="web",
        message_preview="hello",
    )
    orchestrator.mark_done(task.task_id, result_summary="ok")

    monkeypatch.setattr(server_module, "TASK_ORCHESTRATOR_V1_ENABLED", True)
    monkeypatch.setattr(server_module, "_TASK_ORCHESTRATOR", orchestrator)

    payload = server_module._get_task_meta()
    assert payload["tasks_enabled"] is True
    assert payload["tasks_total"] == 1
    assert payload["tasks_done"] == 1
    assert payload["tasks_waiting_io"] == 0


def test_task_resume_from_waiting_io():
    orchestrator = TaskOrchestrator()
    task = orchestrator.start_task(
        conversation_id="conv_resume",
        channel="ide",
        message_preview="long operation",
    )
    orchestrator.mark_waiting_io(task.task_id, "timeout")
    payload = orchestrator.resume_task(task.task_id)
    assert payload["success"] is True
    assert payload["task"]["state"] == "running"


def test_task_waiting_io_checkpoint_is_preserved_across_resume():
    orchestrator = TaskOrchestrator()
    task = orchestrator.start_task(
        conversation_id="conv_resume_checkpoint",
        channel="ide",
        message_preview="clarify and resume",
    )

    checkpoint = {
        "phase": "clarify_waiting_io",
        "clarification_question": "Quel niveau de detail souhaites-tu ?",
    }
    orchestrator.mark_waiting_io(task.task_id, "clarification_required", checkpoint=checkpoint)

    waiting_payload = orchestrator.get_task(task.task_id)
    assert waiting_payload is not None
    assert waiting_payload["state"] == "waiting_io"
    assert waiting_payload["last_checkpoint"] == checkpoint

    resumed = orchestrator.resume_task(task.task_id)
    assert resumed["success"] is True
    assert resumed["task"]["state"] == "running"

    resumed_payload = orchestrator.get_task(task.task_id)
    assert resumed_payload is not None
    assert resumed_payload["last_checkpoint"] == checkpoint
