"""Integration contracts for Claude Opus 5 and Kimi K3."""

from pathlib import Path
from types import SimpleNamespace

import pytest


def test_frontier_models_catalog_matches_official_contracts():
    from src.llm.providers import ProviderType, get_model_config

    opus = get_model_config("claude-opus-5")
    assert opus is not None
    assert opus.provider == ProviderType.ANTHROPIC
    assert opus.model_id == "claude-opus-5"
    assert opus.context_window == 1_000_000
    assert opus.max_output_tokens == 128_000
    assert opus.cost_per_million_tokens == 5.0
    assert opus.supports_vision is True
    assert opus.supports_tools is True
    assert opus.supports_image_generation is False
    assert opus.supports_video_generation is False

    kimi = get_model_config("kimi-k3")
    assert kimi is not None
    assert kimi.provider == ProviderType.MOONSHOT
    assert kimi.model_id == "kimi-k3"
    assert kimi.context_window == 1_000_000
    assert kimi.max_output_tokens == 131_072
    assert kimi.cost_per_million_tokens == 3.0
    assert kimi.supports_vision is True
    assert kimi.supports_tools is True
    assert kimi.supports_image_generation is False
    assert kimi.supports_video_generation is False


def test_frontier_profiles_fallbacks_and_provider_defaults_are_stable():
    from src.llm.model_profile import get_model_profile
    from src.llm.providers import (
        MODEL_SKILLS,
        get_default_model_for_provider,
        get_model_fallbacks,
    )

    assert MODEL_SKILLS["claude-opus-5"]["reasoning"] == 99
    assert MODEL_SKILLS["kimi-k3"]["code"] == 95
    assert get_model_profile("claude-opus-5").tool_call_quality == "excellent"
    assert get_model_profile("kimi-k3").retry_on_empty is True
    assert get_model_fallbacks("claude-opus-5")[:2] == [
        "claude-opus-4.8",
        "claude-sonnet-5",
    ]
    assert get_model_fallbacks("kimi-k3")[:2] == ["kimi-k2.7-code", "kimi-k2.6"]
    assert get_default_model_for_provider("anthropic").name == "claude-opus-4.8"
    assert get_default_model_for_provider("moonshot").name == "kimi-k2.7-code"


def test_frontier_models_are_selectable_but_not_image_generators():
    from web.routes.config import _CONFIG_SCHEMA

    schema = {entry["key"]: entry for entry in _CONFIG_SCHEMA}
    text_keys = (
        "LUMENA_DEFAULT_MODEL",
        "LUMENA_AGENT_CODE_MODEL",
        "LUMENA_AGENT_RESEARCH_MODEL",
        "LUMENA_AGENT_GENERAL_MODEL",
        "LUMENA_BRAIN_VISION",
        "LUMENA_BRAIN_CODE",
        "LUMENA_BRAIN_WEB",
    )
    for model in ("claude-opus-5", "kimi-k3"):
        for key in text_keys:
            assert model in schema[key]["options"], f"{model} absent de {key}"
        assert model not in schema["LUMENA_BRAIN_IMAGE_GEN"]["options"]


def test_frontier_explicit_aliases_do_not_change_historical_generic_aliases():
    from src.core_services.agent_service import AgentService

    service = AgentService.__new__(AgentService)
    assert service._match_model_alias("passe sur claude opus 5") == "claude-opus-5"
    assert service._match_model_alias("utilise kimi k3") == "kimi-k3"
    assert service._match_model_alias("passe sur opus") == "claude-opus-4.8"
    assert service._match_model_alias("utilise kimi") == "kimi-k2.5"


def test_setup_and_cli_surfaces_include_both_models_and_global_kimi_endpoint():
    setup_text = Path("web/routes/setup.py").read_text(encoding="utf-8")
    cli_text = Path("src/cli.py").read_text(encoding="utf-8")

    assert setup_text.count('"claude-opus-5"') == 3
    assert setup_text.count('"kimi-k3"') == 3
    assert "https://platform.kimi.ai/console/api-keys" in setup_text
    assert "MOONSHOT_BASE_URL" in setup_text
    assert "https://api.moonshot.ai/v1" in setup_text
    assert '"6": "claude-opus-5"' in cli_text
    assert '"7": "kimi-k3"' in cli_text


