from pathlib import Path

from src.telemetry.file_edits import FileEditsStore


def test_file_edits_store_undo_session_restores_existing_file(tmp_path: Path):
    target = tmp_path / "demo.py"
    before = "print('before')\n"
    after = "print('after')\n"
    target.write_text(before, encoding="utf-8")

    store = FileEditsStore(enabled=True, undo_enabled=True, max_sessions=20)
    session_id = store.start_edit_session(trace_id="trace-a", turn_id="turn-a")
    assert session_id

    target.write_text(after, encoding="utf-8")
    store.record_edit(
        trace_id="trace-a",
        turn_id="turn-a",
        task_id="task-a",
        tool_name="edit_file",
        action="edited",
        file_path=str(target),
        workspace_relative="demo.py",
        before_content=before,
        after_content=after,
        existed_before=True,
        summary="edited demo.py",
    )

    consumed = store.consume_session_edits("trace-a")
    assert len(consumed) == 1
    assert consumed[0]["action"] == "edited"
    assert consumed[0]["task_id"] == "task-a"

    undo = store.undo_session(session_id)
    assert undo["success"] is True
    assert target.read_text(encoding="utf-8") == before


def test_file_edits_store_undo_session_deletes_created_file(tmp_path: Path):
    target = tmp_path / "created.txt"
    after = "new file\n"

    store = FileEditsStore(enabled=True, undo_enabled=True, max_sessions=20)
    session_id = store.start_edit_session(trace_id="trace-b", turn_id="turn-b")
    assert session_id

    target.write_text(after, encoding="utf-8")
    store.record_edit(
        trace_id="trace-b",
        turn_id="turn-b",
        task_id="task-b",
        tool_name="write_file",
        action="created",
        file_path=str(target),
        workspace_relative="created.txt",
        before_content=None,
        after_content=after,
        existed_before=False,
        summary="created created.txt",
    )

    undo = store.undo_session(session_id)
    assert undo["success"] is True
    assert not target.exists()
