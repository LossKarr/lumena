from __future__ import annotations

import asyncio

import httpx
import pytest

from src.llm.model_access import (
    ModelAccessRef,
    ModelAccessSource,
    ModelFailureKind,
    append_unique_attempt,
    classify_model_failure,
    failure_allows_fallback,
)


def _http_error(status: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://api.example.test/chat")
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError("failed", request=request, response=response)


def test_failure_classification_separates_recoverable_and_terminal_errors():
    assert classify_model_failure(_http_error(401)) is ModelFailureKind.AUTH
    assert classify_model_failure(_http_error(402)) is ModelFailureKind.QUOTA
    assert classify_model_failure(_http_error(429)) is ModelFailureKind.RATE_LIMIT
    assert classify_model_failure(_http_error(503)) is ModelFailureKind.TRANSIENT
    assert classify_model_failure(_http_error(400)) is ModelFailureKind.INVALID_REQUEST
    assert classify_model_failure(RuntimeError("anthropic_refusal: blocked")) is ModelFailureKind.REFUSAL
    assert classify_model_failure(RuntimeError("model_refusal:openai:gpt-6-sol")) is ModelFailureKind.REFUSAL
    assert classify_model_failure(asyncio.CancelledError()) is ModelFailureKind.CANCELLED

    assert failure_allows_fallback(ModelFailureKind.QUOTA)
    assert failure_allows_fallback(ModelFailureKind.TRANSIENT)
    assert not failure_allows_fallback(ModelFailureKind.INVALID_REQUEST)
    assert not failure_allows_fallback(ModelFailureKind.CANCELLED)
    assert not failure_allows_fallback(ModelFailureKind.REFUSAL)


def test_attempt_identity_includes_access_source():
    api = ModelAccessRef(
        source=ModelAccessSource.API,
        provider="openai",
        model="gpt-5.6-sol",
        billing="api",
    )
    codex = ModelAccessRef(
        source=ModelAccessSource.CODEX,
        provider="openai-codex",
        model="gpt-5.6-sol",
        billing="subscription",
    )
    attempts: list[ModelAccessRef] = []

    assert append_unique_attempt(attempts, api)
    assert append_unique_attempt(attempts, codex)
    assert not append_unique_attempt(attempts, api)
    assert [attempt.qualified_id for attempt in attempts] == [
        "api:openai:gpt-5.6-sol",
        "codex:openai-codex:gpt-5.6-sol",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("message", ["400 invalid request", "model_refusal:openai:gpt-6-sol"])
async def test_terminal_failure_never_calls_a_fallback(monkeypatch, message):
    from src.llm.multi_provider import MultiProviderLLM

    llm = MultiProviderLLM("gpt-6-sol")
    calls = []

    async def fail_once(*, provider, model, **kwargs):
        calls.append((provider.value, model))
        raise RuntimeError(message)

    monkeypatch.setattr(llm, "_chat_provider_result", fail_once)
    try:
        result = await llm.chat([{"role": "user", "content": "hello"}])
    finally:
        await llm.close()

    assert result.startswith("[Refus]") if message.startswith("model_refusal") else result.startswith("[Erreur]")
    assert calls == [("openai", "gpt-6-sol")]
    assert llm.get_last_response_meta()["fallback_used"] is False


@pytest.mark.asyncio
async def test_provider_quota_skips_sibling_models_before_cross_provider_fallback(monkeypatch):
    from src.llm.multi_provider import MultiProviderLLM
    from src.llm.providers import ProviderType

    monkeypatch.setenv("LUMENA_CODEX_API_RESCUE", "0")
    monkeypatch.setattr("src.llm.multi_provider.check_api_key", lambda _provider: True)
    llm = MultiProviderLLM("gpt-6-astra")
    calls = []

    async def provider_result(*, provider, model, **kwargs):
        calls.append((provider, model))
        if len(calls) == 1:
            raise RuntimeError("402 quota exhausted")
        if provider is ProviderType.OPENAI:
            raise AssertionError("quota OpenAI must skip OpenAI sibling models")
        return {
            "text": "cross-provider ok",
            "provider_used": provider.value,
            "model_used": model,
            "finish_reason": "stop",
        }

    monkeypatch.setattr(llm, "_chat_provider_result", provider_result)
    try:
        result = await llm.chat([{"role": "user", "content": "hello"}])
    finally:
        await llm.close()

    assert result == "cross-provider ok"
    assert calls[0] == (ProviderType.OPENAI, "gpt-6-astra")
    assert calls[1][0] is ProviderType.ANTHROPIC


@pytest.mark.asyncio
async def test_terminal_failure_inside_fallback_stops_the_whole_cascade(monkeypatch):
    from src.llm.multi_provider import MultiProviderLLM
    from src.llm.providers import ProviderType

    monkeypatch.setenv("LUMENA_CODEX_API_RESCUE", "0")
    monkeypatch.setattr("src.llm.multi_provider.check_api_key", lambda _provider: True)
    llm = MultiProviderLLM("gpt-6-astra")
    calls = []

    async def provider_result(*, provider, model, **kwargs):
        calls.append((provider, model))
        if len(calls) == 1:
            raise RuntimeError("503 provider temporarily unavailable")
        raise RuntimeError("400 invalid request in fallback payload")

    monkeypatch.setattr(llm, "_chat_provider_result", provider_result)
    try:
        result = await llm.chat([{"role": "user", "content": "hello"}])
    finally:
        await llm.close()

    assert result.startswith("[Erreur] 400 invalid request")
    assert calls == [
        (ProviderType.OPENAI, "gpt-6-astra"),
        (ProviderType.OPENAI, "gpt-6.1-sol"),
    ]
    meta = llm.get_last_response_meta()
    assert meta["fallback_used"] is True
    assert meta["finish_reason"] == ModelFailureKind.INVALID_REQUEST.value
