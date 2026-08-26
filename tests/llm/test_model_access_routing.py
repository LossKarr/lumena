from __future__ import annotations

import asyncio

import httpx

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
