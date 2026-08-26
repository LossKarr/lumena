from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core_services.agent_service import AgentService
from src.llm.codex_chat import CodexChatResult


@pytest.mark.asyncio
async def test_historical_api_chat_path_is_unchanged(monkeypatch):
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "api")
    monkeypatch.setenv("LUMENA_CODEX_SURFACES", "codeagent,chat")
    llm = SimpleNamespace(
        chat_with_tools=AsyncMock(return_value="historical-api-response"),
        get_last_response_meta=MagicMock(
            return_value={"provider_used": "deepseek", "model_used": "deepseek-chat"}
        ),
        max_output_tokens=4096,
    )
    core = SimpleNamespace(llm=llm, tool_system=object())
    service = AgentService(core)

    response, meta, used_codex = await service._run_chat_model(
        [{"role": "user", "content": "Bonjour"}],
        user_message="Bonjour",
        source_channel="web",
    )

    assert response == "historical-api-response"
    assert meta["provider_used"] == "deepseek"
    assert used_codex is False
    llm.chat_with_tools.assert_awaited_once()


@pytest.mark.asyncio
async def test_external_channels_never_use_local_subscription(monkeypatch):
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "chatgpt_codex")
    monkeypatch.setenv("LUMENA_CODEX_SURFACES", "codeagent,chat")
    llm = SimpleNamespace(
        chat=AsyncMock(return_value="telegram-api-response"),
        get_last_response_meta=MagicMock(return_value={"provider_used": "deepseek"}),
    )
    service = AgentService(SimpleNamespace(llm=llm, tool_system=None))
    response, _, used_codex = await service._run_chat_model(
        [{"role": "user", "content": "Bonjour"}],
        user_message="Bonjour",
        source_channel="telegram",
    )
    assert response == "telegram-api-response"
    assert used_codex is False
    llm.chat.assert_awaited_once()


@pytest.mark.asyncio
async def test_local_chat_uses_codex_and_publishes_metadata_without_api(monkeypatch):
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "chatgpt_codex")
    monkeypatch.setenv("LUMENA_CODEX_SURFACES", "codeagent,chat")
    monkeypatch.setattr(
        "src.llm.codex_app_server.get_shared_codex_app_server",
        lambda: SimpleNamespace(is_running=True),
    )
    run = AsyncMock(
        return_value=CodexChatResult(
            response="subscription-response",
            model="account-model",
            meta={
                "provider_requested": "openai-codex",
                "provider_used": "openai-codex",
                "model_requested": "auto",
                "model_used": "account-model",
                "fallback_used": False,
            },
        )
    )
    monkeypatch.setattr("src.llm.codex_chat.run_chat_with_codex_subscription", run)
    llm = SimpleNamespace(
        chat=AsyncMock(side_effect=AssertionError("paid API must not run")),
        chat_with_tools=AsyncMock(side_effect=AssertionError("tools must not run")),
        set_external_response_meta=MagicMock(),
    )
    service = AgentService(SimpleNamespace(llm=llm, tool_system=object()))

    response, meta, used_codex = await service._run_chat_model(
        [{"role": "system", "content": "Lumena"}, {"role": "user", "content": "Salut"}],
        user_message="Salut",
        source_channel="web",
    )

    assert response == "subscription-response"
    assert used_codex is True
    assert meta["fallback_used"] is False
    llm.chat.assert_not_awaited()
    llm.chat_with_tools.assert_not_awaited()
    llm.set_external_response_meta.assert_called_once_with(**meta)


@pytest.mark.asyncio
async def test_memory_keeps_history_without_hidden_api_summary(tmp_path):
    memory = SimpleNamespace(remember=MagicMock(), get_fact=MagicMock(return_value=None))
    llm = SimpleNamespace(chat=AsyncMock(side_effect=AssertionError("hidden API call")))
    core = SimpleNamespace(
        memory=memory,
        llm=llm,
        emotion_manager=None,
        data_dir=tmp_path,
    )
    service = AgentService(core)

    await service._save_conversation_to_memory(
        "Je préfère les réponses courtes",
        "Bien reçu, je resterai concise.",
        allow_llm_summary=False,
    )
    await service._llm_extract_facts(
        "Je préfère les réponses courtes",
        "Bien reçu.",
        allow_llm=False,
    )

    llm.chat.assert_not_awaited()
    memory.remember.assert_called_once()
    assert (tmp_path / "memory" / "journal").is_dir()


def test_public_external_metadata_contract_filters_unknown_fields(monkeypatch):
    from src.llm.multi_provider import MultiProviderLLM

    llm = MultiProviderLLM.__new__(MultiProviderLLM)
    import threading

    llm._config = None
    llm._meta_lock = threading.Lock()
    llm._last_response_meta = llm._default_response_meta()
    llm.set_external_response_meta(
        provider_used="openai-codex",
        model_used="account-model",
        fallback_used=False,
        access_token="must-not-be-stored",
    )
    meta = llm.get_last_response_meta()
    assert meta["provider_used"] == "openai-codex"
    assert meta["model_used"] == "account-model"
    assert meta["access_source_used"] == "codex"
    assert meta["billing_source"] == "chatgpt_subscription"
    assert "access_token" not in meta


def test_chat_trace_identity_reports_the_selected_rail(monkeypatch):
    core = SimpleNamespace(
        llm=SimpleNamespace(
            provider=SimpleNamespace(value="deepseek"),
            model_name="deepseek-chat",
        )
    )
    assert AgentService._chat_trace_identity(core, False) == (
        "deepseek",
        "deepseek-chat",
    )

    monkeypatch.setenv("LUMENA_CODEX_DEFAULT_MODEL", "gpt-codex-account")
    assert AgentService._chat_trace_identity(core, True) == (
        "openai-codex",
        "gpt-codex-account",
    )
