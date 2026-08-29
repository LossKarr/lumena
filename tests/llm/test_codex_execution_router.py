from __future__ import annotations

import asyncio
from collections import deque
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from src.llm import execution_router
from src.llm.codex_app_server import CodexAppServerConfig, CodexNotification
from src.llm.codex_subscription import (
    CodexSubscriptionSettings,
    CodexSurface,
    OpenAIAccessMode,
)
from src.llm.execution_router import (
    _mission_deadline_action,
    _prepare_handler_context,
    _record_codex_response_meta,
    _record_tool_observation,
    _visible_tool_names,
    build_codex_tool_app_server_command,
    consume_codex_response_meta,
    maybe_run_codex_surface,
    reset_codex_response_meta,
    should_route_react_to_codex,
)
from src.reasoning.handlers.context import HandlerContext
from src.reasoning.react import ReActLoop
from src.reasoning import react as react_module
from src.reasoning.react_config import Observation
from src.runtime.execution_ledger import ExecutionLedger


def _settings(*surfaces):
    return CodexSubscriptionSettings(
        access_mode=OpenAIAccessMode.CHATGPT_CODEX,
        default_model="account-model",
        surfaces=frozenset(surfaces),
    )


def test_agent_and_mission_routing_are_separate_and_off_by_default():
    agent = _settings(CodexSurface.AGENT)
    missions = _settings(CodexSurface.MISSIONS)
    assert should_route_react_to_codex(is_mission_run=False, settings=agent)
    assert not should_route_react_to_codex(is_mission_run=True, settings=agent)
    assert should_route_react_to_codex(is_mission_run=True, settings=missions)
    assert not should_route_react_to_codex(is_mission_run=False, settings=missions)
    assert not should_route_react_to_codex(
        is_mission_run=False, settings=CodexSubscriptionSettings()
    )


def test_ephemeral_command_uses_required_stdio_mcp_without_token_or_config_file(tmp_path):
    command = build_codex_tool_app_server_command(
        "codex.exe",
        python_executable="python.exe",
        project_root=tmp_path,
        tool_timeout_s=90,
    )
    joined = " ".join(command)
    assert command[-1] == "app-server"
    assert "mcp_servers.lumena.command" in joined
    assert "mcp_servers.lumena.required=true" in joined
    assert "mcp_servers.lumena.env_vars" in joined
    assert "dynamicTools" not in joined
    assert "LUMENA_CODEX_BRIDGE_TOKEN=" not in joined
    assert "config.toml" not in joined


def test_ephemeral_command_adds_compatibility_override_when_requested(tmp_path):
    command = build_codex_tool_app_server_command(
        "codex.exe",
        python_executable="python.exe",
        project_root=tmp_path,
        tool_timeout_s=90,
        config_overrides={"service_tier": "flex"},
    )
    assert command[:3] == (
        "codex.exe",
        "--config",
        'service_tier="flex"',
    )
    assert command[-1] == "app-server"


class SchemaRegistry:
    def __init__(self, *, hard=False):
        self._allowed_tools = {"web_search"}
        self._allowed_tools_hard = hard
        self._tool_modules = {
            "web_search": "web",
            "browser_navigate": "browser",
            "read_file": "files",
            "send_email": "mail",
            "final_answer": "control",
        }
        self._v2_context = HandlerContext()

    def get_tools_schema(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": name,
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for name in self._tool_modules
        ]


def test_visible_tools_mirror_soft_category_transitions_but_respect_hard_scope():
    soft = SimpleNamespace(tools=SchemaRegistry(hard=False))
    hard = SimpleNamespace(tools=SchemaRegistry(hard=True))
    assert _visible_tool_names(soft) == frozenset(
        {"web_search", "browser_navigate", "read_file"}
    )
    assert _visible_tool_names(hard) == frozenset({"web_search"})


