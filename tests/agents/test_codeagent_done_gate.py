import inspect

from src.agents import sub_agent as sa_module


def test_done_gate_has_failure_budget_before_success():
    src = inspect.getsource(sa_module.CodeAgent._single_code_attempt)
    assert "LUMENA_DONE_GATE_MAX_RETRIES" in src
    assert "done gate failed - budget exhausted" in src
    assert "Validation avant fin echouee" in src
    assert "success=False" in src


def test_success_text_cannot_bypass_done_after_edits():
    src = inspect.getsource(sa_module.CodeAgent._single_code_attempt)
    assert "success_text refused after edits" in src
    assert '{"action":"done","summary":"..."}' in src
