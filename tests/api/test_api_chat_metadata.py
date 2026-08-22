from pathlib import Path
import json
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tests._server_compat import server_module
from src.runtime.task_orchestrator import TaskOrchestrator


@pytest.fixture(autouse=True)
def _resolved_workspace_context(monkeypatch, tmp_path):
    workspace = str(tmp_path)

    def _ok_workspace(*_args, **_kwargs):
        return {
            "workspace_path": workspace,
            "active_file_path": None,
            "open_files": [],
            "resolved_date": "2026-04-25",
            "resolution_reason": "explicit_test_workspace",
            "workspace_policy": "default",
            "workspace_used_fallback": False,
            "channel": "web",
        }

    monkeypatch.setattr("web.routes.chat._apply_workspace_policy", _ok_workspace)
    return workspace


class _FakeLLM:
    def __init__(self, meta):
        self._meta = meta

    def get_last_response_meta(self):
        return dict(self._meta)


class _FakeLumena:
    def __init__(self, meta, agent_meta=None):
        self.llm = _FakeLLM(meta)
        self._agent_meta = dict(
            agent_meta
            or {
                "agent_output_incomplete": False,
                "agent_output_warning": None,
                "agent_repair_attempts": 0,
                "agent_final_finish_reason": None,
            }
        )
        self.emotion_manager = SimpleNamespace(
            get_mood=lambda: SimpleNamespace(value="focused"),
            get_stats=lambda: {"mood": "focused", "energy": "high"},
        )
        self.tool_system = None
        self.repo_map = None
        self.code_index = None
        self.rules_loader = None
        self.hook_system = None
        self.instinct_system = None
        self.memory = SimpleNamespace(get_stats=lambda: {"count": 0})
        self._skills = {"pdf": "skill-pdf", "docx": "skill-docx"}
        self.skills_auto_activation = True

    async def chat(self, _message: str):
        return "reponse-chat"

    async def think_and_act(self, _message: str):
        return "reponse-agent"

    def get_last_agent_meta(self):
        return dict(self._agent_meta)

    def get_last_active_skills(self):
        return ["pdf", "docx"]


@pytest.mark.asyncio
async def test_api_chat_returns_provider_fallback_continuation_metadata(monkeypatch):
    fake_meta = {
        "provider_requested": "openai",
        "provider_used": "ollama",
        "model_requested": "gpt-4o",
        "model_used": "qwen3:8b",
        "fallback_used": True,
        "fallback_reason": "openai: 429",
        "continuation_used": True,
        "continuation_steps": 2,
        "finish_reason": "stop",
    }
    monkeypatch.setattr(server_module, "lumena", _FakeLumena(fake_meta))

    response = await server_module.chat(server_module.ChatRequest(message="test", use_agent=False))

    assert response.response == "reponse-chat"
    assert response.provider_requested == "openai"
    assert response.provider_used == "ollama"
    assert response.model_requested == "gpt-4o"
    assert response.model_used == "qwen3:8b"
    assert response.fallback_used is True
    assert response.fallback_reason == "openai: 429"
    assert response.continuation_used is True
    assert response.continuation_steps == 2
    assert response.finish_reason == "stop"


@pytest.mark.asyncio
async def test_api_chat_stream_done_event_contains_same_metadata(monkeypatch):
    fake_meta = {
        "provider_requested": "anthropic",
        "provider_used": "anthropic",
        "model_requested": "claude-sonnet-4-20250514",
        "model_used": "claude-sonnet-4-20250514",
        "fallback_used": False,
        "fallback_reason": None,
        "continuation_used": True,
        "continuation_steps": 1,
        "finish_reason": "end_turn",
    }
    monkeypatch.setattr(server_module, "lumena", _FakeLumena(fake_meta))

    stream_response = await server_module.chat_stream(
        server_module.ChatRequest(message="stream", use_agent=False)
    )

    payloads = []
    async for chunk in stream_response.body_iterator:
        text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for line in text.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line[6:]))

    done_event = next(item for item in payloads if item.get("type") == "done")

    assert done_event["provider_requested"] == "anthropic"
    assert done_event["provider_used"] == "anthropic"
    assert done_event["model_requested"] == "claude-sonnet-4-20250514"
    assert done_event["model_used"] == "claude-sonnet-4-20250514"
    assert done_event["fallback_used"] is False
    assert done_event["continuation_used"] is True
    assert done_event["continuation_steps"] == 1
    assert done_event["finish_reason"] == "end_turn"


