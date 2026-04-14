from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests._server_compat import server_module
from src.runtime.task_orchestrator import TaskOrchestrator


class _FakeLLM:
    def get_last_response_meta(self):
        return {
            "provider_requested": "openai",
            "provider_used": "openai",
            "model_requested": "gpt-4o",
            "model_used": "gpt-4o",
            "fallback_used": False,
            "fallback_reason": None,
            "continuation_used": False,
            "continuation_steps": 0,
            "finish_reason": "stop",
        }


class _FakeLumena:
    def __init__(self):
        self.llm = _FakeLLM()
        self.emotion_manager = SimpleNamespace(get_mood=lambda: SimpleNamespace(value="focused"))
        self.tool_system = None

    async def chat(self, _message: str):
        return "ok"

    async def think_and_act(self, _message: str):
        return "ok-agent"

    def get_last_agent_meta(self):
        return {
            "agent_output_incomplete": False,
            "agent_output_warning": None,
            "agent_repair_attempts": 0,
        }


@pytest.mark.asyncio
async def test_cross_channel_conversation_continuity(monkeypatch):
    orchestrator = TaskOrchestrator()
    monkeypatch.setattr(server_module, "lumena", _FakeLumena())
    monkeypatch.setattr(server_module, "TASK_ORCHESTRATOR_V1_ENABLED", True)
    monkeypatch.setattr(server_module, "_TASK_ORCHESTRATOR", orchestrator)
    monkeypatch.setattr(server_module, "OMNICHANNEL_ENVELOPE_V1_ENABLED", True)

    first = await server_module.chat(
        server_module.ChatRequest(
            message="start from web",
            use_agent=False,
            channel="web",
            client="omni-client",
        )
    )
    second = await server_module.chat(
        server_module.ChatRequest(
            message="continue from ide",
            use_agent=False,
            channel="ide",
            client="omni-client",
        )
    )

    assert first.conversation_id
    assert second.conversation_id == first.conversation_id

    session = await server_module.get_session(first.conversation_id, limit=20)
    assert session["count"] >= 2
    assert session.get("session_state") is not None
    assert session["session_state"]["last_channel"] == "ide"
    if server_module.TELEMETRY_AVAILABLE:
        assert session["session_state"]["last_trace_id"]


@pytest.mark.asyncio
async def test_cross_channel_resume_via_task_id(monkeypatch):
    orchestrator = TaskOrchestrator()
    monkeypatch.setattr(server_module, "lumena", _FakeLumena())
    monkeypatch.setattr(server_module, "TASK_ORCHESTRATOR_V1_ENABLED", True)
    monkeypatch.setattr(server_module, "_TASK_ORCHESTRATOR", orchestrator)
    monkeypatch.setattr(server_module, "OMNICHANNEL_ENVELOPE_V1_ENABLED", True)

    first = await server_module.chat(
        server_module.ChatRequest(
            message="start web",
            use_agent=False,
            channel="web",
            client="web-client",
            task_id="task_cross_resume",
            conversation_id="conv_cross_resume",
        )
    )
    second = await server_module.chat(
        server_module.ChatRequest(
            message="continue telegram",
            use_agent=False,
            channel="telegram",
            client="telegram-client",
            task_id="task_cross_resume",
        )
    )
    third = await server_module.chat(
        server_module.ChatRequest(
            message="finish ide",
            use_agent=False,
            channel="ide",
            client="cursor-ide-local",
            task_id="task_cross_resume",
        )
    )

    assert first.conversation_id == "conv_cross_resume"
    assert second.conversation_id == "conv_cross_resume"
    assert third.conversation_id == "conv_cross_resume"

    session = await server_module.get_session("conv_cross_resume", limit=20)
    assert session["count"] >= 1
    assert session.get("session_state") is not None
    assert session["session_state"]["last_channel"] == "ide"
    if server_module.TELEMETRY_AVAILABLE:
        assert session["session_state"]["last_trace_id"]
