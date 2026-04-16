from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.core import LumenaCore
from src.core_services.identity_service import IdentityService
from src.core_services.contracts import ServiceContext
from src.runtime.context import RuntimeContext, pop_runtime_context, push_runtime_context


def _make_core_with_identity():
    core = LumenaCore.__new__(LumenaCore)
    ctx = ServiceContext.__new__(ServiceContext)
    core._identity_svc = IdentityService(ctx)
    return core


def test_core_runtime_context_overrides_default_web_channel_and_ide_context():
    core = _make_core_with_identity()

    ctx = RuntimeContext.build(
        channel="ide",
        client="cursor-ide-local",
        request_id="req_bridge",
        conversation_id="conv_bridge",
        message_id="msg_bridge",
        workspace_policy="default",
        task_id="task_bridge",
        client_caps={},
        workspace_path="C:/tmp/project",
        active_file_path="C:/tmp/project/main.py",
        open_files=["C:/tmp/project/main.py"],
        resolved_workspace="C:/tmp/project",
        resolved_date="2026-02-15",
        resolution_reason="test",
    )

    token = push_runtime_context(ctx)
    try:
        channel, ide_ctx = core._resolve_channel_and_ide_context("web", None)
    finally:
        pop_runtime_context(token)

    assert channel == "ide"
    assert ide_ctx["workspace_path"] == "C:/tmp/project"
    assert ide_ctx["active_file_path"] == "C:/tmp/project/main.py"
    assert ide_ctx["open_files"] == ["C:/tmp/project/main.py"]


def test_core_runtime_context_keeps_explicit_non_web_channel():
    core = _make_core_with_identity()

    ctx = RuntimeContext.build(
        channel="ide",
        client="cursor-ide-local",
        request_id="req_bridge_2",
        conversation_id="conv_bridge_2",
        message_id="msg_bridge_2",
        workspace_policy="default",
        task_id=None,
        client_caps={},
        workspace_path="C:/tmp/project2",
        active_file_path="",
        open_files=[],
        resolved_workspace="C:/tmp/project2",
        resolved_date="2026-02-15",
        resolution_reason="test",
    )

    token = push_runtime_context(ctx)
    try:
        channel, ide_ctx = core._resolve_channel_and_ide_context("telegram", {"workspace_path": "C:/explicit"})
    finally:
        pop_runtime_context(token)

    assert channel == "telegram"
    assert ide_ctx["workspace_path"] == "C:/explicit"