@pytest.mark.asyncio
async def test_api_chat_stream_forwards_codex_deltas_before_done(monkeypatch):
    fake_meta = {
        "provider_requested": "openai-codex",
        "provider_used": "openai-codex",
        "model_requested": "auto",
        "model_used": "account-model",
        "fallback_used": False,
        "fallback_reason": None,
        "continuation_used": False,
        "continuation_steps": 0,
        "finish_reason": "stop",
    }

    class _StreamingFakeLumena(_FakeLumena):
        async def chat(self, _message: str):
            from src.llm.codex_chat import _DELTA_SINK

            sink = _DELTA_SINK.get()
            assert sink is not None
            sink("Bonjour ")
            await __import__("asyncio").sleep(0.12)
            sink("depuis Codex")
            return "Bonjour depuis Codex"

    monkeypatch.setattr(server_module, "lumena", _StreamingFakeLumena(fake_meta))
    stream_response = await server_module.chat_stream(
        server_module.ChatRequest(message="stream-codex", use_agent=False)
    )
    payloads = []
    async for chunk in stream_response.body_iterator:
        text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for line in text.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line[6:]))

    tokens = [item["content"] for item in payloads if item.get("type") == "token"]
    done_index = next(i for i, item in enumerate(payloads) if item.get("type") == "done")
    token_indices = [i for i, item in enumerate(payloads) if item.get("type") == "token"]
    assert "".join(tokens) == "Bonjour depuis Codex"
    assert token_indices and max(token_indices) < done_index


@pytest.mark.asyncio
async def test_api_chat_agent_returns_agent_metadata(monkeypatch):
    fake_meta = {
        "provider_requested": "deepseek",
        "provider_used": "deepseek",
        "model_requested": "deepseek-reasoner",
        "model_used": "deepseek-reasoner",
        "fallback_used": False,
        "fallback_reason": None,
        "continuation_used": False,
        "continuation_steps": 0,
        "finish_reason": "stop",
    }
    fake_agent_meta = {
        "agent_output_incomplete": True,
        "agent_output_warning": "final_answer_potentially_incomplete",
        "agent_repair_attempts": 1,
        "agent_final_finish_reason": "length",
    }
    monkeypatch.setattr(server_module, "lumena", _FakeLumena(fake_meta, fake_agent_meta))

    response = await server_module.chat(server_module.ChatRequest(message="test", use_agent=True))

    assert response.response == "reponse-agent"
    assert response.agent_output_incomplete is True
    assert response.agent_output_warning == "final_answer_potentially_incomplete"
    assert response.agent_repair_attempts == 1


@pytest.mark.asyncio
async def test_api_chat_agent_prefers_request_local_codex_attribution(monkeypatch):
    stale_api_meta = {
        "provider_requested": "deepseek",
        "provider_used": "deepseek",
        "model_requested": "deepseek-chat",
        "model_used": "deepseek-chat",
        "fallback_used": False,
        "fallback_reason": None,
        "continuation_used": False,
        "continuation_steps": 0,
        "finish_reason": "stop",
    }

    class _CodexAgentLumena(_FakeLumena):
        async def think_and_act(self, _message: str):
            from src.llm.execution_router import _record_codex_response_meta

            _record_codex_response_meta(
                configured_model="gpt-5.6-sol",
                selected_model="gpt-5.6-sol",
            )
            return "reponse-codex-agent"

    monkeypatch.setattr(server_module, "lumena", _CodexAgentLumena(stale_api_meta))
    response = await server_module.chat(
        server_module.ChatRequest(message="lumi", use_agent=True)
    )

    assert response.response == "reponse-codex-agent"
    assert response.provider_requested == "openai-codex"
    assert response.provider_used == "openai-codex"
    assert response.model_requested == "gpt-5.6-sol"
    assert response.model_used == "gpt-5.6-sol"
    assert response.fallback_used is False