def test_opus5_uses_existing_anthropic_no_sampling_contract():
    from src.llm.multi_provider import (
        _anthropic_model_disallows_sampling,
        _strip_anthropic_sampling_params,
    )

    assert _anthropic_model_disallows_sampling("claude-opus-5") is True
    payload = {
        "model": "claude-opus-5",
        "temperature": 0.7,
        "top_p": 0.9,
        "top_k": 40,
        "max_tokens": 128_000,
    }
    _strip_anthropic_sampling_params(payload)
    assert payload == {"model": "claude-opus-5", "max_tokens": 128_000}


@pytest.mark.asyncio
async def test_opus5_normal_payload_strips_sampling(monkeypatch):
    from src.llm.multi_provider import MultiProviderLLM

    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 2, "output_tokens": 1},
            }

    class HTTP:
        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["payload"] = dict(json)
            return Response()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    llm = MultiProviderLLM(model_name="claude-opus-5")
    llm._http = HTTP()
    result = await llm._chat_anthropic_result(
        [{"role": "user", "content": "hello"}],
        temperature=0.7,
        max_tokens=128_000,
    )

    assert result["text"] == "ok"
    assert captured["payload"]["model"] == "claude-opus-5"
    assert captured["payload"]["max_tokens"] == 128_000
    assert "temperature" not in captured["payload"]


@pytest.mark.asyncio
async def test_opus5_stream_payload_strips_sampling(monkeypatch):
    from src.llm.multi_provider import MultiProviderLLM

    captured = {}

    class StreamResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield 'data: {"type":"content_block_delta","delta":{"text":"ok"}}'
            yield 'data: {"type":"message_stop"}'

    class HTTP:
        def stream(self, method, url, headers=None, json=None):
            captured["payload"] = dict(json)
            return StreamResponse()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    llm = MultiProviderLLM(model_name="claude-opus-5")
    llm._http = HTTP()
    chunks = [
        chunk
        async for chunk in llm._stream_anthropic(
            [{"role": "user", "content": "hello"}],
            temperature=0.7,
            max_tokens=128_000,
        )
    ]

    assert chunks == ["ok"]
    assert captured["payload"]["max_tokens"] == 128_000
    assert "temperature" not in captured["payload"]


def test_kimi_k3_payload_contract_and_k2_regression():
    from src.llm.multi_provider import _build_moonshot_payload

    messages = [{"role": "user", "content": "hello"}]
    k3 = _build_moonshot_payload(
        "kimi-k3",
        messages,
        max_tokens=123,
        temperature=0.7,
        stop=["STOP"],
        stream=True,
    )
    assert k3["max_completion_tokens"] == 123
    assert "max_tokens" not in k3
    assert "temperature" not in k3
    assert k3["stream"] is True
    assert k3["stop"] == ["STOP"]

    k2 = _build_moonshot_payload(
        "kimi-k2.7-code",
        messages,
        max_tokens=123,
        temperature=0.7,
    )
    assert k2["max_tokens"] == 123
    assert "max_completion_tokens" not in k2
    assert "temperature" not in k2

    legacy = _build_moonshot_payload(
        "moonshot-v1-8k",
        messages,
        max_tokens=123,
        temperature=0.7,
    )
    assert legacy["max_tokens"] == 123
    assert legacy["temperature"] == 0.7


