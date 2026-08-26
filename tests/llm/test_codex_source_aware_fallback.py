from __future__ import annotations

import httpx
import pytest
from types import SimpleNamespace

from src.llm import execution_router
from src.llm.codex_subscription import (
    CodexSubscriptionSettings,
    OpenAIAccessMode,
)
from src.llm.execution_router import CodexReActUnavailable
from src.llm.multi_provider import MultiProviderLLM
from src.llm.providers import ProviderType


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.openai.com/v1/chat/completions")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("openai failed", request=request, response=response)


@pytest.mark.asyncio
async def test_openai_api_quota_failure_uses_configured_codex_subscription(
    monkeypatch,
):
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "api")
    monkeypatch.setenv("LUMENA_CODEX_DEFAULT_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("LUMENA_CODEX_API_RESCUE", "1")
    llm = MultiProviderLLM("gpt-5.6-luna")

    async def fail_api(**_kwargs):
        raise _http_error(402)

    calls = []

    async def codex_rescue(messages, **kwargs):
        calls.append((messages, kwargs))
        return {
            "text": "reponse abonnement",
            "provider_used": "openai-codex",
            "model_used": "gpt-5.6-luna",
            "finish_reason": "stop",
            "access_source": "codex",
            "billing_source": "chatgpt_subscription",
        }

    monkeypatch.setattr(llm, "_chat_provider_result", fail_api)
    monkeypatch.setattr(execution_router, "chat_with_codex_rescue", codex_rescue)
    try:
        answer = await llm.chat([{"role": "user", "content": "bonjour"}])
    finally:
        await llm.close()

    assert answer == "reponse abonnement"
    assert calls[0][1]["requested_model"] == "gpt-5.6-luna"
    meta = llm.get_last_response_meta()
    assert meta["provider_requested"] == "openai"
    assert meta["provider_used"] == "openai-codex"
    assert meta["access_source_requested"] == "api"
    assert meta["access_source_used"] == "codex"
    assert meta["billing_source"] == "chatgpt_subscription"
    assert meta["fallback_used"] is True
    assert meta["fallback_attempts"][-1]["candidate"]["source"] == "codex"


@pytest.mark.asyncio
async def test_codex_rescue_is_dormant_without_previous_codex_configuration(
    monkeypatch,
):
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "api")
    monkeypatch.delenv("LUMENA_CODEX_DEFAULT_MODEL", raising=False)
    monkeypatch.setenv("LUMENA_CODEX_API_RESCUE", "1")
    llm = MultiProviderLLM("gpt-5.6-luna")
    llm.fallback_order = ["openai"]

    async def fail_api(**_kwargs):
        raise _http_error(402)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("Codex must stay dormant")

    monkeypatch.setattr(llm, "_chat_provider_result", fail_api)
    monkeypatch.setattr(execution_router, "chat_with_codex_rescue", forbidden)
    try:
        answer = await llm.chat([{"role": "user", "content": "bonjour"}])
    finally:
        await llm.close()

    assert answer.startswith("[Erreur]")
    assert llm.get_last_response_meta()["access_source_used"] == "api"


@pytest.mark.asyncio
async def test_codex_rescue_can_be_disabled_without_erasing_account_model(monkeypatch):
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "api")
    monkeypatch.setenv("LUMENA_CODEX_DEFAULT_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("LUMENA_CODEX_API_RESCUE", "0")
    llm = MultiProviderLLM("gpt-5.6-luna")
    llm.fallback_order = ["openai"]

    async def fail_api(**_kwargs):
        raise _http_error(402)

    async def forbidden(*_args, **_kwargs):
        raise AssertionError("Disabled Codex rescue must stay dormant")

    monkeypatch.setattr(llm, "_chat_provider_result", fail_api)
    monkeypatch.setattr(execution_router, "chat_with_codex_rescue", forbidden)
    try:
        answer = await llm.chat([{"role": "user", "content": "bonjour"}])
    finally:
        await llm.close()

    assert answer.startswith("[Erreur]")
    assert llm.get_last_response_meta()["access_source_used"] == "api"


@pytest.mark.asyncio
async def test_anthropic_then_openai_api_then_codex_has_one_ordered_chain(
    monkeypatch,
):
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "api")
    monkeypatch.setenv("LUMENA_CODEX_DEFAULT_MODEL", "gpt-5.6-luna")
    monkeypatch.setenv("LUMENA_CODEX_API_RESCUE", "1")
    llm = MultiProviderLLM("claude-opus-5")
    llm.fallback_order = ["anthropic", "openai", "ollama"]
    provider_calls = []

    async def fail_providers(*, provider, **_kwargs):
        provider_calls.append(provider)
        if provider is ProviderType.ANTHROPIC:
            raise RuntimeError("503 anthropic unavailable")
        if provider is ProviderType.OPENAI:
            raise _http_error(402)
        raise AssertionError("Codex success must stop the chain before Ollama")

    async def codex_rescue(_messages, **_kwargs):
        return {
            "text": "secours codex",
            "model_used": "gpt-5.6-luna",
            "finish_reason": "stop",
        }

    monkeypatch.setattr(llm, "_chat_provider_result", fail_providers)
    monkeypatch.setattr("src.llm.multi_provider.get_model_fallbacks", lambda _name: [])
    monkeypatch.setattr(execution_router, "chat_with_codex_rescue", codex_rescue)
    try:
        answer = await llm.chat([{"role": "user", "content": "travail"}])
    finally:
        await llm.close()

    assert answer == "secours codex"
    assert provider_calls == [ProviderType.ANTHROPIC, ProviderType.OPENAI]
    attempts = llm.get_last_response_meta()["fallback_attempts"]
    assert [item["candidate"]["source"] for item in attempts] == [
        "api",
        "api",
        "codex",
    ]


