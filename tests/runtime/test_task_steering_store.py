from concurrent.futures import ThreadPoolExecutor

import pytest

from src.runtime.task_orchestrator import TaskOrchestrator
from src.runtime.task_steering_store import SteeringConflict, SteeringQueueFull, TaskSteeringStore


def _active_task(orchestrator: TaskOrchestrator) -> str:
    task = orchestrator.start_task(
        conversation_id="web-session",
        channel="web",
        message_preview="objectif complet",
        metadata={"kind": "agent_turn", "objective": "objectif complet"},
    )
    orchestrator.mark_running(task.task_id)
    return task.task_id


def test_concurrent_producers_receive_unique_monotonic_sequences():
    orchestrator = TaskOrchestrator(persistence_path=None)
    task_id = _active_task(orchestrator)
    store = TaskSteeringStore(orchestrator)

    def enqueue(index: int):
        return store.enqueue(task_id, "add_constraint", text=f"contrainte {index}")

    with ThreadPoolExecutor(max_workers=8) as pool:
        commands = list(pool.map(enqueue, range(40)))
    assert sorted(item["sequence"] for item in commands) == list(range(1, 41))
    assert len({item["command_id"] for item in commands}) == 40
    assert len(store.list(task_id)) == 40


def test_idempotency_replays_same_command_and_rejects_changed_payload():
    orchestrator = TaskOrchestrator(persistence_path=None)
    task_id = _active_task(orchestrator)
    store = TaskSteeringStore(orchestrator)
    first = store.enqueue(task_id, "add_constraint", text="garde le menu", idempotency_key="request-1")
    replay = store.enqueue(task_id, "add_constraint", text="garde le menu", idempotency_key="request-1")
    assert replay["command_id"] == first["command_id"]
    assert len(store.list(task_id)) == 1
    with pytest.raises(SteeringConflict, match="idempotency_conflict"):
        store.enqueue(task_id, "add_constraint", text="change le menu", idempotency_key="request-1")


def test_terminal_race_is_persisted_as_late_and_never_delivered():
    orchestrator = TaskOrchestrator(persistence_path=None)
    task_id = _active_task(orchestrator)
    orchestrator.mark_done(task_id, "termine")
    store = TaskSteeringStore(orchestrator)
    command = store.enqueue(task_id, "add_constraint", text="ajoute une recherche")
    assert command["status"] == "late"
    assert store.deliver_pending_text(task_id) == ("", [])


def test_delivery_is_distinct_from_proven_application_and_is_ordered():
    orchestrator = TaskOrchestrator(persistence_path=None)
    task_id = _active_task(orchestrator)
    store = TaskSteeringStore(orchestrator)
    normal = store.enqueue(task_id, "add_constraint", text="normale")
    urgent = store.enqueue(
        task_id, "add_constraint", text="urgente", delivery_policy="urgent_safe_boundary",
    )
    prompt, ids = store.deliver_pending_text(task_id)
    assert ids == [urgent["command_id"], normal["command_id"]]
    assert prompt.index("urgente") < prompt.index("normale")
    assert all(item["status"] == "delivered" for item in store.list(task_id))
    store.transition(task_id, urgent["command_id"], "incorporated", {"revision": 1})
    store.record_outcome(task_id, urgent["command_id"], "applied", {"proof_id": "proof-1"})
    assert store.get(task_id, urgent["command_id"])["status"] == "applied"


def test_compaction_only_removes_terminal_commands_and_never_active_ones():
    orchestrator = TaskOrchestrator(persistence_path=None)
    task_id = _active_task(orchestrator)
    store = TaskSteeringStore(orchestrator, max_commands=10)
    commands = [
        store.enqueue(task_id, "add_constraint", text=f"contrainte {index}")
        for index in range(10)
    ]
    with pytest.raises(SteeringQueueFull, match="steering_queue_full"):
        store.enqueue(task_id, "add_constraint", text="en trop")
    store.deliver_pending_text(task_id)
    store.record_outcome(task_id, commands[0]["command_id"], "applied", {"proof": True})
    store.enqueue(task_id, "add_constraint", text="nouvelle")
    remaining = store.list(task_id)
    assert len(remaining) == 10
    assert commands[0]["command_id"] not in {item["command_id"] for item in remaining}


def test_persistence_keeps_initial_objective_and_pending_commands(tmp_path):
    state = tmp_path / "tasks.json"
    first = TaskOrchestrator(persistence_path=state)
    task_id = _active_task(first)
    TaskSteeringStore(first).enqueue(task_id, "add_constraint", text="reste transparent")
    restarted = TaskOrchestrator(persistence_path=state)
    metadata = restarted.get_task(task_id)["metadata"]
    assert metadata["initial_objective"] == "objectif complet"
    assert metadata["steering_commands"][0]["status"] == "pending"
