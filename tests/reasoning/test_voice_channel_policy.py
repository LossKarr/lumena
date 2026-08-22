from pathlib import Path

import pytest

from src.reasoning.tool_registry import ToolRegistry
from src.runtime.context import RuntimeContext, pop_runtime_context, push_runtime_context
from src.runtime.voice_security import get_voice_confirmation_broker


def _ctx(workspace: Path, *, channel: str, role: str, conversation: str = "voice-test"):
    return RuntimeContext.build(
        channel=channel, client="test", request_id=None,
        conversation_id=conversation, message_id=None,
        workspace_policy="default", task_id=None, client_caps={},
        workspace_path=str(workspace), active_file_path=None, open_files=[],
        resolved_workspace=str(workspace), resolved_date=None,
        resolution_reason="test", user_id=f"{channel}:{role}",
        owner_user_id="local:owner", user_role=role,
        mode="agent",
    )


@pytest.mark.asyncio
async def test_unpaired_voice_can_read_but_cannot_write(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("contenu", encoding="utf-8")
    monkeypatch.setenv("LUMENA_DEFAULT_WORKSPACE", str(workspace))
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    token = push_runtime_context(_ctx(workspace, channel="voice", role="guest"))
    try:
        read_obs = await registry.execute("read_file", {"path": "note.txt"})
        write_obs = await registry.execute(
            "write_file", {"path": "blocked.txt", "content": "non"}
        )
    finally:
        pop_runtime_context(token)
    assert read_obs.success is True
    assert "contenu" in read_obs.content
    assert write_obs.success is False
    assert "n'est pas appairée comme owner" in write_obs.content
    assert not (workspace / "blocked.txt").exists()


@pytest.mark.asyncio
async def test_voice_policy_does_not_change_other_channels(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("LUMENA_DEFAULT_WORKSPACE", str(workspace))
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    token = push_runtime_context(_ctx(workspace, channel="web", role="guest"))
    try:
        obs = await registry.execute(
            "write_file", {"path": "web.txt", "content": "historique"}
        )
    finally:
        pop_runtime_context(token)
    assert "session vocale" not in obs.content.lower()


@pytest.mark.asyncio
async def test_owner_voice_critical_action_needs_exact_screen_authorization(tmp_path, monkeypatch):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "delete-me.txt"
    target.write_text("x", encoding="utf-8")
    monkeypatch.setenv("LUMENA_DEFAULT_WORKSPACE", str(workspace))
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    args = {"path": "delete-me.txt"}
    broker = get_voice_confirmation_broker()
    broker.clear()
    token = push_runtime_context(_ctx(workspace, channel="voice", role="owner"))
    try:
        refused = await registry.execute("delete_file", args)
        assert refused.success is False
        assert "Confirmation écran requise" in refused.content
        assert target.exists()

        broker.authorize(
            conversation_id="voice-test", tool_name="delete_file",
            arguments=args, ttl_s=30,
        )
        accepted = await registry.execute("delete_file", args)
    finally:
        pop_runtime_context(token)
        broker.clear()
    assert "Confirmation écran requise" not in accepted.content
    assert not target.exists()
