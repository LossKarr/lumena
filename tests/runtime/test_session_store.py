from src.runtime.session_store import SessionStore


def test_session_store_records_messages_and_lists_sessions(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite")

    store.record_message(
        conversation_id="conv_1",
        role="user",
        content="Analyse ce projet Lumena",
        channel="web",
        client="browser",
        request_id="req_1",
        task_id="task_1",
        trace_id="trace_1",
    )
    store.record_message(
        conversation_id="conv_1",
        role="assistant",
        content="Voici mon analyse.",
        channel="web",
        client="browser",
        model_used="gpt-test",
        provider_used="openai",
        status="done",
    )
    store.record_event(
        conversation_id="conv_1",
        event_type="response_sent",
        status="done",
        summary="Voici mon analyse.",
    )

    listed = store.list_sessions()
    assert listed["total"] == 1
    assert listed["sessions"][0]["conversation_id"] == "conv_1"
    assert listed["sessions"][0]["message_count"] == 2
    assert listed["sessions"][0]["last_model"] == "gpt-test"

    detail = store.get_session("conv_1")
    assert detail is not None
    assert detail["session"]["status"] == "done"
    assert [m["role"] for m in detail["messages"]] == ["user", "assistant"]
    assert detail["events"][0]["type"] == "response_sent"


def test_session_store_archive_and_delete(tmp_path):
    store = SessionStore(tmp_path / "sessions.sqlite")
    store.record_message(conversation_id="conv_2", role="user", content="hello")

    assert store.archive_session("conv_2") is True
    assert store.list_sessions()["total"] == 0
    assert store.list_sessions(include_archived=True)["total"] == 1

    assert store.delete_session("conv_2") is True
    assert store.get_session("conv_2") is None
