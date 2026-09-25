"""Contrats 2026 OpenAI, Anthropic et Google (MOD-3)."""

from __future__ import annotations

import pytest


def test_gpt6_astra_catalog_contract():
    from src.llm.providers import ModelLifecycle, ProviderType, get_model_config

    cfg = get_model_config("gpt-6-astra")
    assert cfg is not None
    assert cfg.provider == ProviderType.OPENAI
    assert cfg.model_id == "gpt-6-astra"
    assert cfg.context_window == 1_050_000
    assert cfg.max_output_tokens == 128_000
    assert cfg.lifecycle == ModelLifecycle.STABLE
    assert cfg.pricing is not None
    assert cfg.pricing.input_per_million == 10.0
    assert cfg.pricing.cached_input_per_million == 1.0
    assert cfg.pricing.cache_write_per_million == 12.5
    assert cfg.pricing.output_per_million == 50.0
    assert cfg.pricing.long_context_threshold == 272_000
    assert cfg.pricing.verified_on == "2026-09-20"
    assert {"reasoning", "computer_use", "tool_calling", "long_context"}.issubset(cfg.capabilities)


def test_gpt6_sol_and_luna_catalog_contracts():
    from src.llm.providers import ModelLifecycle, ProviderType, get_model_config

    expected = {
        "gpt-6-sol": (2.0, 0.20, 2.50, 10.0),
        "gpt-6-luna": (0.10, 0.01, 0.125, 0.50),
    }
    for name, prices in expected.items():
        cfg = get_model_config(name)
        assert cfg is not None, name
        assert cfg.provider == ProviderType.OPENAI
        assert cfg.model_id == name
        assert cfg.context_window == 1_050_000
        assert cfg.max_output_tokens == 128_000
        assert cfg.lifecycle == ModelLifecycle.STABLE
        assert cfg.pricing is not None
        assert (
            cfg.pricing.input_per_million,
            cfg.pricing.cached_input_per_million,
            cfg.pricing.cache_write_per_million,
            cfg.pricing.output_per_million,
        ) == prices
        assert cfg.pricing.long_context_threshold == 272_000
        assert cfg.pricing.long_context_input_multiplier == 2.0
        assert cfg.pricing.long_context_output_multiplier == 1.5
        assert {"reasoning", "computer_use", "tool_calling", "responses_api"}.issubset(cfg.capabilities)


def test_anthropic_opus55_and_mythos51_catalog_contracts_and_alias():
    from src.llm.providers import ModelLifecycle, ProviderType, get_model_config

    opus = get_model_config("claude-opus-5.5")
    assert opus is not None
    assert opus.provider == ProviderType.ANTHROPIC
    assert opus.model_id == "claude-opus-5-5"
    assert opus.context_window == 1_000_000
    assert opus.max_output_tokens == 128_000
    assert opus.lifecycle == ModelLifecycle.STABLE
    assert opus.pricing is not None
    assert opus.pricing.input_per_million == 4.0
    assert opus.pricing.output_per_million == 20.0
    assert "computer_toolset_20260801" in opus.capabilities

    mythos = get_model_config("claude-mythos-5.1")
    assert mythos is not None
    assert mythos.model_id == "claude-mythos-5-1"
    assert mythos.lifecycle == ModelLifecycle.LIMITED
    assert mythos.is_selectable() is False
    assert mythos.is_fallback_eligible() is False
    assert mythos.pricing is not None
    assert mythos.pricing.input_per_million == 10.0
    assert mythos.pricing.cached_input_per_million == 0.25
    assert mythos.pricing.output_per_million == 50.0
    assert get_model_config("claude-mythos-5") is mythos


def test_new_catalog_pricing_estimations_and_image_exclusion():
    from src.llm.providers import get_model_config

    sol = get_model_config("gpt-6-sol")
    assert sol.pricing.estimate_cost(input_tokens=100_000, output_tokens=10_000) == 0.3
    assert sol.pricing.estimate_cost(input_tokens=300_000, output_tokens=10_000) == 1.35

    for name in ("gpt-6-sol", "gpt-6-luna", "claude-opus-5.5", "claude-mythos-5.1"):
        cfg = get_model_config(name)
        assert cfg.supports_image_generation is False
        assert cfg.supports_video_generation is False


