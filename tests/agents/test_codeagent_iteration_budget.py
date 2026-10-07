from __future__ import annotations

from src.agents.sub_agent import (
    _resolve_code_agent_iteration_budget,
    _resolve_code_agent_max_iter,
)


def test_explicit_codeagent_budget_has_no_arbitrary_ceiling(monkeypatch):
    monkeypatch.setenv("LUMENA_CODE_AGENT_MAX_ITER", "10000")
    assert _resolve_code_agent_max_iter() == 10000
    assert _resolve_code_agent_iteration_budget("ollama/llama3.1:8b") == 10000


def test_invalid_codeagent_budget_falls_back_safely(monkeypatch):
    monkeypatch.setenv("LUMENA_CODE_AGENT_MAX_ITER", "invalid")
    assert _resolve_code_agent_max_iter() == 50


def test_model_profile_remains_a_default_when_user_did_not_configure(monkeypatch):
    monkeypatch.delenv("LUMENA_CODE_AGENT_MAX_ITER", raising=False)
    assert _resolve_code_agent_iteration_budget("ollama/llama3.1:8b") < 50
