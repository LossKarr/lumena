from src.runtime.mission_steering import fanout_command, fanout_to_new_worker
from src.runtime.steering_dispatcher import SteeringDispatcher
from src.runtime.task_orchestrator import TaskOrchestrator
from src.runtime.task_steering_store import TaskSteeringStore


def _mission(orch, objective, *, parent=None, files=None, state="running"):
    record = orch.start_task(
        conversation_id="__missions__", channel="mission", message_preview=objective,
        metadata={
            "kind": "mission", "objective": objective, "initial_objective": objective,
            "owner_user_id": "local:owner", "parent_id": parent,
            "allowed_files": files or [],
        },
    )
    orch.update_state(record.task_id, state)
    return record.task_id


def test_global_orientation_fans_out_to_all_active_workers_only_once():
    orch = TaskOrchestrator(persistence_path=None)
    lead = _mission(orch, "lead")
    first = _mission(orch, "first", parent=lead, files=["a.py"])
    second = _mission(orch, "second", parent=lead, files=["b.py"])
    _mission(orch, "done", parent=lead, files=["c.py"], state="done")
    command = SteeringDispatcher(orch).enqueue(lead, "add_constraint", text="ajoute les tests")
    first_run = fanout_command(orch, lead, command)
    second_run = fanout_command(orch, lead, command)
    assert first_run["worker_count"] == second_run["worker_count"] == 2
    assert len(TaskSteeringStore(orch).list(first)) == 1
    assert len(TaskSteeringStore(orch).list(second)) == 1


def test_file_scope_routes_to_owner_and_unknown_file_creates_review_amendment():
    orch = TaskOrchestrator(persistence_path=None)
    lead = _mission(orch, "lead")
    owner = _mission(orch, "owner", parent=lead, files=["src/a.py"])
    other = _mission(orch, "other", parent=lead, files=["src/b.py"])
    command = SteeringDispatcher(orch).enqueue(
        lead, "add_constraint", text="change ces fichiers",
        scope={"files": ["src/a.py", "src/new.py"]},
    )
    result = fanout_command(orch, lead, command)
    assert result["worker_count"] == 1
    assert len(TaskSteeringStore(orch).list(owner)) == 1
    assert TaskSteeringStore(orch).list(other) == []
    assert result["amendment"]["requested_files"] == ["src/new.py"]
    assert result["amendment"]["status"] == "pending_review"
    assert "src/new.py" not in (orch.get_task(owner)["metadata"].get("allowed_files") or [])


def test_new_worker_receives_active_consolidated_orientation():
    orch = TaskOrchestrator(persistence_path=None)
    lead = _mission(orch, "lead")
    command = SteeringDispatcher(orch).enqueue(lead, "add_constraint", text="reste compatible")
    worker = _mission(orch, "late worker", parent=lead)
    created = fanout_to_new_worker(orch, lead, worker)
    assert len(created) == 1
    child_command = TaskSteeringStore(orch).list(worker)[0]
    assert child_command["parent_command_id"] == command["command_id"]
    assert child_command["root_task_id"] == lead
