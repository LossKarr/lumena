"""Deterministic privacy filtering for personal-model training records."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


_SENSITIVE_KEYS = frozenset({
    "api_key", "apikey", "access_token", "refresh_token", "token", "password",
    "passwd", "secret", "client_secret", "authorization", "cookie", "cookies",
    "chain_of_thought", "reasoning", "hidden_reasoning", "private_key",
})

_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("private_key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----", re.I), "<REDACTED_PRIVATE_KEY>"),
    ("bearer", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"), "Bearer <REDACTED_TOKEN>"),
    ("provider_key", re.compile(r"\b(?:sk|rk|pk)-(?:proj-)?[A-Za-z0-9_-]{16,}\b"), "<REDACTED_API_KEY>"),
    ("github_token", re.compile(r"\bgh(?:p|o|u|s|r)_[A-Za-z0-9]{20,}\b"), "<REDACTED_GITHUB_TOKEN>"),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"), "<REDACTED_AWS_KEY>"),
    ("assigned_secret", re.compile(r"(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*['\"]?(?!<REDACTED_)[^\s,'\"]{8,}"), r"\1=<REDACTED_SECRET>"),
    ("email", re.compile(r"(?<![\w.+-])[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}(?![\w.-])", re.I), "<REDACTED_EMAIL>"),
    ("windows_user_path", re.compile(r"(?i)\b[A-Z]:\\Users\\(?!<REDACTED_USER>)[^\\\s]+"), r"C:\\Users\\<REDACTED_USER>"),
)


@dataclass(frozen=True, slots=True)
class RedactionResult:
    value: Any
    findings: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return bool(self.findings)


def redact_text(value: str) -> RedactionResult:
    text = str(value)
    findings: list[str] = []
    for name, pattern, replacement in _PATTERNS:
        text, count = pattern.subn(replacement, text)
        if count:
            findings.extend([name] * count)
    return RedactionResult(text, tuple(findings))


def redact_value(value: Any, *, _key: str = "") -> RedactionResult:
    """Recursively redact secrets/PII and remove hidden-reasoning fields."""

    key = _key.strip().lower()
    if key in _SENSITIVE_KEYS:
        if value == "<REDACTED_FIELD>":
            return RedactionResult(value, ())
        return RedactionResult("<REDACTED_FIELD>", (f"field:{key}",))
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        output: dict[str, Any] = {}
        findings: list[str] = []
        for child_key, child_value in value.items():
            result = redact_value(child_value, _key=str(child_key))
            output[str(child_key)] = result.value
            findings.extend(result.findings)
        return RedactionResult(output, tuple(findings))
    if isinstance(value, (list, tuple)):
        output_list = []
        findings = []
        for child in value:
            result = redact_value(child)
            output_list.append(result.value)
            findings.extend(result.findings)
        return RedactionResult(output_list, tuple(findings))
    return RedactionResult(value, ())


def has_unredacted_sensitive_data(value: Any) -> bool:
    result = redact_value(value)
    return result.changed