@pytest.mark.asyncio
async def test_api_chat_stream_agent_emits_request_local_codex_attribution(monkeypatch):
    stale_api_meta = {
        "provider_requested": "deepseek",
        "provider_used": "deepseek",
        "model_requested": "deepseek-chat",
        "model_used": "deepseek-chat",
        "fallback_used": False,
        "fallback_reason": None,
        "continuation_used": False,
        "continuation_steps": 0,
        "finish_reason": "stop",
    }

    class _CodexAgentLumena(_FakeLumena):
        async def think_and_act(self, _message: str):
            from src.llm.execution_router import _record_codex_response_meta

            _record_codex_response_meta(
                configured_model="gpt-5.6-sol",
                selected_model="gpt-5.6-sol",
            )
            return "reponse-codex-agent"

    monkeypatch.setattr(server_module, "lumena", _CodexAgentLumena(stale_api_meta))
    stream_response = await server_module.chat_stream(
        server_module.ChatRequest(message="lumi", use_agent=True)
    )
    payloads = []
    async for chunk in stream_response.body_iterator:
        text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for line in text.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line[6:]))

    done_event = next(item for item in payloads if item.get("type") == "done")
    assert done_event["provider_used"] == "openai-codex"
    assert done_event["model_used"] == "gpt-5.6-sol"
    assert done_event["fallback_used"] is False


@pytest.mark.asyncio
async def test_api_chat_stream_done_event_contains_agent_metadata(monkeypatch):
    fake_meta = {
        "provider_requested": "deepseek",
        "provider_used": "deepseek",
        "model_requested": "deepseek-reasoner",
        "model_used": "deepseek-reasoner",
        "fallback_used": False,
        "fallback_reason": None,
        "continuation_used": False,
        "continuation_steps": 0,
        "finish_reason": "stop",
    }
    fake_agent_meta = {
        "agent_output_incomplete": True,
        "agent_output_warning": "agent final may be truncated",
        "agent_repair_attempts": 1,
        "agent_final_finish_reason": "length",
    }
    monkeypatch.setattr(server_module, "lumena", _FakeLumena(fake_meta, fake_agent_meta))

    stream_response = await server_module.chat_stream(
        server_module.ChatRequest(message="stream-agent", use_agent=True)
    )

    payloads = []
    async for chunk in stream_response.body_iterator:
        text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for line in text.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line[6:]))

    done_event = next(item for item in payloads if item.get("type") == "done")

    assert done_event["agent_output_incomplete"] is True
    assert done_event["agent_output_warning"] == "agent final may be truncated"
    assert done_event["agent_repair_attempts"] == 1


