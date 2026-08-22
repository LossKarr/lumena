import pytest


def test_claude_sonnet_5_catalog_and_capabilities():
    from src.llm.providers import ProviderType, get_model_config

    cfg = get_model_config("claude-sonnet-5")

    assert cfg is not None
    assert cfg.provider == ProviderType.ANTHROPIC
    assert cfg.model_id == "claude-sonnet-5"
    assert cfg.context_window == 1_000_000
    assert cfg.max_output_tokens == 128_000
    assert cfg.supports_vision is True
    assert cfg.supports_image_generation is False
    assert "vision_describe" in cfg.capabilities
    assert "tool_calling" in cfg.capabilities
    assert "reasoning" in cfg.capabilities


def test_claude_sonnet_5_skills_profile_and_fallbacks():
    from src.llm.model_profile import get_model_profile
    from src.llm.providers import MODEL_SKILLS, get_model_fallbacks

    assert MODEL_SKILLS["claude-sonnet-5"]["code"] > MODEL_SKILLS["claude-sonnet-4.6"]["code"]
    assert MODEL_SKILLS["claude-sonnet-5"]["vision"] > 0

    profile = get_model_profile("claude-sonnet-5")
    assert profile.parser_severity == "strict"
    assert profile.thought_leak_risk == "low"
    assert profile.tool_call_quality == "excellent"
    assert profile.retry_on_empty is False

    fallbacks = get_model_fallbacks("claude-sonnet-5")
    assert fallbacks[:2] == ["claude-sonnet-4.6", "claude-opus-4.8"]
    assert "nvidia-nemotron-3-ultra-550b-a55b" in fallbacks

    assert "claude-sonnet-5" in get_model_fallbacks("claude-fable-5")
    assert "claude-sonnet-5" in get_model_fallbacks("claude-mythos-5")


def test_claude_sonnet_5_config_lists_and_not_image_generation():
    from web.routes.config import _CONFIG_SCHEMA

    schema = {entry["key"]: entry for entry in _CONFIG_SCHEMA}

    assert "claude-sonnet-5" in schema["LUMENA_BRAIN_VISION"]["options"]
    assert "claude-sonnet-5" in schema["LUMENA_BRAIN_CODE"]["options"]
    assert "claude-sonnet-5" in schema["LUMENA_BRAIN_WEB"]["options"]
    assert "claude-sonnet-5" not in schema["LUMENA_BRAIN_IMAGE_GEN"]["options"]


def test_claude_sonnet_5_setup_wizard_recommendations():
    from pathlib import Path

    setup_text = Path("web/routes/setup.py").read_text(encoding="utf-8")

    assert '"claude-sonnet-5"' in setup_text


def test_anthropic_sampling_helper_covers_sonnet_5():
    from src.llm.multi_provider import (
        _anthropic_model_disallows_sampling,
        _strip_anthropic_sampling_params,
    )

    assert _anthropic_model_disallows_sampling("claude-sonnet-5") is True
    assert _anthropic_model_disallows_sampling("claude-sonnet-4-6") is False

    payload = {
        "model": "claude-sonnet-5",
        "temperature": 0.7,
        "top_p": 0.9,
        "top_k": 40,
        "max_tokens": 128,
    }
    _strip_anthropic_sampling_params(payload)

    assert "temperature" not in payload
    assert "top_p" not in payload
    assert "top_k" not in payload
    assert payload["max_tokens"] == 128


@pytest.mark.asyncio
async def test_anthropic_result_payload_strips_sampling_for_sonnet_5(monkeypatch):
    from src.llm.multi_provider import MultiProviderLLM

    captured = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

    class _HTTP:
        async def post(self, url, headers=None, json=None):
            captured["payload"] = dict(json)
            return _Response()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    llm = MultiProviderLLM(model_name="claude-sonnet-5")
    llm._http = _HTTP()

    result = await llm._chat_anthropic_result(
        [{"role": "user", "content": "hello"}],
        temperature=0.7,
        max_tokens=128,
        model="claude-sonnet-5",
    )

    assert result["text"] == "ok"
    assert captured["payload"]["model"] == "claude-sonnet-5"
    assert "temperature" not in captured["payload"]
    assert "top_p" not in captured["payload"]
    assert "top_k" not in captured["payload"]


@pytest.mark.asyncio
async def test_anthropic_tools_refusal_uses_safe_fallback_and_strips_sampling(monkeypatch):
    from unittest.mock import AsyncMock

    from src.llm.multi_provider import MultiProviderLLM

    captured = {}

    class _Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {"stop_reason": "refusal", "content": []}

    class _HTTP:
        async def post(self, url, headers=None, json=None):
            captured["payload"] = dict(json)
            return _Response()

    class _ToolSystem:
        def get_tools_for_provider(self, provider):
            return []

        def get_tools_prompt_section(self):
            return "tools"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    llm = MultiProviderLLM(model_name="claude-sonnet-5")
    llm._http = _HTTP()
    llm.chat = AsyncMock(return_value="fallback ok")

    result = await llm._chat_anthropic_with_tools(
        [{"role": "user", "content": "hello"}],
        _ToolSystem(),
        temperature=0.7,
        max_tokens=128,
        max_iterations=1,
    )

    assert result == "fallback ok"
    assert captured["payload"]["model"] == "claude-sonnet-5"
    assert "temperature" not in captured["payload"]
    assert "top_p" not in captured["payload"]
    assert "top_k" not in captured["payload"]
    llm.chat.assert_awaited_once()