@pytest.mark.asyncio
async def test_code_heavy_autoswitch_is_strictly_deepseek_chat_only(monkeypatch):
    llm = MultiProviderLLM("gpt-5.6-luna")
    captured = []

    async def success(*, provider, model, **_kwargs):
        captured.append((provider, model))
        return {
            "text": "ok",
            "provider_used": provider.value,
            "model_used": model,
            "finish_reason": "stop",
        }

    monkeypatch.setattr(llm, "_is_code_heavy_request", lambda *_a, **_k: (True, "code_task"))
    monkeypatch.setattr(llm, "_chat_provider_result", success)
    try:
        answer = await llm.chat([{"role": "user", "content": "cree un projet"}])
    finally:
        await llm.close()

    assert answer == "ok"
    assert captured == [(ProviderType.OPENAI, "gpt-5.6-luna")]
    assert llm.get_last_response_meta()["auto_switch_used"] is False


@pytest.mark.asyncio
async def test_global_ollama_fallback_is_reported_as_local(monkeypatch):
    llm = MultiProviderLLM("claude-opus-5")
    llm.fallback_order = ["anthropic", "ollama"]

    async def provider_result(*, provider, model, **_kwargs):
        provider_value = getattr(provider, "value", str(provider))
        if provider_value == ProviderType.ANTHROPIC.value:
            raise RuntimeError("503 anthropic unavailable")
        assert provider_value == ProviderType.OLLAMA.value
        return {
            "text": "local ok",
            "provider_used": "ollama",
            "model_used": model,
            "finish_reason": "stop",
        }

    monkeypatch.setattr(llm, "_chat_provider_result", provider_result)
    monkeypatch.setattr("src.llm.multi_provider.get_model_fallbacks", lambda _name: [])
    try:
        answer = await llm.chat([{"role": "user", "content": "travail"}])
    finally:
        await llm.close()

    assert answer == "local ok"
    meta = llm.get_last_response_meta()
    assert meta["access_source_requested"] == "api"
    assert meta["access_source_used"] == "local"
    assert meta["billing_source"] == "local"
    assert [item["status"] for item in meta["fallback_attempts"]] == [
        "failed",
        "success",
    ]


@pytest.mark.asyncio
async def test_codex_quota_is_checked_before_the_rescue_model_turn(monkeypatch):
    events = []

    class FakeBrain:
        def __init__(self, owner, settings):
            del owner
            self.settings = settings
            self.supervisor = object()
            self.model_name = settings.default_model

        async def _ensure_started(self):
            events.append("start")

        async def chat(self, **_kwargs):
            events.append("chat")
            raise AssertionError("The model turn must not start with exhausted quota")

        def get_last_response_meta(self):
            return {}

        async def aclose(self):
            events.append("close")

    class FakeGateway:
        def __init__(self, supervisor):
            assert supervisor is not None

        async def read_rate_limits(self):
            events.append("quota")
            return SimpleNamespace(exhausted=True)

    monkeypatch.setattr(execution_router, "CodexTextBrain", FakeBrain)
    monkeypatch.setattr(execution_router, "CodexSubscriptionGateway", FakeGateway)
    settings = CodexSubscriptionSettings(
        access_mode=OpenAIAccessMode.API,
        default_model="account-model",
        api_rescue_enabled=True,
    )

    with pytest.raises(CodexReActUnavailable, match="Quota.*epuise"):
        await execution_router.chat_with_codex_rescue(
            [{"role": "user", "content": "bonjour"}], settings=settings
        )

    assert events == ["start", "quota", "close"]


@pytest.mark.asyncio
async def test_rescue_keeps_the_users_codex_model_choice(monkeypatch):
    captured = []

    class FakeBrain:
        def __init__(self, owner, settings):
            del owner
            captured.append(settings.default_model)
            self.supervisor = object()
            self.model_name = settings.default_model

        async def _ensure_started(self):
            return None

        async def chat(self, **_kwargs):
            return "ok"

        def get_last_response_meta(self):
            return {"finish_reason": "stop"}

        async def aclose(self):
            return None

    class FakeGateway:
        def __init__(self, _supervisor):
            pass

        async def read_rate_limits(self):
            return SimpleNamespace(exhausted=False)

    monkeypatch.setattr(execution_router, "CodexTextBrain", FakeBrain)
    monkeypatch.setattr(execution_router, "CodexSubscriptionGateway", FakeGateway)
    settings = CodexSubscriptionSettings(
        access_mode=OpenAIAccessMode.API,
        default_model="codex-user-choice",
        api_rescue_enabled=True,
    )

    result = await execution_router.chat_with_codex_rescue(
        [{"role": "user", "content": "bonjour"}],
        requested_model="failed-api-model",
        settings=settings,
    )

    assert captured == ["codex-user-choice"]
    assert result["model_used"] == "codex-user-choice"
