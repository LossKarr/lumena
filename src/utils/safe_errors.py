"""Helpers for bounded error diagnostics that never expose credentials."""

from __future__ import annotations

import re


_QUERY_SECRET_RE = re.compile(
    r"([?&](?:key|api_key|token|access_token|client_secret)=)[^&\s]+",
    flags=re.IGNORECASE,
)
_BEARER_RE = re.compile(
    r"(Bearer\s+)[A-Za-z0-9._\-]+",
    flags=re.IGNORECASE,
)


def safe_error_summary(exc: BaseException, *, limit: int = 240) -> str:
    """Return a bounded diagnostic without request URLs or credentials."""
    try:
        import httpx

        if isinstance(exc, httpx.HTTPStatusError):
            response = getattr(exc, "response", None)
            status_code = getattr(response, "status_code", None)
            reason = getattr(response, "reason_phrase", "") or ""
            return f"http_status:{status_code}:{reason}".rstrip(":")[:limit]
        if isinstance(exc, httpx.RequestError):
            return type(exc).__name__[:limit]
    except ImportError:
        pass

    text = str(exc) or type(exc).__name__
    text = _QUERY_SECRET_RE.sub(r"\1<redacted>", text)
    text = _BEARER_RE.sub(r"\1<redacted>", text)
    return text[:limit]
