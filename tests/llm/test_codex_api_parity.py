from __future__ import annotations

import inspect
from types import SimpleNamespace

from src.core_services.agent_service import AgentService, _effective_agent_llm_meta
from src.llm.execution_router import (
    _build_prompt,
    consume_codex_response_meta,
    peek_codex_response_meta,
    reset_codex_response_meta,
    run_react_with_codex_subscription,
)


class _ContextReact:
    _is_mission_run = False
    task_id = None
    conversation_context = "USER: Nous construisons Lumena."
    active_skills_context = "SKILL frontend-design: produire une interface soignee."

    @staticmethod
    def _build_identity_context(_query: str) -> str:
        return "Tu es Lumena. Souvenir pertinent: Losskarr prefere le francais."

    @staticmethod
    def _mission_allowed_files_meta():
        return []


def test_codex_agent_prompt_keeps_lumena_identity_memory_history_and_skills():
    prompt = _build_prompt(_ContextReact(), "Construis le site", "Construis le site")

    assert "IDENTITE ET MEMOIRE LUMENA" in prompt
    assert "Tu es Lumena" in prompt
    assert "Souvenir pertinent" in prompt
    assert "CONVERSATION LUMENA" in prompt
    assert "Nous construisons Lumena" in prompt
    assert "SKILLS ACTIFS LUMENA" in prompt
    assert "frontend-design" in prompt


def test_agent_metadata_prefers_codex_without_erasing_common_fields():
    merged = _effective_agent_llm_meta(
        {"provider_used": "deepseek", "latency_ms": 12},
        {"provider_used": "openai-codex", "model_used": "gpt-account"},
    )

    assert merged == {
        "provider_used": "openai-codex",
        "model_used": "gpt-account",
        "latency_ms": 12,
    }


def test_agent_codex_postprocessing_is_local_and_does_not_consume_metadata():
    source = inspect.getsource(AgentService.think_and_act)

    assert "reset_codex_response_meta()" in source
    assert "allow_llm_summary=not _used_codex_agent" in source
    assert "allow_llm=not _used_codex_agent" in source
    assert '_codex_run_meta.get("provider_used")' in source
    reset_codex_response_meta()
    # Empty state is safe and remains available to the web route until consumed.
    assert peek_codex_response_meta() == {}
    assert consume_codex_response_meta() == {}


def test_codex_chat_action_uses_agent_only_for_opted_in_local_surfaces(monkeypatch):
    monkeypatch.setenv("LUMENA_OPENAI_ACCESS_MODE", "chatgpt_codex")
    monkeypatch.setenv("LUMENA_CODEX_SURFACES", "codeagent,chat,agent")

    assert AgentService._codex_chat_action_uses_agent(
        "cree un dossier et ecris app.py", "web"
    )
    assert not AgentService._codex_chat_action_uses_agent("comment vas-tu ?", "web")
    assert not AgentService._codex_chat_action_uses_agent(
        "cree un dossier et ecris app.py", "telegram"
    )


def test_codex_agent_uses_private_turn_scope_not_shared_chat_lock():
    source = inspect.getsource(run_react_with_codex_subscription)

    assert "_dedicated_codex_turn_scope" in source
    assert "codex_turn_execution_lock" not in source
