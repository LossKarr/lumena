"""Sécurité runtime du canal voice, sans import de la stack audio."""
from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict, Optional

def _arguments_digest(arguments: Optional[Dict[str, Any]]) -> str:
    try:
        payload = json.dumps(arguments or {}, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        payload = repr(arguments or {})
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class _Authorization:
    conversation_id: str
    tool_name: str
    arguments_digest: str
    expires_at: float


@dataclass(frozen=True)
class _ConfirmationRequest:
    request_id: str
    conversation_id: str
    tool_name: str
    arguments_digest: str
    expires_at: float


class VoiceConfirmationBroker:
    """Autorisation écran one-shot, exacte et non créable depuis une utterance."""

    def __init__(self, *, time_fn=time.monotonic) -> None:
        self._time = time_fn
        self._lock = threading.Lock()
        self._pending: Dict[str, _Authorization] = {}
        self._requests: Dict[str, _ConfirmationRequest] = {}

    @staticmethod
    def _key(conversation_id: str, tool_name: str) -> str:
        return f"{conversation_id}::{tool_name}"

    def authorize(
        self,
        *,
        conversation_id: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]],
        ttl_s: float = 45.0,
    ) -> None:
        ttl = max(1.0, min(float(ttl_s), 300.0))
        auth = _Authorization(
            conversation_id=conversation_id,
            tool_name=tool_name,
            arguments_digest=_arguments_digest(arguments),
            expires_at=self._time() + ttl,
        )
        with self._lock:
            self._pending[self._key(conversation_id, tool_name)] = auth

    def request_confirmation(
        self,
        *,
        conversation_id: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]],
        ttl_s: float = 120.0,
    ) -> str:
        now = self._time()
        digest = _arguments_digest(arguments)
        ttl = max(5.0, min(float(ttl_s), 300.0))
        with self._lock:
            for request in self._requests.values():
                if (
                    request.expires_at >= now
                    and request.conversation_id == conversation_id
                    and request.tool_name == tool_name
                    and request.arguments_digest == digest
                ):
                    return request.request_id
            request_id = f"voice_confirm_{uuid.uuid4().hex}"
            self._requests[request_id] = _ConfirmationRequest(
                request_id=request_id,
                conversation_id=conversation_id,
                tool_name=tool_name,
                arguments_digest=digest,
                expires_at=now + ttl,
            )
            return request_id

    def approve(self, request_id: str, *, ttl_s: float = 45.0) -> bool:
        with self._lock:
            request = self._requests.pop(request_id, None)
        if request is None or self._time() > request.expires_at:
            return False
        ttl = max(1.0, min(float(ttl_s), 300.0))
        auth = _Authorization(
            conversation_id=request.conversation_id,
            tool_name=request.tool_name,
            arguments_digest=request.arguments_digest,
            expires_at=self._time() + ttl,
        )
        with self._lock:
            self._pending[self._key(request.conversation_id, request.tool_name)] = auth
        return True

    def list_requests(self) -> list[Dict[str, Any]]:
        now = self._time()
        with self._lock:
            expired = [rid for rid, req in self._requests.items() if req.expires_at < now]
            for rid in expired:
                self._requests.pop(rid, None)
            requests = list(self._requests.values())
        return [
            {
                "request_id": req.request_id,
                "conversation_id": req.conversation_id,
                "tool_name": req.tool_name,
                "expires_in_s": max(0.0, req.expires_at - now),
            }
            for req in requests
        ]

    def consume(
        self,
        *,
        conversation_id: str,
        tool_name: str,
        arguments: Optional[Dict[str, Any]],
    ) -> bool:
        key = self._key(conversation_id, tool_name)
        with self._lock:
            auth = self._pending.pop(key, None)
        if auth is None or self._time() > auth.expires_at:
            return False
        return auth.arguments_digest == _arguments_digest(arguments)

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()
            self._requests.clear()


_BROKER = VoiceConfirmationBroker()


def get_voice_confirmation_broker() -> VoiceConfirmationBroker:
    return _BROKER
