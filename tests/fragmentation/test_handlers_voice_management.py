from __future__ import annotations

import json

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.voice_management import (
    apply_voice_recommendation_handler,
    diagnose_voice_handler,
    get_voice_management_handler_defs,
    inspect_voice_runtime_handler,
    recommend_voice_configuration_handler,
    rollback_voice_configuration_handler,
)
from src.voice.v2.management import get_voice_recommendation_broker
from src.runtime.context import RuntimeContext, pop_runtime_context, push_runtime_context


def test_voice_management_handlers_are_registered_with_bounded_schemas():
    defs = get_voice_management_handler_defs()
    assert {item.name for item in defs} == {
        "inspect_voice_runtime",
        "diagnose_voice",
        "recommend_voice_configuration",
        "apply_voice_recommendation",
        "verify_voice_configuration",
        "rollback_voice_configuration",
    }
    assert all(item.category == "voice" for item in defs)


@pytest.mark.asyncio
async def test_recommend_handler_returns_facts_and_one_shot_ticket(monkeypatch):
    monkeypatch.setattr(
        "src.reasoning.handlers.voice_management._snapshot",
        lambda: {
            "hardware": {"cpu_count": 8, "ram_gb": 16, "cuda_ready": False, "gpu": {}},
            "components": {"qwen3_tts": {"qualified": False}, "audio_frontend": {}},
            "timings": {"event_count": 0},
        },
    )
    result = await recommend_voice_configuration_handler(HandlerContext(), goal="fast")
    payload = json.loads(result.output)
    assert result.success is True
    assert payload["recommendation"]["updates"]["LUMENA_VOICE_TTS_ENGINE"] == "piper"
    assert payload["recommendation"]["recommendation_id"]


@pytest.mark.asyncio
async def test_inspection_and_diagnostic_report_runtime_facts_without_inventing(monkeypatch):
    snapshot = {
        "lifecycle": {"running": True, "state": "running", "backend": "v2"},
        "configuration": {"settings_pending": False, "effective": {"voice": {}}},
        "engine": {
            "requested": "qwen3",
            "effective": "piper",
            "fallback_count": 1,
            "fallback_reason": "qwen_not_qualified",
        },
        "components": {"qwen3_tts": {}, "audio_frontend": {}},
        "timings": {"event_count": 4},
    }
    monkeypatch.setattr("src.reasoning.handlers.voice_management._snapshot", lambda: snapshot)
    inspected = json.loads((await inspect_voice_runtime_handler(HandlerContext())).output)
    diagnosed = json.loads((await diagnose_voice_handler(HandlerContext())).output)
    assert inspected["snapshot"]["engine"] == snapshot["engine"]
    assert diagnosed["diagnostic"]["healthy"] is False
    assert diagnosed["diagnostic"]["issues"][0]["code"] == "tts_fallback_active"


@pytest.mark.asyncio
async def test_apply_rejects_missing_human_intent_without_consuming_ticket(monkeypatch):
    ticket = get_voice_recommendation_broker().issue(
        {
            "goal": "fast",
            "profile_id": "balanced",
            "updates": {"LUMENA_VOICE_TTS_ENGINE": "piper"},
        }
    )["recommendation_id"]
    ctx = HandlerContext(original_user_query="Donne-moi simplement ton diagnostic")
    result = await apply_voice_recommendation_handler(ctx, ticket)
    assert result.success is False
    assert result.status_code == "explicit_voice_change_request_required"
    assert get_voice_recommendation_broker().consume(ticket) is not None


@pytest.mark.asyncio
async def test_mission_cannot_mutate_voice(monkeypatch):
    ticket = get_voice_recommendation_broker().issue(
        {
            "goal": "fast",
            "profile_id": "balanced",
            "updates": {"LUMENA_VOICE_TTS_ENGINE": "piper"},
        }
    )["recommendation_id"]
    ctx = HandlerContext(
        original_user_query="Règle ta voix pour parler plus vite",
        is_mission_run=True,
        runtime_task_id="mission-1",
    )
    result = await apply_voice_recommendation_handler(ctx, ticket)
    assert result.success is False
    assert result.status_code == "explicit_voice_change_request_required"


class _Manager:
    active_backend = "v2"

    def get_status(self):
        return {"running": True, "runtime_state": "idle", "settings_pending": False}


