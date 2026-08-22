from __future__ import annotations

import asyncio
from collections import deque
from pathlib import Path
import pytest

from src.llm.codex_app_server import (
    CodexNotification,
    attach_shared_codex_app_server,
    detach_shared_codex_app_server,
    get_shared_codex_app_server,
)
from src.llm.codex_codeagent import (
    run_codeagent_with_codex_subscription,
    should_route_codeagent_to_codex,
)
from src.llm.codex_subscription import (
    CodexSurface,
    CodexSubscriptionSettings,
    OpenAIAccessMode,
)


def _settings(*, enabled: bool = True, model: str = "codex-model"):
    return CodexSubscriptionSettings(
        access_mode=(
            OpenAIAccessMode.CHATGPT_CODEX if enabled else OpenAIAccessMode.API
        ),
        default_model=model,
        surfaces=frozenset({CodexSurface.CODEAGENT}),
    )


class FakeSupervisor:
    def __init__(self, mutate=None, notifications=()):
        self.is_running = True
        self.mutate = mutate
        self.notifications = deque(notifications)
        self.requests = []

    async def request(self, method, params=None, *, timeout=None):
        self.requests.append((method, params, timeout))
        if method == "account/read":
            return {"account": {"type": "chatgpt", "plan": "test"}}
        if method == "model/list":
            return {
                "models": [
                    {"id": "codex-model", "isDefault": True},
                    {"id": "other-model"},
                ]
            }
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "turn/start":
            if self.mutate:
                self.mutate(Path(params["cwd"]))
            return {"turn": {"id": "turn-1", "status": "inProgress"}}
        if method == "turn/interrupt":
            return {}
        raise AssertionError(f"unexpected method: {method}")

    async def next_notification(self, *, timeout=None):
        if not self.notifications:
            await asyncio.Event().wait()
        return self.notifications.popleft()


def _notification(method, params):
    return CodexNotification(method=method, params=params)


