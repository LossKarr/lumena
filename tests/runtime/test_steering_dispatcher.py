import asyncio

import pytest

from src.runtime.steering_dispatcher import (
    SIGNALS,
    STEERING_INTERRUPTED,
    SteeringDispatcher,
    await_interruptible_llm,
    execution_phase,
)
from src.runtime.task_orchestrator import TaskOrchestrator


def _task():
    orchestrator = TaskOrchestrator(persistence_path=None)
    record = orchestrator.start_task(
        conversation_id="conv", channel="web", message_preview="objectif",
        metadata={"kind": "agent_turn", "owner_user_id": "local:owner"},
    )
    orchestrator.mark_running(record.task_id)
    return orchestrator, record.task_id


@pytest.mark.asyncio
async def test_urgent_command_interrupts_only_the_llm_await():
    orchestrator, task_id = _task()
    cancelled = asyncio.Event()

    async def slow_llm():
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    pending = asyncio.create_task(
        await_interruptible_llm(slow_llm(), task_id=task_id, timeout=20),
    )
    await asyncio.sleep(0.06)
    command = SteeringDispatcher(orchestrator).enqueue(
        task_id, "add_constraint", text="urgent", delivery_policy="urgent_safe_boundary",
    )
    assert command["safe_boundary"] == "generation_interruptible"
    assert await asyncio.wait_for(pending, 2) is STEERING_INTERRUPTED
    assert cancelled.is_set()
    SIGNALS.clear(task_id)


def test_tool_phase_reports_after_current_operation_and_is_not_cancelled():
    orchestrator, task_id = _task()
    with execution_phase(task_id, "tool"):
        command = SteeringDispatcher(orchestrator).enqueue(
            task_id, "add_constraint", text="urgent", delivery_policy="urgent_safe_boundary",
        )
        assert command["safe_boundary"] == "after_current_operation"
        assert SIGNALS.is_set(task_id)
    SIGNALS.clear(task_id)


@pytest.mark.asyncio
async def test_normal_orientation_does_not_interrupt_llm():
    orchestrator, task_id = _task()

    async def quick_llm():
        await asyncio.sleep(0.08)
        return "ok"

    pending = asyncio.create_task(
        await_interruptible_llm(quick_llm(), task_id=task_id, timeout=2),
    )
    await asyncio.sleep(0.02)
    SteeringDispatcher(orchestrator).enqueue(task_id, "add_constraint", text="normal")
    assert await pending == "ok"
