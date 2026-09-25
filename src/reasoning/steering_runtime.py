"""Small ReAct-facing hook for safe steering checkpoints."""
from __future__ import annotations

import asyncio
from typing import Any, List, Tuple

from src.runtime.steering_reconciliation import (
    build_effective_work_brief,
    incorporate_delivered,
    reconcile_final_outcomes,
)
from src.runtime.task_steering import acknowledge_control, consume_text_steering


async def apply_steering_checkpoint(orchestrator: Any, task_id: str) -> Tuple[str, List[str]]:
    from src.runtime.steering_dispatcher import SIGNALS
    SIGNALS.clear(task_id)
    from src.runtime.task_steering_store import TaskSteeringStore
    TaskSteeringStore(orchestrator).recover_orphaned_deliveries(task_id)
    record = orchestrator.get_task(task_id) or {}
    metadata = record.get("metadata") or {}
    if metadata.get("pause_requested"):
        orchestrator.set_task_metadata(task_id, paused=True)
        acknowledge_control(orchestrator, task_id, "pause")
        while True:
            if orchestrator.is_cancel_requested(task_id):
                raise SystemExit("task_orchestrator_cancel")
            paused_record = orchestrator.get_task(task_id) or {}
            if not ((paused_record.get("metadata") or {}).get("pause_requested")):
                orchestrator.set_task_metadata(task_id, paused=False)
                break
            await asyncio.sleep(0.2)
    _delivered_text, command_ids = consume_text_steering(orchestrator, task_id)
    if not command_ids:
        return "", []
    brief = build_effective_work_brief(orchestrator, task_id, command_ids)
    incorporate_delivered(orchestrator, task_id, command_ids)
    return brief, command_ids


async def reconcile_before_final(
    orchestrator: Any,
    task_id: str,
    answer: str,
) -> str:
    """Return a retry brief if steering arrived during the last LLM call."""
    brief, command_ids = await apply_steering_checkpoint(orchestrator, task_id)
    if command_ids:
        return brief
    reconcile_final_outcomes(orchestrator, task_id, answer)
    return ""
