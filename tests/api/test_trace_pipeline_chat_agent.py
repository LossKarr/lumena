from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tests._server_compat import server_module
from src.telemetry import get_trace_bus, reset_trace_bus_for_tests


class _FakeLLM:
    def get_last_response_meta(self):
        return {
            "provider_requested": "test",
            "provider_used": "test",
            "model_requested": "test-model",
            "model_used": "test-model",
            "fallback_used": False,
            "fallback_reason": None,
            "continuation_used": False,
            "continuation_steps": 0,
            "finish_reason": "stop",
        }


class _FakeLumena:
    def __init__(self):
        self.llm = _FakeLLM()
        self.emotion_manager = SimpleNamespace(
            get_mood=lambda: SimpleNamespace(value="neutral"),
            get_stats=lambda: {"mood": "neutral", "energy": "medium"},
        )
        self.tool_system = None
        self.repo_map = None
        self.code_index = None
        self.rules_loader = None
        self.hook_system = None
        self.instinct_system = None
        self.memory = SimpleNamespace(get_stats=lambda: {"total_memories": 10})

    async def chat(self, _message: str):
        return "chat-ok"

    async def think_and_act(self, _message: str):
        return "agent-ok"

    def get_last_agent_meta(self):
        return {
            "agent_output_incomplete": False,
            "agent_output_warning": None,
            "agent_repair_attempts": 0,
            "agent_final_finish_reason": "stop",
        }


@pytest.mark.asyncio
async def test_api_chat_emits_trace_input_output_stages(monkeypatch):
    monkeypatch.setattr(server_module, "lumena", _FakeLumena())
    reset_trace_bus_for_tests()
    bus = get_trace_bus()
    bus.clear_for_tests()

    response = await server_module.chat(server_module.ChatRequest(message="hello", use_agent=False))
    assert response.response == "chat-ok"

    events = bus.recent(50)
    stages = [event["stage"] for event in events]
    assert "input_received" in stages
    assert "output_sent" in stages


@pytest.mark.asyncio
async def test_api_chat_agent_emits_agent_mode_trace(monkeypatch):
    monkeypatch.setattr(server_module, "lumena", _FakeLumena())
    reset_trace_bus_for_tests()
    bus = get_trace_bus()
    bus.clear_for_tests()

    response = await server_module.chat(server_module.ChatRequest(message="build", use_agent=True))
    assert response.response == "agent-ok"

    events = bus.recent(50)
    assert any(e["stage"] == "input_received" and e["mode"] == "agent" for e in events)
    assert any(e["stage"] == "output_sent" and e["mode"] == "agent" for e in events)