class FakeOrchestrator:
    def __init__(self):
        self.metadata = {
            "mission_workspace": "missions/task-1",
            "allowed_files": ["app.py"],
        }
        self.updates = []
        self.cancelled = []

    def get_task(self, _task_id):
        return {"metadata": dict(self.metadata)}

    def set_task_metadata(self, task_id, **values):
        self.updates.append((task_id, values))

    def is_cancel_requested(self, _task_id):
        return False

    def cancel_task(self, task_id, *, propagate=False):
        self.cancelled.append((task_id, propagate))


class FakeReact:
    def __init__(self, tmp_path, *, mission=False):
        self._is_mission_run = mission
        self.task_id = "task-1" if mission else None
        self.task_orchestrator = FakeOrchestrator() if mission else None
        self.timeout_seconds = 600
        self._loop_start_time = asyncio.get_running_loop().time()
        self._current_iteration = 2
        self._original_query = "fais le travail"
        self.tools = SchemaRegistry()
        self.tools.default_workspace_root = tmp_path
        self.tools._v2_context.runtime_root = tmp_path
        self.execution_ledger = ExecutionLedger()
        self.history = []
        self._successful_session_tools = set()
        self.checkpoints = []
        self.final_messages = []

    def _mission_workspace_meta(self):
        return "missions/task-1" if self._is_mission_run else ""

    def _mission_allowed_files_meta(self):
        return ["app.py"] if self._is_mission_run else []

    def _record_document_catalog_evidence(self, *_args):
        pass

    def _record_document_workflow_evidence(self, *_args):
        pass

    def _feed_structured_tool(self, _name):
        pass

    def _update_plan_progress(self, *_args):
        pass

    def _mark_task_checkpoint(self, payload):
        self.checkpoints.append(payload)

    def _stream_and_return_final(self, message):
        self.final_messages.append(message)
        return f"locked:{message}"


@pytest.mark.asyncio
async def test_handler_context_and_test_proof_are_owned_by_lumena(tmp_path):
    react = FakeReact(tmp_path, mission=True)
    _prepare_handler_context(react)
    context = react.tools._v2_context
    assert context.runtime_task_id == "task-1"
    assert context.is_mission_run is True
    assert context.mission_workspace == "missions/task-1"
    assert context.mission_allowed_files == ["app.py"]

    _record_tool_observation(
        react,
        "run_command",
        {"command": "python -m pytest -q"},
        Observation(content="3 passed in 0.10s", success=True),
        0.2,
    )
    outcome = react.execution_ledger.last_test_outcome()
    assert outcome["is_test_cmd"] is True
    assert outcome["passed"] == 3
    assert react.history[-1].action.tool_name == "run_command"
    assert react.checkpoints[-1]["phase"] == "codex_tool"
    assert react.task_orchestrator.updates[-1][1]["tests_green"] is True


class SharedSupervisor:
    is_running = True
    config = CodexAppServerConfig(command=("codex.exe", "app-server"), environ={})


class FakeDedicatedSupervisor:
    instances = []

    def __init__(self, config):
        self.config = config
        self.is_running = False
        self.requests = []
        self.notifications = deque(
            [
                CodexNotification(
                    method="item/completed",
                    params={
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "item": {"type": "agentMessage", "text": "travail prouve"},
                    },
                ),
                CodexNotification(
                    method="turn/completed",
                    params={
                        "threadId": "thread-1",
                        "turn": {"id": "turn-1", "status": "completed"},
                    },
                ),
            ]
        )
        self.__class__.instances.append(self)

    async def start(self):
        self.is_running = True

    async def stop(self):
        self.is_running = False

    async def request(self, method, params=None, *, timeout=None):
        self.requests.append((method, params, timeout))
        if method == "account/read":
            return {"account": {"type": "chatgpt", "plan": "plus"}}
        if method == "model/list":
            return {"models": [{"id": "account-model", "isDefault": True}]}
        if method == "thread/start":
            return {"thread": {"id": "thread-1"}}
        if method == "turn/start":
            return {"turn": {"id": "turn-1"}}
        if method == "turn/interrupt":
            return {}
        raise AssertionError(method)

    async def next_notification(self, *, timeout=None):
        return self.notifications.popleft()


