from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from src.llm.codex_collaboration import (
    CodexCollaborationRegistry,
    CodexCollaborationService,
    CodexShareMode,
    CollaborationLink,
    WorkspaceWriterLease,
    build_handoff,
    normalise_thread_summary,
    sanitise_thread_for_ui,
)


class FakeSupervisor:
    def __init__(self):
        self.is_running = True
        self.requests: list[tuple[str, dict, float | None]] = []
        self.thread = {
            "id": "thr-1",
            "cwd": "",
            "name": "Corriger Lumena",
            "preview": "Audit de regression",
            "status": {"type": "notLoaded"},
            "turns": [],
        }

    async def request(self, method, params=None, *, timeout=None):
        params = dict(params or {})
        self.requests.append((method, params, timeout))
        if method == "thread/list":
            return {"data": [self.thread], "nextCursor": "next-page"}
        if method == "thread/read":
            return {"thread": self.thread}
        if method == "thread/loaded/list":
            return {"data": ["thr-1"]}
        if method == "thread/resume":
            return {"thread": {"id": params["threadId"]}}
        if method == "turn/start":
            return {"turn": {"id": "turn-1", "status": "inProgress"}}
        if method == "turn/steer":
            return {"turnId": params["expectedTurnId"]}
        if method == "turn/interrupt":
            return {}
        if method == "thread/fork":
            return {"thread": {"id": "fork-1", "ephemeral": True}}
        raise AssertionError(f"unexpected method: {method}")


def _service(tmp_path: Path, *, mode=CodexShareMode.SELECTED):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    supervisor = FakeSupervisor()
    supervisor.thread["cwd"] = str(workspace.resolve())
    registry = CodexCollaborationRegistry(tmp_path / "collaboration.json")
    registry.set_share_mode(mode)
    service = CodexCollaborationService(
        supervisor,
        registry=registry,
        lock_dir=tmp_path / "locks",
    )
    return workspace, supervisor, registry, service


def test_thread_summary_exposes_waiting_approval_without_hidden_fields():
    summary = normalise_thread_summary(
        {
            "id": "thr",
            "cwd": "C:/repo",
            "preview": "Fix tests",
            "status": {
                "type": "active",
                "activeFlags": ["waitingOnApproval"],
            },
            "reasoning": "must stay hidden",
        }
    )
    assert summary is not None
    assert summary.waiting_on_approval is True
    assert "reasoning" not in summary.__dict__


@pytest.mark.asyncio
async def test_share_none_does_not_call_app_server(tmp_path):
    workspace, supervisor, _, service = _service(tmp_path, mode=CodexShareMode.NONE)
    threads, cursor = await service.discover_threads(workspace)
    assert threads == ()
    assert cursor == ""
    assert supervisor.requests == []


@pytest.mark.asyncio
async def test_discovery_is_workspace_filtered_non_archived_and_paginated(tmp_path):
    workspace, supervisor, _, service = _service(tmp_path)
    threads, cursor = await service.discover_threads(
        workspace, cursor="cursor-1", limit=300
    )
    assert [item.thread_id for item in threads] == ["thr-1"]
    assert cursor == "next-page"
    method, params, _ = supervisor.requests[-1]
    assert method == "thread/list"
    assert params["cwd"] == str(workspace.resolve())
    assert params["archived"] is False
    assert params["limit"] == 100
    assert "appServer" in params["sourceKinds"]


@pytest.mark.asyncio
async def test_link_and_dissociate_are_idempotent_and_never_delete_thread(tmp_path):
    workspace, supervisor, registry, service = _service(tmp_path)
    first = await service.link("thr-1", workspace)
    calls_after_first = len(supervisor.requests)
    second = await service.link("thr-1", workspace)
    assert first.thread_id == second.thread_id
    assert len(supervisor.requests) == calls_after_first
    assert service.dissociate("thr-1") is True
    assert service.dissociate("thr-1") is False
    assert registry.get("thr-1") is None
    assert all(method != "thread/delete" for method, _, _ in supervisor.requests)


@pytest.mark.asyncio
async def test_selected_mode_blocks_detailed_read_until_linked(tmp_path):
    workspace, supervisor, _, service = _service(tmp_path)
    with pytest.raises(PermissionError, match="liee explicitement"):
        await service.read_thread("thr-1", workspace)
    assert supervisor.requests == []
    await service.link("thr-1", workspace)
    payload = await service.read_thread("thr-1", workspace)
    assert payload["id"] == "thr-1"
    assert supervisor.requests[-1][1]["includeTurns"] is True


@pytest.mark.asyncio
async def test_other_workspace_is_rejected_on_link_and_read(tmp_path):
    workspace, supervisor, registry, service = _service(tmp_path)
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(PermissionError, match="autre workspace"):
        await service.link("thr-1", other)
    registry.put(CollaborationLink(thread_id="thr-1", workspace=str(workspace)))
    with pytest.raises(PermissionError, match="autre workspace"):
        await service.read_thread("thr-1", other)


@pytest.mark.asyncio
async def test_all_local_mode_links_using_the_thread_workspace(tmp_path):
    workspace, supervisor, registry, service = _service(
        tmp_path, mode=CodexShareMode.ALL_LOCAL
    )
    other = tmp_path / "other"
    other.mkdir()
    supervisor.thread["cwd"] = str(other.resolve())
    link = await service.link("thr-1", workspace)
    assert Path(link.workspace) == other.resolve()
    assert registry.get("thr-1") is not None