@pytest.mark.asyncio
async def test_api_status_contains_telegram_runtime_and_instance_id(monkeypatch):
    fake_meta = {
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
    monkeypatch.setattr(server_module, "lumena", _FakeLumena(fake_meta))

    class _FakeTelegram:
        def get_runtime_status(self):
            return {
                "enabled": True,
                "running": False,
                "conflict_seen": True,
                "last_error": "Conflict: terminated by other getUpdates request",
                "state": "disabled_conflict",
                "transient_error": False,
                "transient_backoff_sec": 0.0,
            }

    monkeypatch.setattr(server_module, "telegram_channel", _FakeTelegram())

    payload = await server_module.get_status()

    assert payload["status"] == "ok"
    assert isinstance(payload["instance_id"], str)
    assert payload["telegram_enabled"] is True
    assert payload["telegram_running"] is False
    assert payload["telegram_conflict_seen"] is True
    assert "Conflict" in payload["telegram_last_error"]
    assert payload["telegram_transient_error"] is False
    assert payload["telegram_transient_backoff_sec"] == 0.0
    assert "trace_enabled" in payload
    assert "trace_buffer_size" in payload
    assert "trace_events_in_buffer" in payload
    assert "trace_stream_clients" in payload
    assert "pipeline_chat_requests_total" in payload
    assert "pipeline_chat_success_total" in payload
    assert "pipeline_stream_requests_total" in payload
    assert "pipeline_stream_success_total" in payload
    assert "pipeline_errors_total" in payload
    assert "pipeline_timeouts_total" in payload
    assert "pipeline_cancelled_total" in payload
    assert "pipeline_last_event" in payload
    assert "pipeline_last_event_ts" in payload
    assert "server_time" in payload
    assert payload["status_source"] in {"live", "degraded"}
    assert isinstance(payload["status_poll_recommended_ms"], int)
    assert payload["status_poll_recommended_ms"] > 0
    assert payload["skills_loaded"] == 2
    assert payload["skills_last_active"] == ["pdf", "docx"]
    assert payload["skills_auto_activation"] is True


@pytest.mark.asyncio
async def test_api_chat_returns_file_edits_payload(monkeypatch):
    fake_meta = {
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
    monkeypatch.setattr(server_module, "lumena", _FakeLumena(fake_meta))

    fake_edits = [
        {
            "id": "edit-1",
            "trace_id": "trace-1",
            "turn_id": "turn-1",
            "session_id": "sess-1",
            "tool_name": "write_file",
            "action": "created",
            "file_path": "workspace/demo.py",
            "workspace_relative": "workspace/demo.py",
            "additions": 4,
            "deletions": 0,
            "summary": "created: demo.py",
            "diff_preview": ["+print('hi')"],
        }
    ]
    monkeypatch.setattr(
        server_module,
        "_safe_file_edits_for_trace",
        lambda trace_id, consume: (fake_edits, "sess-1", True),
    )

    response = await server_module.chat(server_module.ChatRequest(message="edit", use_agent=False))

    assert response.file_edits and response.file_edits[0]["id"] == "edit-1"
    assert response.edit_session_id == "sess-1"
    assert response.undo_available is True


@pytest.mark.asyncio
async def test_api_chat_stream_emits_file_edit_and_done_payload(monkeypatch):
    fake_meta = {
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

    class _SlowLumena(_FakeLumena):
        async def think_and_act(self, _message: str):
            import asyncio
            await asyncio.sleep(0.7)
            return "reponse-agent"

    monkeypatch.setattr(server_module, "lumena", _SlowLumena(fake_meta))

    edit_item = {
        "id": "edit-live-1",
        "trace_id": "trace-live",
        "turn_id": "turn-live",
        "session_id": "sess-live",
        "tool_name": "edit_file",
        "action": "edited",
        "file_path": "workspace/live.py",
        "workspace_relative": "workspace/live.py",
        "additions": 2,
        "deletions": 1,
        "summary": "edited: live.py",
        "diff_preview": ["-old", "+new"],
    }
    state = {"calls": 0}

    def _fake_safe(trace_id, consume):
        if consume:
            return ([edit_item], "sess-live", True)
        state["calls"] += 1
        if state["calls"] >= 2:
            return ([edit_item], "sess-live", True)
        return ([], "sess-live", True)

    monkeypatch.setattr(server_module, "_safe_file_edits_for_trace", _fake_safe)

    stream_response = await server_module.chat_stream(
        server_module.ChatRequest(message="stream-agent", use_agent=True)
    )

    payloads = []
    async for chunk in stream_response.body_iterator:
        text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for line in text.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line[6:]))

    file_events = [item for item in payloads if item.get("type") == "file_edit"]
    assert file_events, "expected at least one live file_edit event"
    assert file_events[0]["edit"]["id"] == "edit-live-1"

    done_event = next(item for item in payloads if item.get("type") == "done")
    assert done_event["file_edits"][0]["id"] == "edit-live-1"
    assert done_event["edit_session_id"] == "sess-live"
    assert done_event["undo_available"] is True


@pytest.mark.asyncio
async def test_api_chat_stream_done_event_contains_runtime_ids(monkeypatch):
    fake_meta = {
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
    monkeypatch.setattr(server_module, "lumena", _FakeLumena(fake_meta))

    stream_response = await server_module.chat_stream(
        server_module.ChatRequest(
            message="stream",
            use_agent=False,
            request_id="req_test_1",
            conversation_id="conv_test_1",
            task_id="task_test_1",
        )
    )

    payloads = []
    async for chunk in stream_response.body_iterator:
        text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for line in text.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line[6:]))

    done_event = next(item for item in payloads if item.get("type") == "done")
    assert done_event["request_id"] == "req_test_1"
    assert done_event["conversation_id"] == "conv_test_1"
    assert done_event["task_id"] == "task_test_1"