@pytest.mark.asyncio
async def test_full_opt_in_route_is_read_only_and_returns_via_final_chokepoint(
    monkeypatch, tmp_path
):
    react = FakeReact(tmp_path, mission=False)
    FakeDedicatedSupervisor.instances.clear()
    monkeypatch.setattr(execution_router, "get_shared_codex_app_server", lambda: SharedSupervisor())
    monkeypatch.setattr(execution_router, "CodexAppServerSupervisor", FakeDedicatedSupervisor)
    result = await maybe_run_codex_surface(
        react,
        "fais le travail",
        "fais le travail",
        settings=_settings(CodexSurface.AGENT),
    )
    assert result == "locked:travail prouve"
    dedicated = FakeDedicatedSupervisor.instances[-1]
    thread = next(params for method, params, _ in dedicated.requests if method == "thread/start")
    turn = next(params for method, params, _ in dedicated.requests if method == "turn/start")
    assert thread["sandbox"] == "read-only"
    assert turn["sandboxPolicy"] == {
        "type": "readOnly",
        "networkAccess": False,
    }
    assert "serveur MCP `lumena`" in turn["input"][0]["text"]
    assert dedicated.is_running is False
    assert consume_codex_response_meta() == {
        "provider_requested": "openai-codex",
        "provider_used": "openai-codex",
        "model_requested": "account-model",
        "model_used": "account-model",
        "access_source_requested": "codex",
        "access_source_used": "codex",
        "billing_source": "chatgpt_subscription",
        "fallback_used": False,
        "fallback_reason": None,
        "fallback_attempts": [],
        "continuation_used": False,
        "continuation_steps": 0,
        "finish_reason": "stop",
        "prompt_tokens": None,
        "completion_tokens": None,
    }


@pytest.mark.asyncio
async def test_codex_response_metadata_is_task_local_and_consumed_once():
    async def one_turn(model: str):
        reset_codex_response_meta()
        _record_codex_response_meta(configured_model=model, selected_model=model)
        await asyncio.sleep(0)
        return consume_codex_response_meta(), consume_codex_response_meta()

    sol, luna = await asyncio.gather(
        one_turn("gpt-5.6-sol"),
        one_turn("gpt-5.6-luna"),
    )
    assert sol[0]["model_used"] == "gpt-5.6-sol"
    assert luna[0]["model_used"] == "gpt-5.6-luna"
    assert sol[1] == {}
    assert luna[1] == {}


@pytest.mark.asyncio
async def test_disabled_surface_is_exact_noop_without_shared_server(tmp_path):
    react = FakeReact(tmp_path, mission=False)
    result = await maybe_run_codex_surface(
        react,
        "historique",
        "historique",
        settings=CodexSubscriptionSettings(),
    )
    assert result is None
    assert react.final_messages == []


class DeadlineSupervisor:
    is_running = True

    def __init__(self):
        self.requests = []

    async def request(self, method, params=None, *, timeout=None):
        self.requests.append((method, params, timeout))
        return {"turnId": "turn-1"}


@pytest.mark.asyncio
async def test_mission_deadline_steers_once_then_hard_cancels(tmp_path):
    react = FakeReact(tmp_path, mission=True)
    now = datetime.now()
    react.task_orchestrator.get_task = lambda _task_id: {
        "created_at": (now - timedelta(minutes=5)).isoformat(),
        "metadata": {
            **react.task_orchestrator.metadata,
            "deadline_ts": (now - timedelta(seconds=1)).isoformat(),
        },
    }
    supervisor = DeadlineSupervisor()
    action = await _mission_deadline_action(
        react, supervisor, "thread-1", "turn-1", steered=False
    )
    assert action == "steered"
    assert supervisor.requests[0][0] == "turn/steer"
    assert react.task_orchestrator.updates[-1][1]["deadline_steered"] is True

    react.task_orchestrator.get_task = lambda _task_id: {
        "created_at": (now - timedelta(minutes=10)).isoformat(),
        "metadata": {
            **react.task_orchestrator.metadata,
            "deadline_ts": (now - timedelta(minutes=3)).isoformat(),
            "deadline_steered": True,
        },
    }
    action = await _mission_deadline_action(
        react, supervisor, "thread-1", "turn-1", steered=True
    )
    assert action == "cancel"
    assert react.task_orchestrator.cancelled == [("task-1", True)]


