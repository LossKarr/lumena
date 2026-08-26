"""Source-aware model routing primitives.

Lumena historically identified a candidate with only ``provider/model``.  That
is insufficient now that OpenAI can be reached through two independent access
paths: paid API credentials and the user's Codex subscription.  This module is
pure on purpose: it describes attempts and failures without importing a
provider client, Codex App Server, ReAct, or the web application.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any, Iterable

import httpx


class ModelAccessSource(str, Enum):
    API = "api"
    CODEX = "codex"
    LOCAL = "local"


class ModelFailureKind(str, Enum):
    AUTH = "auth"
    QUOTA = "quota"
    RATE_LIMIT = "rate_limit"
    TRANSIENT = "transient"
    REFUSAL = "refusal"
    INVALID_REQUEST = "invalid_request"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ModelAccessRef:
    source: ModelAccessSource
    provider: str
    model: str
    billing: str = ""
    capabilities: frozenset[str] = frozenset({"text"})

    @property
    def qualified_id(self) -> str:
        return f"{self.source.value}:{self.provider}:{self.model}"


@dataclass(frozen=True)
class ModelAttemptTrace:
    candidate: ModelAccessRef
    status: str
    reason: str = ""
    failure_kind: ModelFailureKind | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["candidate"]["source"] = self.candidate.source.value
        payload["candidate"]["capabilities"] = sorted(self.candidate.capabilities)
        payload["failure_kind"] = (
            self.failure_kind.value if self.failure_kind is not None else None
        )
        return payload


_STATUS_RE = re.compile(r"\b(400|401|402|403|408|409|422|429|5\d\d)\b")


def _status_code(error: BaseException) -> int | None:
    if isinstance(error, httpx.HTTPStatusError):
        return int(error.response.status_code)
    match = _STATUS_RE.search(str(error or ""))
    return int(match.group(1)) if match else None


def classify_model_failure(error: BaseException) -> ModelFailureKind:
    """Classify only what the router can act on deterministically."""

    if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt)):
        return ModelFailureKind.CANCELLED

    text = str(error or "").lower()
    if text.startswith("anthropic_refusal:") or "content policy" in text:
        return ModelFailureKind.REFUSAL

    status = _status_code(error)
    if status == 401 or any(
        marker in text
        for marker in ("authentication failed", "session codex non connectee")
    ):
        return ModelFailureKind.AUTH
    if status in {402, 403} or any(
        marker in text
        for marker in (
            "insufficient quota",
            "credits exhausted",
            "quota exhausted",
            "quota de l'abonnement codex epuise",
            "quota de l’abonnement codex epuise",
        )
    ):
        return ModelFailureKind.QUOTA
    if status == 429 or "rate limit" in text:
        return ModelFailureKind.RATE_LIMIT
    if status in {400, 409, 422}:
        return ModelFailureKind.INVALID_REQUEST
    if status == 408 or (status is not None and status >= 500):
        return ModelFailureKind.TRANSIENT
    if isinstance(error, (TimeoutError, httpx.TimeoutException, httpx.RequestError)):
        return ModelFailureKind.TRANSIENT
    return ModelFailureKind.UNKNOWN


def failure_allows_fallback(kind: ModelFailureKind) -> bool:
    """Never hide cancellation, malformed requests, or model policy refusals."""

    return kind in {
        ModelFailureKind.AUTH,
        ModelFailureKind.QUOTA,
        ModelFailureKind.RATE_LIMIT,
        ModelFailureKind.TRANSIENT,
        ModelFailureKind.UNKNOWN,
    }


def append_unique_attempt(
    attempts: list[ModelAccessRef], candidate: ModelAccessRef
) -> bool:
    """Append once by fully-qualified identity; return whether it was added."""

    known = {attempt.qualified_id for attempt in attempts}
    if candidate.qualified_id in known:
        return False
    attempts.append(candidate)
    return True


def serialise_attempts(attempts: Iterable[ModelAttemptTrace]) -> list[dict[str, Any]]:
    return [attempt.to_dict() for attempt in attempts]
