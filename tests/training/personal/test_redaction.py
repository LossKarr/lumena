from __future__ import annotations

from src.training.personal.redaction import has_unredacted_sensitive_data, redact_value


def test_redacts_secrets_pii_paths_and_hidden_reasoning() -> None:
    marker = "SECRET_API_LEAK_123456789"
    payload = {
        "api_key": marker,
        "message": "contact moi@example.com token=abcdef1234567890 C:\\Users\\charl\\secret.txt",
        "reasoning": "private chain",
    }
    result = redact_value(payload)
    rendered = str(result.value)
    assert marker not in rendered
    assert "moi@example.com" not in rendered
    assert "charl" not in rendered
    assert "private chain" not in rendered
    assert result.changed is True
    assert has_unredacted_sensitive_data(payload) is True
    assert has_unredacted_sensitive_data(result.value) is False
