import pytest

from src.reasoning.steering_runtime import apply_steering_checkpoint, reconcile_before_final
from src.runtime.task_orchestrator import TaskOrchestrator
from src.runtime.task_steering_store import TaskSteeringStore


def _task():
    orchestrator = TaskOrchestrator(persistence_path=None)
    record = orchestrator.start_task(
        conversation_id="conv", channel="web", message_preview="construis le panneau",
        metadata={"kind": "agent_turn", "initial_objective": "construis le panneau"},
    )
    orchestrator.mark_running(record.task_id)
    return orchestrator, record.task_id


@pytest.mark.asyncio
async def test_checkpoint_preserves_initial_objective_and_incorporates_multiple_messages():
    orchestrator, task_id = _task()
    store = TaskSteeringStore(orchestrator)
    store.enqueue(task_id, "add_constraint", text="garde le menu")
    store.enqueue(task_id, "add_constraint", text="ajoute une recherche")
    brief, ids = await apply_steering_checkpoint(orchestrator, task_id)
    assert len(ids) == 2
    assert "construis le panneau" in brief
    assert brief.index("garde le menu") < brief.index("ajoute une recherche")
    assert all(item["status"] == "incorporated" for item in store.list(task_id))
    assert await apply_steering_checkpoint(orchestrator, task_id) == ("", [])


@pytest.mark.asyncio
async def test_final_reconciliation_is_conservative_without_effect_proof():
    orchestrator, task_id = _task()
    store = TaskSteeringStore(orchestrator)
    command = store.enqueue(task_id, "add_constraint", text="reste transparent")
    await apply_steering_checkpoint(orchestrator, task_id)
    assert await reconcile_before_final(orchestrator, task_id, "Termine.") == ""
    outcome = store.get(task_id, command["command_id"])
    assert outcome["status"] == "partially_applied"
    assert outcome["outcome"]["evidence_kind"] == "final_reconciliation"


@pytest.mark.asyncio
async def test_orientation_arriving_during_llm_blocks_that_final_once():
    orchestrator, task_id = _task()
    store = TaskSteeringStore(orchestrator)
    store.enqueue(task_id, "add_constraint", text="ajoute le filtre")
    retry = await reconcile_before_final(orchestrator, task_id, "Ancienne conclusion")
    assert "ajoute le filtre" in retry
    assert store.list(task_id)[0]["status"] == "incorporated"


@pytest.mark.asyncio
async def test_cancel_while_paused_still_escapes_through_system_exit():
    orchestrator, task_id = _task()
    orchestrator.set_task_metadata(task_id, pause_requested=True)
    orchestrator.cancel_task(task_id)
    with pytest.raises(SystemExit, match="task_orchestrator_cancel"):
        await apply_steering_checkpoint(orchestrator, task_id)