def test_new_fallback_chains_are_ordered_bounded_and_keep_nvidia_last():
    from src.llm.providers import get_model_fallbacks

    expected_prefixes = {
        "gpt-6-astra": ["gpt-6-sol", "gpt-5.6-sol", "claude-opus-5.5"],
        "gpt-6-sol": ["gpt-6-luna", "gpt-5.6-terra", "claude-sonnet-5"],
        "gpt-6-luna": ["deepseek-flash", "qwen3-8b"],
        "claude-fable-5.1": ["claude-opus-5.5", "claude-sonnet-5", "gpt-6-sol"],
        "claude-mythos-5.1": ["claude-fable-5.1", "claude-opus-5.5", "claude-sonnet-5"],
        "claude-opus-5.5": ["claude-sonnet-5", "gpt-6-sol", "deepseek-flash"],
    }
    for root, prefix in expected_prefixes.items():
        chain = get_model_fallbacks(root)
        assert chain[: len(prefix)] == prefix
        nvidia_positions = [i for i, model in enumerate(chain) if model.startswith("nvidia-")]
        if nvidia_positions:
            assert nvidia_positions == list(range(nvidia_positions[0], len(chain)))
        assert "claude-mythos-5.1" not in chain


def test_global_default_remains_deepseek_flash(monkeypatch):
    from src.llm.multi_provider import MultiProviderLLM

    for name in ("LUMENA_DEFAULT_MODEL", "DEFAULT_MODEL", "LUMENA_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert MultiProviderLLM._resolve_initial_model_name(None) == "deepseek-flash"


def test_astra_responses_payload_strips_incompatible_sampling_and_converts_tools():
    from src.llm.multi_provider import MultiProviderLLM

    payload = MultiProviderLLM._build_openai_responses_payload(
        "gpt-6-astra",
        [{"role": "system", "content": "safe"}, {"role": "user", "content": "run"}],
        max_tokens=123,
        tools=[{
            "type": "function",
            "function": {
                "name": "probe",
                "description": "Probe",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
        reasoning_effort="none",
    )

    assert payload["model"] == "gpt-6-astra"
    assert payload["max_output_tokens"] == 123
    assert payload["reasoning"] == {"effort": "low"}
    assert payload["tools"] == [{
        "type": "function",
        "name": "probe",
        "description": "Probe",
        "parameters": {"type": "object", "properties": {}},
    }]
    assert payload["input"][0]["role"] == "developer"
    assert not {"temperature", "top_p", "top_logprobs", "logprobs"}.intersection(payload)


def test_gpt6_responses_transport_is_capability_driven_and_effort_is_model_safe():
    from src.llm.multi_provider import MultiProviderLLM

    assert MultiProviderLLM._openai_uses_responses("gpt-6-astra") is True
    assert MultiProviderLLM._openai_uses_responses("gpt-6-sol") is True
    assert MultiProviderLLM._openai_uses_responses("gpt-6-luna") is True
    assert MultiProviderLLM._openai_uses_responses("gpt-4o") is False

    messages = [{"role": "system", "content": "safe"}, {"role": "user", "content": "run"}]
    sol = MultiProviderLLM._build_openai_responses_payload(
        "gpt-6-sol", messages, reasoning_effort="none"
    )
    luna = MultiProviderLLM._build_openai_responses_payload(
        "gpt-6-luna", messages, reasoning_effort="none"
    )
    astra = MultiProviderLLM._build_openai_responses_payload(
        "gpt-6-astra", messages, reasoning_effort="none"
    )
    assert sol["reasoning"] == {"effort": "none"}
    assert luna["reasoning"] == {"effort": "none"}
    assert astra["reasoning"] == {"effort": "low"}
    assert all(payload["input"][0]["role"] == "developer" for payload in (sol, luna, astra))


@pytest.mark.asyncio
async def test_gpt6_sol_normal_call_uses_responses_and_normalizes_usage(monkeypatch):
    from src.llm.multi_provider import MultiProviderLLM

    captured = {}

    class Response:
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "status": "completed",
                "model": "gpt-6-sol",
                "output": [{
                    "type": "message",
                    "content": [{"type": "output_text", "text": "ok"}],
                }],
                "usage": {"input_tokens": 7, "output_tokens": 3},
            }

    class HTTP:
        async def post(self, url, headers=None, json=None):
            captured["url"] = url
            captured["payload"] = dict(json)
            return Response()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    llm = MultiProviderLLM(model_name="gpt-6-sol")
    llm._http = HTTP()
    result = await llm._chat_openai_result(
        [{"role": "user", "content": "hello"}], max_tokens=123
    )

    assert captured["url"] == "https://api.openai.com/v1/responses"
    assert captured["payload"]["max_output_tokens"] == 123
    assert result["text"] == "ok"
    assert result["prompt_tokens"] == 7
    assert result["completion_tokens"] == 3


@pytest.mark.asyncio
async def test_openai_responses_stream_reads_only_public_text(monkeypatch):
    from src.llm.multi_provider import MultiProviderLLM

    class StreamResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def raise_for_status(self):
            return None

        async def aiter_lines(self):
            yield 'data: {"type":"response.reasoning.delta","delta":"private"}'
            yield 'data: {"type":"response.output_text.delta","delta":"bon"}'
            yield 'data: {"type":"response.output_text.delta","delta":"jour"}'
            yield 'data: {"type":"response.completed"}'

    class HTTP:
        def stream(self, method, url, headers=None, json=None):
            return StreamResponse()

    llm = MultiProviderLLM(model_name="gpt-6-luna")
    llm._http = HTTP()
    chunks = [
        chunk async for chunk in llm._stream_responses_api(
            [{"role": "user", "content": "hello"}],
            url="https://api.openai.com/v1/responses",
            api_key="test-key",
            model="gpt-6-luna",
            max_tokens=32,
            payload_builder=llm._build_openai_responses_payload,
        )
    ]
    assert chunks == ["bon", "jour"]


@pytest.mark.asyncio
async def test_gpt6_sol_tool_loop_batches_calls_and_replays_response_items(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from src.llm.multi_provider import MultiProviderLLM

    payloads = []

    class Response:
        def __init__(self, body):
            self.body = body

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    bodies = iter([
        {
            "status": "completed",
            "output": [
                {"type": "reasoning", "id": "reasoning-1", "summary": []},
                {"type": "function_call", "call_id": "call-1", "name": "one", "arguments": "{}"},
                {"type": "function_call", "call_id": "call-2", "name": "two", "arguments": "{\"x\":2}"},
            ],
        },
        {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}],
        },
    ])

    class HTTP:
        async def post(self, url, headers=None, json=None):
            payloads.append(dict(json))
            return Response(next(bodies))

    tool_system = SimpleNamespace(
        get_tools_for_provider=lambda provider: [{
            "type": "function",
            "function": {"name": "one", "parameters": {"type": "object", "properties": {}}},
        }],
        get_tools_prompt_section=lambda: "",
        execute_tool=AsyncMock(side_effect=[
            SimpleNamespace(success=True, output="first", error=None),
            SimpleNamespace(success=True, output="second", error=None),
        ]),
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    llm = MultiProviderLLM(model_name="gpt-6-sol")
    llm._http = HTTP()

    text = await llm._chat_openai_with_tools(
        [{"role": "user", "content": "run"}], tool_system, 0.7, 64, 2
    )

    assert text == "done"
    second_input = payloads[1]["input"]
    assert {"type": "reasoning", "id": "reasoning-1", "summary": []} in second_input
    assert [item for item in second_input if item.get("type") == "function_call_output"] == [
        {"type": "function_call_output", "call_id": "call-1", "output": "first"},
        {"type": "function_call_output", "call_id": "call-2", "output": "second"},
    ]


def test_missing_active_openai_models_are_selectable_without_becoming_defaults():
    from src.llm.providers import get_model_config

    names = {
        "gpt-5.5-pro", "gpt-5.4-pro", "gpt-5.2", "gpt-5.2-pro",
        "gpt-5.1", "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5-pro",
        "o3-pro", "gpt-4.1-mini",
    }
    for name in names:
        cfg = get_model_config(name)
        assert cfg is not None, name
        assert cfg.model_id == name
        assert cfg.is_selectable() is True


def test_anthropic_fable_51_and_sonnet_45_exact_ids():
    from src.llm.providers import ModelLifecycle, get_model_config

    fable = get_model_config("claude-fable-5.1")
    assert fable is not None
    assert fable.model_id == "claude-fable-5-1"
    assert fable.lifecycle == ModelLifecycle.STABLE
    assert get_model_config("claude-fable-5") is fable
    assert get_model_config("claude-sonnet-4.5").model_id == "claude-sonnet-4-5-20250929"


def test_anthropic_limited_model_is_never_a_fallback():
    from src.llm.providers import ModelLifecycle, get_model_config

    mythos = get_model_config("claude-mythos-5")
    assert mythos.lifecycle == ModelLifecycle.LIMITED
    assert mythos.is_selectable() is False
    assert mythos.is_fallback_eligible() is False


def test_google_current_flash_and_gemma_models_are_present():
    from src.llm.providers import ProviderType, get_model_config

    expected = {
        "gemini-3.8-flash": (1_048_576, 65_536, True),
        "gemini-3.7-flash": (1_048_576, 65_536, True),
        "gemma-4-26b-a4b-it": (128_000, 16_384, True),
        "gemma-4-31b-it": (128_000, 16_384, True),
    }
    for name, (context, output, vision) in expected.items():
        cfg = get_model_config(name)
        assert cfg is not None, name
        assert cfg.provider == ProviderType.GOOGLE
        assert cfg.model_id == name
        assert cfg.context_window == context
        assert cfg.max_output_tokens == output
        assert cfg.supports_vision is vision
