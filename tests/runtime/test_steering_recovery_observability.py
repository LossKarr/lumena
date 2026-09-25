import json

from src.runtime.steering_observability import steering_metrics
from src.runtime.task_orchestrator import TaskOrchestrator
from src.runtime.task_steering_store import TaskSteeringStore


def _task(orch):
    record = orch.start_task(
        conversation_id="conv", channel="web", message_preview="objectif",
        metadata={"kind": "agent_turn", "owner_user_id": "local:owner"},
    )
    orch.mark_running(record.task_id)
    return record.task_id


def test_crash_after_delivery_is_recovered_and_redelivered_after_restart(tmp_path):
    state = tmp_path / "tasks.json"
    first = TaskOrchestrator(persistence_path=state)
    task_id = _task(first)
    store = TaskSteeringStore(first)
    command = store.enqueue(task_id, "add_constraint", text="secret orientation")
    assert store.deliver_pending_text(task_id)[1] == [command["command_id"]]
    restarted = TaskOrchestrator(persistence_path=state)
    recovered = TaskSteeringStore(restarted)
    assert recovered.recover_orphaned_deliveries(task_id) == [command["command_id"]]
    text, ids = recovered.deliver_pending_text(task_id)
    assert "secret orientation" in text and ids == [command["command_id"]]
    assert recovered.get(task_id, command["command_id"])["delivery"]["recovery_count"] == 1


def test_persisted_command_and_metrics_are_bounded_facts_without_log_payload(tmp_path):
    state = tmp_path / "tasks.json"
    orch = TaskOrchestrator(persistence_path=state)
    task_id = _task(orch)
    store = TaskSteeringStore(orch)
    store.enqueue(task_id, "add_constraint", text="TOKEN_SUPER_SECRET")
    metrics = steering_metrics(orch)
    assert metrics["commands_total"] == 1 and metrics["pending"] == 1
    # Persistence owns the authorized full text; observability metrics never copy it.
    assert "TOKEN_SUPER_SECRET" not in json.dumps(metrics)
