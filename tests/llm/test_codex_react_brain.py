from __future__ import annotations

import asyncio
import inspect
import json
from collections import deque
from types import SimpleNamespace

import pytest

from src.core_services.agent_service import AgentService
from src.llm import execution_router
from src.llm.codex_app_server import CodexAppServerConfig, CodexNotification
from src.llm.codex_subscription import (
    CodexSubscriptionSettings,
    CodexSurface,
    OpenAIAccessMode,
)
from src.llm.execution_router import (
    CodexReActBrain,
    CodexReActUnavailable,
    _parse_codex_decision,
    codex_react_brain_scope,
)
from src.reasoning.react import Observation, ReActLoop


def _settings(*surfaces: CodexSurface) -> CodexSubscriptionSettings:
    return CodexSubscriptionSettings(
        access_mode=OpenAIAccessMode.CHATGPT_CODEX,
        default_model="account-model",
        surfaces=frozenset(surfaces),
    )


class _SharedSupervisor:
    is_running = True
    config = CodexAppServerConfig(
        command=("codex.exe", "app-server"),
        environ={"PATH": "test"},
    )


class _DecisionSupervisor:
    instances: list["_DecisionSupervisor"] = []
    decisions: deque[str] = deque()
    effectful_item = ""

    def __init__(self, config):
        self.config = config
        self.is_running = False
        self.requests = []
        self.notifications: deque[CodexNotification] = deque()
        self.turn_count = 0
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
            return {"thread": {"id": f"thread-{self.turn_count + 1}"}}
        if method == "turn/start":
            self.turn_count += 1
            thread_id = str(params["threadId"])
            turn_id = f"turn-{self.turn_count}"
            if self.effectful_item:
                self.notifications.append(
                    CodexNotification(
                        method="item/completed",
                        params={
                            "threadId": thread_id,
                            "turnId": turn_id,
                            "item": {"type": self.effectful_item},
                        },
                    )
                )
            response = self.decisions.popleft()
            self.notifications.extend(
                (
                    CodexNotification(
                        method="item/completed",
                        params={
                            "threadId": thread_id,
                            "turnId": turn_id,
                            "item": {"type": "agentMessage", "text": response},
                        },
                    ),
                    CodexNotification(
                        method="turn/completed",
                        params={
                            "threadId": thread_id,
                            "turn": {"id": turn_id, "status": "completed"},
                        },
                    ),
                )
            )
            return {"turn": {"id": turn_id}}
        if method in {"thread/archive", "turn/interrupt"}:
            return {}
        raise AssertionError(method)

    async def next_notification(self, *, timeout=None):
        return self.notifications.popleft()


class _DummyTools:
    def __init__(self):
        self.tools = {"list_directory": {"handler": None}}
        self.calls = []

    def get_tools_description(self) -> str:
        return "- list_directory(path): liste un dossier"

    async def execute(self, tool_name: str, tool_args, **kwargs):
        self.calls.append((tool_name, tool_args))
        return Observation(content="preuve: dossier inspecte", success=True)


def _configure_fake_server(monkeypatch, *decisions: dict) -> None:
    _DecisionSupervisor.instances.clear()
    _DecisionSupervisor.decisions = deque(
        json.dumps(decision, ensure_ascii=False) for decision in decisions
    )
    _DecisionSupervisor.effectful_item = ""
    monkeypatch.setattr(
        execution_router, "get_shared_codex_app_server", lambda: _SharedSupervisor()
    )
    monkeypatch.setattr(
        execution_router, "CodexAppServerSupervisor", _DecisionSupervisor
    )


def test_schema_decision_is_converted_to_native_react_wire_format():
    wire = _parse_codex_decision(
        '{"thought":"Je verifie.","action":"read_file",'
        '"action_input":"{\\"path\\":\\"app.py\\"}"}'
    )
    assert wire == (
        "THOUGHT: Je verifie.\n"
        "ACTION: read_file\n"
        'ACTION_INPUT: {"path":"app.py"}'
    )


@pytest.mark.asyncio
async def test_brain_is_schema_constrained_isolated_and_has_no_mcp(monkeypatch):
    _configure_fake_server(
        monkeypatch,
        {"thought": "Fini.", "action": "FINAL", "action_input": "Resultat."},
    )
    react = ReActLoop(llm_chat_func=lambda *_a, **_k: "historique")
    brain = CodexReActBrain(react, _settings(CodexSurface.AGENT))
    try:
        result = await brain([{"role": "user", "content": "PROMPT LUMENA"}])
    finally:
        await brain.aclose()

    assert "ACTION: FINAL" in result
    dedicated = _DecisionSupervisor.instances[-1]
    command = " ".join(dedicated.config.command)
    assert "mcp_servers={}" in command
    thread = next(p for m, p, _ in dedicated.requests if m == "thread/start")
    turn = next(p for m, p, _ in dedicated.requests if m == "turn/start")
    assert thread["sandbox"] == "read-only"
    assert turn["sandboxPolicy"] == {"type": "readOnly", "networkAccess": False}
    assert turn["outputSchema"]["required"] == [
        "thought",
        "action",
        "action_input",
    ]
    assert "N'execute AUCUN outil Codex" in turn["input"][0]["text"]
    assert dedicated.is_running is False


