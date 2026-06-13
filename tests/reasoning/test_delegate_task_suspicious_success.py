from types import SimpleNamespace

from src.reasoning.handlers.agents import _suspicious_delegate_success_reason


def test_suspicious_delegate_success_rejects_no_result_report():
    result = SimpleNamespace(
        output="🔍 Aucun résultat pour 'Crée un site web complet'",
        meta={"iterations": "?"},
        duration_ms=900,
        artifacts=[],
    )

    reason = _suspicious_delegate_success_reason(result, "code")

    assert reason
    assert "sans action productive" in reason