@pytest.mark.asyncio
async def test_kimi_k3_normal_request_preserves_reasoning_privately(monkeypatch):
    from src.llm.multi_provider import MultiProviderLLM

    captured = {}

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "choices": [{
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "reasoning_content": "private chain",
                        "content": "public answer",
                    },
                }],
                "usage": {"prompt_tokens": 2, "completion_tokens": 3},
            }

    class HTTP:
        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["payload"] = dict(json)
            return Response()

    monkeypatch.setenv("MOONSHOT_API_KEY", "test-key")
    monkeypatch.setenv("MOONSHOT_BASE_URL", "https://api.moonshot.ai/v1/")
    llm = MultiProviderLLM(model_name="kimi-k3")
    llm._http = HTTP()
    result = await llm._chat_moonshot_result(
        [{"role": "user", "content": "hello"}],
        temperature=0.7,
        max_tokens=321,
    )

    assert result["text"] == "public answer"
    assert captured["url"] == "https://api.moonshot.ai/v1/chat/completions"
    assert captured["payload"]["max_completion_tokens"] == 321
    assert "max_tokens" not in captured["payload"]
    assert "temperature" not in captured["payload"]
    assert llm.get_preserved_assistant_message() == {
        "role": "assistant",
        "reasoning_content": "private chain",
        "content": "public answer",
    }
    assert "reasoning_content" not in llm.get_last_response_meta()


@pytest.mark.asyncio
async def test_kimi_k3_tool_loop_returns_complete_assistant_message(monkeypatch):
    from src.llm.multi_provider import MultiProviderLLM

    payloads = []
    responses = [
        {
            "choices": [{
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": "",
                    "reasoning_content": "decide privately",
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "probe", "arguments": '{"value": 1}'},
                    }],
                },
            }]
        },
        {
            "choices": [{
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "done",
                    "reasoning_content": "finish privately",
                },
            }]
        },
    ]

    class Response:
        def __init__(self, data):
            self.data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self.data

    class HTTP:
        async def post(self, url, headers=None, json=None):
            payloads.append(json)
            return Response(responses.pop(0))

    class ToolSystem:
        def get_tools_for_provider(self, provider):
            return [{"type": "function", "function": {"name": "probe"}}]

        def get_tools_prompt_section(self):
            return "tools"

        async def execute_tool(self, call):
            return SimpleNamespace(success=True, output="tool result", error=None)

    monkeypatch.setenv("MOONSHOT_API_KEY", "test-key")
    llm = MultiProviderLLM(model_name="kimi-k3")
    llm._http = HTTP()
    result = await llm._chat_moonshot_with_tools(
        [{"role": "system", "content": "system"}, {"role": "user", "content": "act"}],
        ToolSystem(),
        temperature=0.7,
        max_tokens=456,
        max_iterations=2,
    )

    assert result == "done"
    assert all("max_completion_tokens" in payload for payload in payloads)
    assert all("temperature" not in payload for payload in payloads)
    assistant_turn = payloads[1]["messages"][-2]
    assert assistant_turn["reasoning_content"] == "decide privately"
    assert assistant_turn["tool_calls"][0]["id"] == "call-1"
    assert llm.get_preserved_assistant_message()["reasoning_content"] == "finish privately"


@pytest.mark.asyncio
async def test_kimi_k3_stream_hides_reasoning_and_preserves_it(monkeypatch):
    from src.llm.multi_provider import MultiProviderLLM

    captured = {}
    lines = [
        'data: {"choices":[{"delta":{"reasoning_content":"private "}}]}',
        'data: {"choices":[{"delta":{"reasoning_content":"stream","content":"hello"}}]}',
        'data: {"choices":[{"delta":{"content":" world"}}]}',
        "data: [DONE]",
    ]

    class StreamResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            for line in lines:
                yield line

    class HTTP:
        def stream(self, method, url, headers=None, json=None):
            captured["payload"] = dict(json)
            return StreamResponse()

    monkeypatch.setenv("MOONSHOT_API_KEY", "test-key")
    llm = MultiProviderLLM(model_name="kimi-k3")
    llm._http = HTTP()
    chunks = [
        chunk
        async for chunk in llm._stream_openai_compat(
            [{"role": "user", "content": "hello"}],
            0.7,
            789,
            url="https://api.moonshot.ai/v1/chat/completions",
            api_key="test-key",
            model="kimi-k3",
        )
    ]

    assert chunks == ["hello", " world"]
    assert captured["payload"]["max_completion_tokens"] == 789
    assert "temperature" not in captured["payload"]
    assert llm.get_preserved_assistant_message() == {
        "role": "assistant",
        "content": "hello world",
        "reasoning_content": "private stream",
    }


