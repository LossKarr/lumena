"""MOD-8 — audit reproductible et traces d'échec sans secrets."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch


def test_catalog_audit_is_green_and_offline():
    from scripts.audit_model_catalog import build_report

    report = build_report()
    assert report["status"] == "ok", report["errors"]
    assert report["network_used"] is False
    assert report["credentials_serialized"] is False
    assert report["catalog_revision"] == "2026-09-30"
    assert report["text"]["selectable"] > 0
    assert report["image"]["fallbacks"] == report["image"]["auto_eligible"]


def test_model_attempt_trace_redacts_credentials():
    from src.llm.model_access import (
        ModelAccessRef,
        ModelAccessSource,
        ModelAttemptTrace,
    )

    secret = "sk-super-secret-value-123456"
    trace = ModelAttemptTrace(
        candidate=ModelAccessRef(ModelAccessSource.API, "openai", "gpt-6-astra"),
        status="failed",
        reason=f"Authorization: Bearer {secret} api_key={secret}",
    ).to_dict()
    rendered = repr(trace)
    assert secret not in rendered
    assert "<redacted>" in rendered
    assert trace["candidate"]["model"] == "gpt-6-astra"


def test_canary_is_direct_bounded_and_does_not_record_content():
    from scripts.audit_model_catalog import run_text_canary

    fake = AsyncMock()
    fake._chat_provider_result.return_value = {
        "text": "OK",
        "model_used": "gpt-6-astra",
        "provider_used": "openai",
        "prompt_tokens": 7,
        "completion_tokens": 1,
        "finish_reason": "stop",
    }
    fake.close = AsyncMock()
    with patch("src.llm.multi_provider.MultiProviderLLM", return_value=fake):
        report = asyncio.run(run_text_canary(
            "gpt-6-astra", max_cost_usd=0.01, max_output_tokens=16,
        ))

    assert report["status"] == "passed"
    assert report["fallback_enabled"] is False
    assert report["resolved_model"] == "gpt-6-astra"
    assert report["content_recorded"] is False
    assert fake._chat_provider_result.await_count == 1


def test_canary_refuses_an_insufficient_cost_cap():
    from scripts.audit_model_catalog import run_text_canary

    try:
        asyncio.run(run_text_canary("gpt-6-astra", max_cost_usd=0.0, max_output_tokens=32))
    except ValueError as exc:
        assert "cost_cap_too_low" in str(exc)
    else:
        raise AssertionError("Le canari ne doit pas dépasser son plafond")