@pytest.mark.asyncio
async def test_api_chat_stream_events_include_correlation_context(monkeypatch):
    fake_meta = {
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
    monkeypatch.setattr(server_module, "lumena", _FakeLumena(fake_meta))

    stream_response = await server_module.chat_stream(
        server_module.ChatRequest(
            message="corr",
            use_agent=False,
            channel="ide",
            client="cursor-ide-local",
            request_id="req_corr_1",
            conversation_id="conv_corr_1",
            task_id="task_corr_1",
        )
    )

    payloads = []
    async for chunk in stream_response.body_iterator:
        text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for line in text.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line[6:]))

    assert payloads
    for item in payloads:
        assert item.get("channel") == "ide"
        assert item.get("client") == "cursor-ide-local"
        assert item.get("request_id") == "req_corr_1"
        assert item.get("conversation_id") == "conv_corr_1"
        assert item.get("task_id") == "task_corr_1"
        assert "trace_id" in item

    done_event = next(item for item in payloads if item.get("type") == "done")
    if server_module.TELEMETRY_AVAILABLE:
        assert done_event.get("trace_id")


@pytest.mark.asyncio
async def test_api_chat_stream_v2_adds_schema_version(monkeypatch):
    fake_meta = {
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
    monkeypatch.setattr(server_module, "lumena", _FakeLumena(fake_meta))
    monkeypatch.setattr(server_module, "STREAM_EVENT_V2_ENABLED", True)

    stream_response = await server_module.chat_stream(
        server_module.ChatRequest(message="stream", use_agent=False)
    )

    payloads = []
    async for chunk in stream_response.body_iterator:
        text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for line in text.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line[6:]))

    assert payloads, "expected stream payloads"
    assert all(item.get("schema_version") == 2 for item in payloads)


@pytest.mark.asyncio
async def test_api_chat_reuses_conversation_id_for_same_client(monkeypatch):
    fake_meta = {
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
    monkeypatch.setattr(server_module, "lumena", _FakeLumena(fake_meta))
    monkeypatch.setattr(server_module, "OMNICHANNEL_ENVELOPE_V1_ENABLED", True)

    first = await server_module.chat(
        server_module.ChatRequest(message="hello", use_agent=False, client="cursor-ide-local")
    )
    second = await server_module.chat(
        server_module.ChatRequest(message="hello again", use_agent=False, client="cursor-ide-local")
    )

    assert first.conversation_id
    assert second.conversation_id == first.conversation_id


@pytest.mark.asyncio
async def test_api_chat_stream_emits_checkpoint_event(monkeypatch):
    fake_meta = {
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
    monkeypatch.setattr(server_module, "lumena", _FakeLumena(fake_meta))

    stream_response = await server_module.chat_stream(
        server_module.ChatRequest(message="checkpoint", use_agent=False)
    )
    payloads = []
    async for chunk in stream_response.body_iterator:
        text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for line in text.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line[6:]))

    assert any(item.get("type") == "checkpoint" for item in payloads)