@pytest.mark.asyncio
async def test_effectful_codex_item_is_rejected_before_lumena_execution(monkeypatch):
    _configure_fake_server(
        monkeypatch,
        {"thought": "Fini.", "action": "FINAL", "action_input": "Resultat."},
    )
    _DecisionSupervisor.effectful_item = "commandExecution"
    brain = CodexReActBrain(ReActLoop(), _settings(CodexSurface.AGENT))
    try:
        with pytest.raises(CodexReActUnavailable, match="hors de Lumena"):
            await brain([{"role": "user", "content": "PROMPT"}])
    finally:
        await brain.aclose()


@pytest.mark.asyncio
async def test_opted_in_codex_drives_real_react_tool_observe_final_cycle(monkeypatch):
    _configure_fake_server(
        monkeypatch,
        {
            "thought": "Je liste le dossier.",
            "action": "list_directory",
            "action_input": '{"path":"workspace"}',
        },
        {
            "thought": "La preuve est recue.",
            "action": "FINAL",
            "action_input": "Le dossier a ete inspecte.",
        },
    )
    monkeypatch.setattr(
        execution_router,
        "load_codex_subscription_settings",
        lambda: _settings(CodexSurface.AGENT),
    )
    historical_calls = []

    async def historical_llm(*_args, **_kwargs):
        historical_calls.append(True)
        return "ACTION: FINAL\nACTION_INPUT: mauvais rail"

    tools = _DummyTools()
    loop = ReActLoop(llm_chat_func=historical_llm, tools=tools, max_iterations=4)
    loop.timeout_seconds = None
    result = await loop.run("inspecte le workspace puis reponds")

    assert "Le dossier a ete inspecte" in result
    assert historical_calls == []
    assert tools.calls == [("list_directory", {"path": "workspace"})]
    assert loop.history[0].observation.content == "preuve: dossier inspecte"
    dedicated = _DecisionSupervisor.instances[-1]
    turns = [p for m, p, _ in dedicated.requests if m == "turn/start"]
    assert len(turns) == 2
    assert "preuve: dossier inspecte" in turns[1]["input"][0]["text"]


@pytest.mark.asyncio
async def test_disabled_surface_keeps_historical_callback_exactly(monkeypatch):
    react = ReActLoop(llm_chat_func=lambda *_a, **_k: "historique")
    old_chat = react.llm_chat
    old_meta = react.llm_meta_getter
    async with codex_react_brain_scope(
        react, settings=CodexSubscriptionSettings()
    ) as enabled:
        assert enabled is False
        assert react.llm_chat is old_chat
        assert react.llm_meta_getter is old_meta


@pytest.mark.asyncio
async def test_mission_surface_uses_same_brain_without_changing_agent_surface():
    orchestrator = SimpleNamespace(
        get_task=lambda _task_id: {"metadata": {"kind": "mission"}}
    )
    mission = ReActLoop(task_id="task-1", task_orchestrator=orchestrator)
    old_chat = mission.llm_chat
    async with codex_react_brain_scope(
        mission, settings=_settings(CodexSurface.MISSIONS)
    ) as enabled:
        assert enabled is True
        assert isinstance(mission.llm_chat, CodexReActBrain)
    assert mission.llm_chat is old_chat


@pytest.mark.asyncio
async def test_cancel_interrupts_current_codex_decision(monkeypatch):
    _configure_fake_server(
        monkeypatch,
        {"thought": "Fini.", "action": "FINAL", "action_input": "Resultat."},
    )
    monkeypatch.setattr(execution_router, "_cancel_requested", lambda _react: True)
    brain = CodexReActBrain(ReActLoop(), _settings(CodexSurface.AGENT))
    try:
        with pytest.raises(asyncio.CancelledError):
            await brain([{"role": "user", "content": "PROMPT"}])
    finally:
        await brain.aclose()
    methods = [method for method, _, _ in _DecisionSupervisor.instances[-1].requests]
    assert "turn/interrupt" in methods


def test_agent_service_has_explicit_codex_no_fallback_branch():
    interactive = inspect.getsource(AgentService.think_and_act)
    silent = inspect.getsource(AgentService.think_and_act_silent)
    assert "Aucun fallback vers une API ou un autre modele" in interactive
    assert "Aucun fallback vers une API ou un autre modele" in silent
