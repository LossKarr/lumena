from src.runtime.context import (
    RuntimeContext,
    get_current_runtime_context,
    get_current_runtime_context_dict,
    pop_runtime_context,
    push_runtime_context,
)


def test_runtime_context_build_generates_identifiers():
    ctx = RuntimeContext.build(
        channel="ide",
        client="cursor-ide-local",
        request_id=None,
        conversation_id=None,
        message_id=None,
        workspace_policy="strict_default",
        task_id=None,
        client_caps={"sse": True},
        workspace_path=None,
        active_file_path=None,
        open_files=[],
        resolved_workspace="C:/tmp/workspace/2026-02-15",
        resolved_date="2026-02-15",
        resolution_reason="strict_default_forced",
    )

    assert ctx.request_id.startswith("req_")
    assert ctx.conversation_id.startswith("conv_")
    assert ctx.message_id.startswith("msg_")
    assert ctx.workspace_policy == "strict_default"
    assert ctx.client_caps["sse"] is True


def test_runtime_context_push_pop_roundtrip():
    ctx = RuntimeContext.build(
        channel="web",
        client="browser",
        request_id="req_manual",
        conversation_id="conv_manual",
        message_id="msg_manual",
        workspace_policy="default",
        task_id="task_123",
        client_caps={},
        workspace_path=None,
        active_file_path=None,
        open_files=[],
        resolved_workspace="C:/tmp/workspace/2026-02-15",
        resolved_date="2026-02-15",
        resolution_reason="default_fallback_default_workspace",
    )

    token = push_runtime_context(ctx)
    try:
        current = get_current_runtime_context()
        assert current is not None
        assert current.task_id == "task_123"
        as_dict = get_current_runtime_context_dict()
        assert as_dict["conversation_id"] == "conv_manual"
    finally:
        pop_runtime_context(token)

    assert get_current_runtime_context() is None