@pytest.mark.asyncio
async def test_api_chat_stream_timeout_marks_waiting_io(monkeypatch):
    class _SlowLumena(_FakeLumena):
        async def chat(self, _message: str):
            import asyncio
            await asyncio.sleep(0.2)
            return "too late"

    fake_meta = {
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
    orchestrator = TaskOrchestrator()
    monkeypatch.setattr(server_module, "lumena", _SlowLumena(fake_meta))
    monkeypatch.setattr(server_module, "TASK_ORCHESTRATOR_V1_ENABLED", True)
    monkeypatch.setattr(server_module, "_TASK_ORCHESTRATOR", orchestrator)
    monkeypatch.setenv("LUMENA_TASK_STEP_TIMEOUT_SEC", "0.05")
    monkeypatch.setenv("LUMENA_TASK_STEP_TIMEOUT_RETRIES", "0")
    monkeypatch.setenv("LUMENA_STREAM_GLOBAL_TIMEOUT_SEC", "0")

    stream_response = await server_module.chat_stream(
        server_module.ChatRequest(
            message="timeout-test",
            use_agent=False,
            task_id="task_timeout_stream",
            conversation_id="conv_timeout_stream",
        )
    )
    payloads = []
    async for chunk in stream_response.body_iterator:
        text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for line in text.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line[6:]))

    assert any(item.get("type") == "error" for item in payloads)
    task = orchestrator.get_task("task_timeout_stream")
    assert task is not None
    assert task["state"] == "waiting_io"