@pytest.mark.asyncio
async def test_kimi_k3_vision_uses_the_same_payload_contract(monkeypatch, tmp_path):
    from src.llm.multi_provider import MultiProviderLLM

    captured = {}
    image_path = tmp_path / "probe.png"
    image_path.write_bytes(b"not-a-real-png-but-valid-for-payload-testing")

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "image ok"}}]}

    class HTTP:
        async def post(self, url, headers=None, json=None, timeout=None):
            captured["url"] = url
            captured["payload"] = dict(json)
            return Response()

    monkeypatch.setenv("MOONSHOT_API_KEY", "test-key")
    monkeypatch.setenv("LUMENA_BRAIN_VISION", "kimi-k3")
    llm = MultiProviderLLM(model_name="kimi-k3")
    llm._http = HTTP()
    result = await llm.describe_image(str(image_path), prompt="describe")

    assert result == "image ok"
    assert captured["url"] == "https://api.moonshot.ai/v1/chat/completions"
    assert captured["payload"]["model"] == "kimi-k3"
    assert captured["payload"]["max_completion_tokens"] == 1024
    assert "max_tokens" not in captured["payload"]
    assert "temperature" not in captured["payload"]


@pytest.mark.asyncio
async def test_kimi_k3_private_continuity_is_task_local(monkeypatch):
    import asyncio

    from src.llm.multi_provider import MultiProviderLLM

    monkeypatch.setenv("MOONSHOT_API_KEY", "test-key")
    llm = MultiProviderLLM(model_name="kimi-k3")

    async def preserve_and_read(value):
        llm._preserve_assistant_message(
            "kimi-k3",
            {"role": "assistant", "content": value, "reasoning_content": value},
        )
        await asyncio.sleep(0)
        return llm.get_preserved_assistant_message()

    first, second = await asyncio.gather(
        preserve_and_read("first"),
        preserve_and_read("second"),
    )
    assert first["reasoning_content"] == "first"
    assert second["reasoning_content"] == "second"


@pytest.mark.asyncio
async def test_kimi_k3_does_not_use_synthetic_continuation(monkeypatch):
    from src.llm.multi_provider import MultiProviderLLM
    from src.llm.providers import ProviderType

    monkeypatch.setenv("MOONSHOT_API_KEY", "test-key")
    llm = MultiProviderLLM(model_name="kimi-k3")

    async def must_not_run(**kwargs):
        raise AssertionError("Kimi K3 must not use generic continuation")

    monkeypatch.setattr(llm, "_chat_provider_result", must_not_run)
    result = await llm._continue_if_needed(
        provider=ProviderType.MOONSHOT,
        base_messages=[{"role": "user", "content": "write"}],
        temperature=0.7,
        max_tokens=10,
        initial_result={"text": "partial", "finish_reason": "length"},
        model="kimi-k3",
    )

    assert result["text"] == "partial"
    assert result["continuation_used"] is False
    assert result["continuation_steps"] == 0
    assert result["text_may_be_incomplete"] is True
    assert "preserver son contexte de raisonnement" in result["continuation_warning"]


def test_kimi_k3_conversation_continuity_is_model_scoped_and_ram_only():
    from src.core import ConversationContext

    context = ConversationContext()
    context.add_message("user", "question")
    context.add_message(
        "assistant",
        "public answer",
        metadata={
            "_provider_assistant_message": {
                "role": "assistant",
                "content": "public answer",
                "reasoning_content": "private chain",
            }
        },
    )

    k3_history = context.get_history_for_llm(model_name="kimi-k3")
    assert k3_history[-1]["reasoning_content"] == "private chain"
    assert context.get_history_for_llm(model_name="kimi-k2.7-code")[-1] == {
        "role": "assistant",
        "content": "public answer",
    }
    assert context.get_recent(1) == [{"role": "assistant", "content": "public answer"}]