def _successful_notifications(*, green_test: bool = True):
    items = []
    if green_test:
        items.append(
            _notification(
                "item/completed",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "item": {
                        "type": "commandExecution",
                        "command": ["python", "-m", "pytest", "-q"],
                        "status": "completed",
                        "exitCode": 0,
                    },
                },
            )
        )
    items.extend(
        [
            _notification(
                "item/completed",
                {
                    "threadId": "thread-1",
                    "turnId": "turn-1",
                    "item": {
                        "type": "agentMessage",
                        "phase": "final_answer",
                        "text": "Implementation terminee et verifiee.",
                    },
                },
            ),
            _notification(
                "turn/completed",
                {
                    "threadId": "thread-1",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            ),
        ]
    )
    return items


def test_route_is_strictly_opt_in_and_codeagent_only():
    assert should_route_codeagent_to_codex("code", _settings()) is True
    assert should_route_codeagent_to_codex("debug", _settings()) is True
    assert should_route_codeagent_to_codex("general", _settings()) is False
    assert should_route_codeagent_to_codex("code", _settings(enabled=False)) is False


def test_shared_supervisor_never_returns_stale_instance():
    detach_shared_codex_app_server()
    supervisor = FakeSupervisor()
    attach_shared_codex_app_server(supervisor)
    assert get_shared_codex_app_server() is supervisor
    supervisor.is_running = False
    assert get_shared_codex_app_server() is None
    assert detach_shared_codex_app_server(supervisor) is True


@pytest.mark.asyncio
async def test_scoped_run_applies_only_allowed_changes_and_uses_official_payload(tmp_path):
    workspace = tmp_path / "project"
    (workspace / "tests").mkdir(parents=True)
    (workspace / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (workspace / "tests" / "test_app.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )

    def mutate(root: Path):
        (root / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

    supervisor = FakeSupervisor(mutate, _successful_notifications())
    result = await run_codeagent_with_codex_subscription(
        "Passe VALUE a 2 et teste.",
        agent_type="code",
        context={"user_original_request": "Corrige l'application"},
        workspace_path=workspace,
        allowed_files=["app.py"],
        settings=_settings(),
        supervisor=supervisor,
    )

    assert result.success is True
    assert (workspace / "app.py").read_text(encoding="utf-8") == "VALUE = 2\n"
    assert result.artifacts == [str((workspace / "app.py").resolve())]
    thread_params = next(p for m, p, _ in supervisor.requests if m == "thread/start")
    turn_params = next(p for m, p, _ in supervisor.requests if m == "turn/start")
    assert thread_params["approvalPolicy"] == "never"
    assert thread_params["sandbox"] == "workspace-write"
    assert thread_params["model"] == "codex-model"
    assert turn_params["sandboxPolicy"]["networkAccess"] is False
    assert turn_params["sandboxPolicy"]["writableRoots"] == [turn_params["cwd"]]
    assert turn_params["cwd"] != str(workspace)


@pytest.mark.asyncio
async def test_scope_violation_copies_nothing_back(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "app.py").write_text("VALUE = 1\n", encoding="utf-8")

    def mutate(root: Path):
        (root / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        (root / "evil.py").write_text("STOLEN = True\n", encoding="utf-8")

    supervisor = FakeSupervisor(mutate, _successful_notifications(green_test=False))
    result = await run_codeagent_with_codex_subscription(
        "Modifie app.py.",
        agent_type="code",
        context={},
        workspace_path=workspace,
        allowed_files=["app.py"],
        settings=_settings(),
        supervisor=supervisor,
    )

    assert result.success is False
    assert result.status_code == "scope_violation"
    assert (workspace / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert not (workspace / "evil.py").exists()


@pytest.mark.asyncio
async def test_existing_tests_require_green_command_proof(tmp_path):
    workspace = tmp_path / "project"
    (workspace / "tests").mkdir(parents=True)
    (workspace / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (workspace / "tests" / "test_app.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )

    def mutate(root: Path):
        (root / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

    supervisor = FakeSupervisor(mutate, _successful_notifications(green_test=False))
    result = await run_codeagent_with_codex_subscription(
        "Modifie app.py sans lancer les tests.",
        agent_type="code",
        context={},
        workspace_path=workspace,
        allowed_files=["app.py"],
        settings=_settings(),
        supervisor=supervisor,
    )
    assert result.success is False
    assert result.status_code == "validation_failed"
    assert "aucune execution verte" in result.output
    assert (workspace / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"


@pytest.mark.asyncio
async def test_cancellation_interrupts_the_exact_turn(tmp_path):
    workspace = tmp_path / "project"
    workspace.mkdir()
    supervisor = FakeSupervisor()
    task = asyncio.create_task(
        run_codeagent_with_codex_subscription(
            "Travail long.",
            agent_type="code",
            context={},
            workspace_path=workspace,
            allowed_files=None,
            settings=_settings(),
            supervisor=supervisor,
            timeout_s=30,
        )
    )
    for _ in range(100):
        if any(method == "turn/start" for method, _, _ in supervisor.requests):
            break
        await asyncio.sleep(0.001)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    interrupt = next(
        params for method, params, _ in supervisor.requests if method == "turn/interrupt"
    )
    assert interrupt == {"threadId": "thread-1", "turnId": "turn-1"}


@pytest.mark.asyncio
async def test_handler_fails_closed_when_subscription_selected_without_session(
    tmp_path, monkeypatch
):
    from src.reasoning.handlers.agents import delegate_task_handler
    from src.reasoning.handlers.context import HandlerContext

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    ctx = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=workspace)
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "chatgpt_codex")
    monkeypatch.setenv("LUMENA_CODEX_SURFACES", "codeagent")
    detach_shared_codex_app_server()

    result = await delegate_task_handler(
        ctx,
        description="Modifie app.py",
        agent_type="code",
        project_path=str(workspace),
    )
    assert result.success is False
    assert result.status_code == "codex_not_connected"
    assert "Aucun fallback API" in result.output
