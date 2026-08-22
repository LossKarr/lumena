from src.runtime.task_orchestrator import TaskOrchestrator
from src.runtime.task_steering import (
    acknowledge_control,
    consume_text_steering,
    queue_steering,
)
from src.voice.v2.work_registry import (
    ActiveWorkRegistry, WorkNotificationTracker, classify_work_turn,
)


def _task(orch, objective="analyse le projet", *, kind="mission"):
    record = orch.start_task(
        conversation_id="voice-conv", channel="voice", message_preview=objective,
        metadata={"kind": kind, "objective": objective},
    )
    orch.mark_running(record.task_id)
    return record.task_id


def test_work_intent_router_is_deterministic():
    assert classify_work_turn("tu en es où ?") == "status"
    assert classify_work_turn("mets la tâche en pause") == "pause"
    assert classify_work_turn("reprends la tâche") == "resume"
    assert classify_work_turn("change plutôt la couleur en vert") == "steer"
    assert classify_work_turn("comment vas-tu ?") == "conversation"


def test_snapshot_is_read_only_projection_of_orchestrator():
    orch = TaskOrchestrator(persistence_path=None)
    lead = _task(orch)
    child = orch.start_task(
        conversation_id="__missions__", channel="mission", message_preview="worker",
        metadata={"kind": "mission", "parent_id": lead},
    )
    orch.mark_done(child.task_id, "ok")
    orch.set_task_metadata(lead, artifacts=["rapport.md"])
    orch.mark_checkpoint(lead, {"phase": "integration"})
    snap = ActiveWorkRegistry(orch).snapshot(lead)
    assert snap.completed_workers == 1
    assert snap.artifacts == ("rapport.md",)
    assert snap.last_phase == "integration"


def test_status_never_runs_an_agent_and_handles_ambiguity():
    orch = TaskOrchestrator(persistence_path=None)
    one = _task(orch, "premier")
    registry = ActiveWorkRegistry(orch)
    assert "running" in registry.status_text(one)
    _task(orch, "deuxieme")
    assert "2 travaux actifs" in registry.status_text(None)


def test_text_steering_is_persistent_and_consumed_once():
    orch = TaskOrchestrator(persistence_path=None)
    task_id = _task(orch)
    command = queue_steering(
        orch, task_id, "add_constraint", {"text": "ne touche plus a config.py"},
    )
    text, ids = consume_text_steering(orch, task_id)
    assert "config.py" in text
    assert ids == [command["command_id"]]
    assert consume_text_steering(orch, task_id) == ("", [])
    persisted = orch.get_task(task_id)["metadata"]["steering_commands"][0]
    assert persisted["status"] == "applied" and persisted["applied_at"]


def test_pause_resume_protocol_uses_metadata_not_fake_state():
    orch = TaskOrchestrator(persistence_path=None)
    task_id = _task(orch)
    queue_steering(orch, task_id, "pause")
    rec = orch.get_task(task_id)
    assert rec["state"] == "running"
    assert rec["metadata"]["pause_requested"] is True
    assert acknowledge_control(orch, task_id, "pause")
    queue_steering(orch, task_id, "resume")
    rec = orch.get_task(task_id)
    assert rec["metadata"]["pause_requested"] is False
    assert rec["metadata"]["paused"] is False


def test_mission_notifications_are_voice_scoped_and_deduplicated():
    orch = TaskOrchestrator(persistence_path=None)
    rec = orch.start_task(
        conversation_id="__missions__", channel="mission", message_preview="mission",
        metadata={
            "kind": "mission", "source_channel": "voice",
            "source_conversation_id": "voice-a",
        },
    )
    tracker = WorkNotificationTracker(orch, "voice-a")
    assert tracker.collect() == []
    orch.mark_running(rec.task_id)
    assert tracker.collect() == []
    orch.mark_done(rec.task_id, "ok")
    notices = tracker.collect()
    assert len(notices) == 1 and "terminée" in notices[0]
    assert tracker.collect() == []


def test_mission_notification_never_leaks_other_voice_conversation():
    orch = TaskOrchestrator(persistence_path=None)
    rec = orch.start_task(
        conversation_id="__missions__", channel="mission", message_preview="other",
        metadata={
            "kind": "mission", "source_channel": "voice",
            "source_conversation_id": "voice-b",
        },
    )
    tracker = WorkNotificationTracker(orch, "voice-a")
    orch.mark_done(rec.task_id, "ok")
    assert tracker.collect() == []