@pytest.mark.asyncio
async def test_api_chat_auto_resume_after_timeout(monkeypatch):
    class _RecoveringLumena(_FakeLumena):
        def __init__(self, meta):
            super().__init__(meta)
            self.calls = 0

        async def chat(self, _message: str):
            import asyncio
            self.calls += 1
            if self.calls == 1:
                await asyncio.sleep(0.15)
                return "late"
            await asyncio.sleep(0.01)
            return "recovered"

    fake_meta = {
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
    orchestrator = TaskOrchestrator()
    monkeypatch.setattr(server_module, "lumena", _RecoveringLumena(fake_meta))
    monkeypatch.setattr(server_module, "TASK_ORCHESTRATOR_V1_ENABLED", True)
    monkeypatch.setattr(server_module, "_TASK_ORCHESTRATOR", orchestrator)
    monkeypatch.setenv("LUMENA_TASK_STEP_TIMEOUT_SEC", "0.05")
    monkeypatch.setenv("LUMENA_TASK_STEP_TIMEOUT_RETRIES", "0")
    monkeypatch.setenv("LUMENA_TIMEOUT_AUTO_RESUME", "1")
    monkeypatch.setenv("LUMENA_TIMEOUT_RESUME_STEP_SEC", "0.2")

    response = await server_module.chat(
        server_module.ChatRequest(
            message="recover",
            use_agent=False,
            task_id="task_auto_resume_chat",
            conversation_id="conv_auto_resume_chat",
        )
    )

    assert response.response == "recovered"
    assert any("Reprise automatique" in item.get("content", "") for item in response.thinking_steps)

    task = orchestrator.get_task("task_auto_resume_chat")
    assert task is not None
    assert task["state"] == "done"


@pytest.mark.asyncio
async def test_api_chat_cancelled_error_marks_cancelled(monkeypatch):
    class _CancelledLumena(_FakeLumena):
        async def chat(self, _message: str):
            raise RuntimeError("task_cancelled")

    fake_meta = {
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
    orchestrator = TaskOrchestrator()
    monkeypatch.setattr(server_module, "lumena", _CancelledLumena(fake_meta))
    monkeypatch.setattr(server_module, "TASK_ORCHESTRATOR_V1_ENABLED", True)
    monkeypatch.setattr(server_module, "_TASK_ORCHESTRATOR", orchestrator)

    with pytest.raises(server_module.HTTPException):
        await server_module.chat(
            server_module.ChatRequest(
                message="cancel-me",
                use_agent=False,
                task_id="task_cancelled_chat",
                conversation_id="conv_cancelled_chat",
            )
        )

    task = orchestrator.get_task("task_cancelled_chat")
    assert task is not None
    assert task["state"] == "cancelled"

    session_payload = await server_module.get_session("conv_cancelled_chat")
    assert session_payload["session_state"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_api_chat_stream_cancel_request_marks_cancelled(monkeypatch):
    class _SlowAgentLumena(_FakeLumena):
        async def think_and_act(self, _message: str):
            import asyncio
            await asyncio.sleep(1.0)
            return "late-agent"

    fake_meta = {
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
    orchestrator = TaskOrchestrator()
    monkeypatch.setattr(server_module, "lumena", _SlowAgentLumena(fake_meta))
    monkeypatch.setattr(server_module, "TASK_ORCHESTRATOR_V1_ENABLED", True)
    monkeypatch.setattr(server_module, "_TASK_ORCHESTRATOR", orchestrator)
    monkeypatch.setenv("LUMENA_STREAM_GLOBAL_TIMEOUT_SEC", "0")

    stream_response = await server_module.chat_stream(
        server_module.ChatRequest(
            message="cancel-stream",
            use_agent=True,
            task_id="task_cancelled_stream",
            conversation_id="conv_cancelled_stream",
        )
    )

    payloads = []
    iterator = stream_response.body_iterator.__aiter__()
    first_chunk = await iterator.__anext__()
    first_text = first_chunk.decode("utf-8") if isinstance(first_chunk, (bytes, bytearray)) else str(first_chunk)
    for line in first_text.splitlines():
        if line.startswith("data: "):
            payloads.append(json.loads(line[6:]))

    orchestrator.cancel_task("task_cancelled_stream")

    async for chunk in iterator:
        text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for line in text.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line[6:]))

    assert any(item.get("type") == "error" for item in payloads)
    task = orchestrator.get_task("task_cancelled_stream")
    assert task is not None
    assert task["state"] == "cancelled"

    session_payload = await server_module.get_session("conv_cancelled_stream")
    assert session_payload["session_state"]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_api_chat_refuses_ambiguous_workspace(monkeypatch):
    from web.routes.chat import WorkspacePolicyError

    fake_meta = {
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
    monkeypatch.setattr(server_module, "lumena", _FakeLumena(fake_meta))

    def _raise_workspace_error(*_args, **_kwargs):
        raise WorkspacePolicyError(
            "workspace_ambiguous: ambiguous project",
            status_code=409,
        )

    monkeypatch.setattr("web.routes.chat._apply_workspace_policy", _raise_workspace_error)

    with pytest.raises(server_module.HTTPException) as exc:
        await server_module.chat(server_module.ChatRequest(message="test", use_agent=False))

    assert exc.value.status_code == 409
    assert "workspace_ambiguous" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_api_chat_stream_emits_workspace_ambiguous_error(monkeypatch):
    from web.routes.chat import WorkspacePolicyError

    fake_meta = {
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
    monkeypatch.setattr(server_module, "lumena", _FakeLumena(fake_meta))

    def _raise_workspace_error(*_args, **_kwargs):
        raise WorkspacePolicyError(
            "workspace_ambiguous: ambiguous project",
            status_code=409,
        )

    monkeypatch.setattr("web.routes.chat._apply_workspace_policy", _raise_workspace_error)

    stream_response = await server_module.chat_stream(
        server_module.ChatRequest(message="workspace-ambiguous-stream", use_agent=False)
    )

    payloads = []
    async for chunk in stream_response.body_iterator:
        text = chunk.decode("utf-8") if isinstance(chunk, (bytes, bytearray)) else str(chunk)
        for line in text.splitlines():
            if line.startswith("data: "):
                payloads.append(json.loads(line[6:]))

    assert payloads
    error_event = payloads[0]
    assert error_event["type"] == "error"
    assert "workspace_ambiguous" in error_event["content"]