def test_handoff_is_structured_bounded_and_removes_reasoning_and_secrets(tmp_path):
    handoff = build_handoff(
        {
            "id": "thr-1",
            "name": "Audit",
            "reasoning": "hidden chain of thought",
            "turns": [
                {
                    "id": "turn-1",
                    "status": "completed",
                    "items": [
                        {
                            "type": "agentMessage",
                            "text": "Fix termine Bearer abcdefghijklmnop",
                        },
                        {
                            "type": "fileChange",
                            "changes": [{"path": "src/app.py"}],
                        },
                        {
                            "type": "commandExecution",
                            "command": "python -m pytest tests -q",
                            "exitCode": 0,
                        },
                        {"type": "reasoning", "text": "private"},
                    ],
                }
            ],
        },
        workspace=tmp_path,
    )
    dump = json.dumps(handoff.__dict__)
    assert handoff.files_touched == ("src/app.py",)
    assert handoff.tests == ("python -m pytest tests -q -> exit 0",)
    assert "[REDACTED]" in dump
    assert "hidden chain" not in dump
    assert "private" not in dump


def test_ui_history_excludes_reasoning_and_redacts_secrets():
    payload = sanitise_thread_for_ui(
        {
            "id": "thr-1",
            "turns": [
                {
                    "id": "turn-1",
                    "status": "completed",
                    "items": [
                        {"type": "userMessage", "text": "Corrige le test"},
                        {"type": "reasoning", "text": "secret reasoning"},
                        {
                            "type": "agentMessage",
                            "text": "Fini Bearer abcdefghijklmnop",
                        },
                    ],
                }
            ],
        }
    )
    dump = json.dumps(payload)
    assert "Corrige le test" in dump
    assert "[REDACTED]" in dump
    assert "secret reasoning" not in dump


@pytest.mark.asyncio
async def test_handoff_persists_only_summary_and_memory_is_opt_in(tmp_path):
    workspace, supervisor, registry, service = _service(tmp_path)
    supervisor.thread["turns"] = [
        {
            "status": "completed",
            "items": [{"type": "agentMessage", "text": "Travail fini"}],
        }
    ]
    await service.link("thr-1", workspace)
    handoff = await service.create_handoff("thr-1", workspace)
    link = registry.get("thr-1")
    assert handoff.completed == ("Travail fini",)
    assert link is not None and link.memory_approved is False
    persisted = json.loads(registry.path.read_text(encoding="utf-8"))
    assert "turns" not in json.dumps(persisted)


def test_workspace_writer_is_exclusive_across_processes(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    lock_dir = tmp_path / "locks"
    parent = WorkspaceWriterLease(workspace, actor="lumena", lock_dir=lock_dir)
    assert parent.acquire() is True
    code = (
        "from src.llm.codex_collaboration import WorkspaceWriterLease;"
        f"l=WorkspaceWriterLease({str(workspace)!r},actor='codex',lock_dir={str(lock_dir)!r});"
        "print(l.acquire())"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).parents[2],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    try:
        assert result.stdout.strip() == "False"
        assert "lumena" in parent.owner_info()["owner_id"]
    finally:
        parent.release()
    assert WorkspaceWriterLease(workspace, actor="codex", lock_dir=lock_dir).acquire()


@pytest.mark.asyncio
async def test_waiting_approval_is_never_auto_accepted(tmp_path):
    workspace, supervisor, registry, service = _service(tmp_path)
    registry.put(
        CollaborationLink(
            thread_id="thr-1",
            workspace=str(workspace),
            status="active",
            active_flags=("waitingOnApproval",),
        )
    )
    with pytest.raises(RuntimeError, match="aucune auto-acceptation"):
        await service.start_turn("thr-1", workspace, "Continue")
    assert all(method != "turn/start" for method, _, _ in supervisor.requests)


@pytest.mark.asyncio
async def test_review_turn_is_read_only_and_mutating_turn_holds_writer(tmp_path):
    workspace, supervisor, registry, service = _service(tmp_path)
    registry.put(CollaborationLink(thread_id="thr-1", workspace=str(workspace)))
    turn_id, lease = await service.start_turn(
        "thr-1", workspace, "Verifie les tests", write=False
    )
    assert turn_id == "turn-1"
    assert lease is None
    read_turn = next(params for method, params, _ in supervisor.requests if method == "turn/start")
    assert read_turn["sandboxPolicy"] == {
        "type": "readOnly",
        "networkAccess": False,
    }

    supervisor.requests.clear()
    turn_id, lease = await service.start_turn(
        "thr-1", workspace, "Corrige le bug", write=True
    )
    assert turn_id == "turn-1"
    assert lease is not None and lease.lock.is_acquired
    write_turn = next(params for method, params, _ in supervisor.requests if method == "turn/start")
    assert write_turn["sandboxPolicy"] == {
        "type": "workspaceWrite",
        "writableRoots": [str(workspace.resolve())],
        "networkAccess": False,
    }
    lease.release()


@pytest.mark.asyncio
async def test_steer_interrupt_loaded_and_ephemeral_fork_use_exact_contract(tmp_path):
    workspace, supervisor, registry, service = _service(tmp_path)
    registry.put(CollaborationLink(thread_id="thr-1", workspace=str(workspace)))
    assert await service.loaded_thread_ids() == ("thr-1",)
    assert await service.steer("thr-1", "turn-9", "Priorise les tests") == "turn-9"
    await service.interrupt("thr-1", "turn-9")
    assert await service.fork_ephemeral("thr-1") == "fork-1"
    calls = {method: params for method, params, _ in supervisor.requests}
    assert calls["turn/steer"]["expectedTurnId"] == "turn-9"
    assert calls["turn/interrupt"] == {"threadId": "thr-1", "turnId": "turn-9"}
    assert calls["thread/fork"] == {"threadId": "thr-1", "ephemeral": True}