@pytest.mark.asyncio
@pytest.mark.parametrize("channel,expected_deferred", [("web", False), ("telegram", False), ("voice", True)])
async def test_owner_channels_share_the_same_tool_and_voice_defers_restart(
    monkeypatch,
    channel,
    expected_deferred,
):
    captured = {}

    async def apply_stub(recommendation, **kwargs):
        captured.update(kwargs)
        return {"state": "scheduled" if kwargs["defer_until_safe"] else "verified"}

    monkeypatch.setattr("src.reasoning.handlers.voice_management.apply_voice_recommendation_transaction", apply_stub)
    monkeypatch.setattr("src.reasoning.handlers.voice_management._manager", lambda: _Manager())
    monkeypatch.setattr(
        "src.reasoning.handlers.voice_management._config_dependencies",
        lambda: (lambda: {"LUMENA_VOICE_MANAGEMENT_MODE": "assisted"}, lambda _: None, lambda u: (u, [])),
    )
    ticket = get_voice_recommendation_broker().issue(
        {
            "goal": "fast",
            "profile_id": "balanced",
            "updates": {"LUMENA_VOICE_TTS_ENGINE": "piper"},
        }
    )["recommendation_id"]
    runtime = RuntimeContext.build(
        channel=channel,
        client="test",
        request_id="r",
        conversation_id="c",
        message_id="m",
        workspace_policy="default",
        task_id=None,
        client_caps={},
        workspace_path=None,
        active_file_path=None,
        open_files=[],
        resolved_workspace=None,
        resolved_date=None,
        resolution_reason=None,
        user_role="owner",
    )
    token = push_runtime_context(runtime)
    try:
        result = await apply_voice_recommendation_handler(
            HandlerContext(lumena=object(), original_user_query="Règle ta voix pour parler plus vite"), ticket
        )
    finally:
        pop_runtime_context(token)
    assert result.success is True
    assert captured["defer_until_safe"] is expected_deferred


@pytest.mark.asyncio
async def test_unpaired_external_guest_cannot_change_voice():
    ticket = get_voice_recommendation_broker().issue(
        {
            "goal": "fast",
            "updates": {"LUMENA_VOICE_TTS_ENGINE": "piper"},
        }
    )["recommendation_id"]
    runtime = RuntimeContext.build(
        channel="telegram",
        client="test",
        request_id="r",
        conversation_id="c",
        message_id="m",
        workspace_policy="default",
        task_id=None,
        client_caps={},
        workspace_path=None,
        active_file_path=None,
        open_files=[],
        resolved_workspace=None,
        resolved_date=None,
        resolution_reason=None,
        user_role="guest",
    )
    token = push_runtime_context(runtime)
    try:
        result = await apply_voice_recommendation_handler(
            HandlerContext(original_user_query="Règle ta voix pour parler plus vite"), ticket
        )
    finally:
        pop_runtime_context(token)
    assert result.success is False
    assert result.status_code == "voice_owner_or_admin_required"


@pytest.mark.asyncio
async def test_owner_can_explicitly_rollback_from_an_external_paired_channel(monkeypatch):
    async def rollback_stub(**kwargs):
        assert kwargs["explicitly_authorized"] is True
        return {"ok": True, "state": "restored", "verified": True}

    monkeypatch.setattr(
        "src.reasoning.handlers.voice_management.rollback_last_voice_transaction",
        rollback_stub,
    )
    monkeypatch.setattr("src.reasoning.handlers.voice_management._manager", lambda: _Manager())
    monkeypatch.setattr(
        "src.reasoning.handlers.voice_management._config_dependencies",
        lambda: (lambda: {}, lambda _: None, lambda values: (values, [])),
    )
    runtime = RuntimeContext.build(
        channel="telegram",
        client="test",
        request_id="r",
        conversation_id="c",
        message_id="m",
        workspace_policy="default",
        task_id=None,
        client_caps={},
        workspace_path=None,
        active_file_path=None,
        open_files=[],
        resolved_workspace=None,
        resolved_date=None,
        resolution_reason=None,
        user_role="owner",
    )
    token = push_runtime_context(runtime)
    try:
        result = await rollback_voice_configuration_handler(
            HandlerContext(
                lumena=object(),
                original_user_query="Annule le dernier réglage de ta voix et reviens au précédent",
            )
        )
    finally:
        pop_runtime_context(token)
    assert result.success is True
    assert json.loads(result.output)["result"]["state"] == "restored"
