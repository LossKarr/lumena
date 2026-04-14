from src.runtime.task_orchestrator import TaskOrchestrator


def test_task_orchestrator_lifecycle_and_stats():
    orchestrator = TaskOrchestrator()

    task = orchestrator.start_task(
        conversation_id="conv_1",
        channel="ide",
        message_preview="create project",
        metadata={"request_id": "req_1"},
    )
    assert task.state == "queued"

    orchestrator.mark_running(task.task_id)
    orchestrator.mark_checkpoint(task.task_id, {"phase": "editing"})
    done = orchestrator.mark_done(task.task_id, result_summary="ok")
    assert done is not None
    assert done.state == "done"
    assert done.result_summary == "ok"

    stats = orchestrator.stats()
    assert stats["total_tasks"] == 1
    assert stats["done_tasks"] == 1
    assert stats["backlog_tasks"] == 0
    assert stats["checkpoints_live"] == 1
    assert stats["checkpoints_compacted_total"] == 0


def test_task_orchestrator_cancel_marks_cancelled():
    orchestrator = TaskOrchestrator()
    task = orchestrator.start_task(
        conversation_id="conv_2",
        channel="web",
        message_preview="long run",
    )
    orchestrator.mark_running(task.task_id)
    payload = orchestrator.cancel_task(task.task_id)

    assert payload["success"] is True
    assert payload["task"]["state"] == "cancelled"
    assert orchestrator.is_cancel_requested(task.task_id) is True


def test_task_orchestrator_persistence_roundtrip(tmp_path):
    state_path = tmp_path / "task_state.json"
    orchestrator = TaskOrchestrator(persistence_path=state_path)

    task = orchestrator.start_task(
        conversation_id="conv_persist",
        channel="telegram",
        message_preview="persist me",
        metadata={"request_id": "req_persist"},
    )
    orchestrator.mark_running(task.task_id)
    orchestrator.mark_checkpoint(task.task_id, {"phase": "checkpointed"})
    orchestrator.mark_done(task.task_id, result_summary="done_persisted")

    assert state_path.exists()

    restored = TaskOrchestrator(persistence_path=state_path)
    payload = restored.get_task(task.task_id)
    assert payload is not None
    assert payload["state"] == "done"
    assert payload["last_checkpoint"] == {"phase": "checkpointed"}
    assert len(payload["checkpoint_history"]) == 1
    assert payload["result_summary"] == "done_persisted"

    stats = restored.stats()
    assert stats["total_tasks"] == 1
    assert stats["done_tasks"] == 1
    assert stats["persistence_enabled"] is True
    assert stats["persistence_last_error"] is None


def test_task_orchestrator_checkpoint_compaction_retention():
    orchestrator = TaskOrchestrator(
        checkpoint_history_max=3,
        checkpoint_compact_min_drop=1,
    )
    task = orchestrator.start_task(
        conversation_id="conv_compact",
        channel="ide",
        message_preview="compact checkpoints",
    )

    for index in range(6):
        orchestrator.mark_checkpoint(
            task.task_id,
            {"phase": f"phase_{index}", "step": index},
        )

    payload = orchestrator.get_task(task.task_id)
    assert payload is not None
    assert payload["last_checkpoint"] == {"phase": "phase_5", "step": 5}
    assert len(payload["checkpoint_history"]) == 3
    assert [item["payload"]["step"] for item in payload["checkpoint_history"]] == [3, 4, 5]

    compaction = payload["checkpoint_compaction"]
    assert compaction["compacted_total"] == 3
    assert compaction["compacted_by_phase"]["phase_0"] == 1
    assert compaction["compacted_by_phase"]["phase_1"] == 1
    assert compaction["compacted_by_phase"]["phase_2"] == 1

    stats = orchestrator.stats()
    assert stats["checkpoint_history_max"] == 3
    assert stats["checkpoints_live"] == 3
    assert stats["checkpoints_compacted_total"] == 3


def test_task_orchestrator_checkpoint_compaction_persisted(tmp_path):
    state_path = tmp_path / "task_state_compact.json"
    orchestrator = TaskOrchestrator(
        persistence_path=state_path,
        checkpoint_history_max=2,
        checkpoint_compact_min_drop=1,
    )
    task = orchestrator.start_task(
        conversation_id="conv_compact_persist",
        channel="telegram",
        message_preview="persist compaction",
    )
    for index in range(4):
        orchestrator.mark_checkpoint(task.task_id, {"phase": "io", "step": index})

    restored = TaskOrchestrator(
        persistence_path=state_path,
        checkpoint_history_max=2,
        checkpoint_compact_min_drop=1,
    )
    payload = restored.get_task(task.task_id)
    assert payload is not None
    assert [item["payload"]["step"] for item in payload["checkpoint_history"]] == [2, 3]
    assert payload["checkpoint_compaction"]["compacted_total"] == 2
    assert payload["checkpoint_compaction"]["compacted_by_phase"]["io"] == 2


def test_task_orchestrator_persistence_corrupted_file(tmp_path):
    state_path = tmp_path / "task_state_broken.json"
    state_path.write_text("{broken", encoding="utf-8")

    orchestrator = TaskOrchestrator(persistence_path=state_path)
    stats = orchestrator.stats()

    assert stats["total_tasks"] == 0
    assert stats["persistence_enabled"] is True
    assert stats["persistence_last_error"]