@pytest.mark.asyncio
async def test_mission_deadline_disarms_when_completion_is_proven(tmp_path):
    react = FakeReact(tmp_path, mission=True)
    now = datetime.now()
    react._mission_completion_evidence = lambda: {
        "complete": True,
        "scope": "lead",
        "delivery_proven": True,
        "tests_required": True,
        "tests_green": True,
    }
    react.task_orchestrator.get_task = lambda _task_id: {
        "created_at": (now - timedelta(minutes=10)).isoformat(),
        "metadata": {
            **react.task_orchestrator.metadata,
            "deadline_ts": (now - timedelta(minutes=3)).isoformat(),
            "deadline_steered": True,
        },
    }
    action = await _mission_deadline_action(
        react, DeadlineSupervisor(), "thread-1", "turn-1", steered=True
    )
    assert action == "none"
    assert react.task_orchestrator.cancelled == []
    _, metadata = react.task_orchestrator.updates[-1]
    assert metadata["deadline_net_disarmed"] is True
    assert metadata["completion_proof"]["complete"] is True


def test_codex_agent_final_uses_existing_truth_lock_without_changing_legacy_agent(
    monkeypatch,
):
    loop = ReActLoop.__new__(ReActLoop)
    loop._codex_tool_bridge_run = True
    loop.task_id = None
    loop.history = []
    loop.runtime_ctx = SimpleNamespace(channel="voice")
    loop.execution_ledger = SimpleNamespace(
        last_test_outcome=lambda: None,
        has_any_mutation=lambda: False,
        has_published=lambda: False,
    )
    loop._current_green_test_proof = lambda: False
    loop._current_browser_proof = lambda: False
    loop._tests_present_but_not_run = lambda: False
    loop._truth_lock_web_flag = lambda: False
    loop._server_started_proof = lambda: False
    loop._browser_content_seen = lambda: False
    loop._truth_lock_interaction_proven = lambda: False
    loop._truth_lock_interaction_flag = lambda: False
    loop._truth_lock_game_flag = lambda: False
    loop._browser_runtime_failed_for_truth_lock = lambda: False
    completed = []
    loop._mark_task_done = completed.append
    calls = []

    def fake_lock(message, **kwargs):
        calls.append((message, kwargs))
        return "factual", {"changed": True, "mutation": True}

    # Lot RF-8 : le verrou est APPELE depuis `final_delivery_runtime.py`.
    # Patcher `react_module` ne l'atteint plus — c'est exactement le risque que
    # le fichier de tests de RF-1 avait nomme : « les monkeypatchs des tests
    # patcheraient l'un et le code appellerait l'autre ». Ici le test l'a
    # attrape au lieu de passer en silence. Son intention est inchangee : la
    # voie codex passe bien par le verrou existant.
    import src.reasoning.final_delivery_runtime as fd_module

    monkeypatch.setattr(fd_module, "apply_mission_truth_lock", fake_lock)
    assert loop._stream_and_return_final("claim") == "factual"
    assert calls and calls[0][1]["has_any_mutation"] is False
    assert completed == ["factual"]

    loop._codex_tool_bridge_run = False
    calls.clear()
    completed.clear()
    assert loop._stream_and_return_final("legacy") == "legacy"
    assert calls == []
