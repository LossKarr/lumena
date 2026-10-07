"""Bounded OAuth 2.1 + PKCE flow for remote MCP servers.

The manager never persists authorization codes, PKCE verifiers or access
tokens outside the encrypted MCP credentials service.  A browser redirect is
still a human consent boundary; autonomous code may prepare and resume a flow
but may not approve the provider's consent screen for the user.
"""
from __future__ import annotations

import base64
import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Mapping, Optional, Tuple
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen


MAX_METADATA_BYTES = 256 * 1024
MAX_PENDING_FLOWS = 128
DEFAULT_FLOW_TTL_S = 600.0


class MCPOAuthError(RuntimeError):
    """Short-code OAuth error; never embeds token material."""


HTTPCallable = Callable[[str, str, Mapping[str, str], Optional[bytes], float], Tuple[int, Mapping[str, str], bytes]]


def _https_url(value: Any, *, loopback_http: bool = False) -> str:
    if not isinstance(value, str) or len(value) > 2048:
        raise MCPOAuthError("oauth_url_invalid")
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower()
    loopback = host in {"localhost", "127.0.0.1", "::1"}
    if parsed.username or parsed.password or parsed.fragment or not host:
        raise MCPOAuthError("oauth_url_invalid")
    if parsed.scheme != "https" and not (
        loopback_http and loopback and parsed.scheme == "http"
    ):
        raise MCPOAuthError("oauth_url_invalid")
    return value


def _json_object(body: bytes) -> Dict[str, Any]:
    if len(body) > MAX_METADATA_BYTES:
        raise MCPOAuthError("oauth_response_too_large")
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MCPOAuthError("oauth_response_invalid") from exc
    if not isinstance(value, dict):
        raise MCPOAuthError("oauth_response_invalid")
    return value


@dataclass(frozen=True)
class OAuthServerMetadata:
    authorization_endpoint: str
    token_endpoint: str
    scopes_supported: Tuple[str, ...] = ()
    registration_endpoint: Optional[str] = None


@dataclass(frozen=True)
class OAuthStartResult:
    authorization_url: str
    state: str
    expires_at: float


@dataclass(frozen=True)
class _PendingFlow:
    server_id: str
    verifier: str
    redirect_uri: str
    client_id: str
    token_endpoint: str
    secret_key: str
    refresh_secret_key: str
    expires_at: float


class MCPOAuthManager:
    def __init__(
        self,
        *,
        credentials_service: Any,
        http: Optional[HTTPCallable] = None,
        now: Callable[[], float] = time.time,
        flow_ttl_s: float = DEFAULT_FLOW_TTL_S,
    ) -> None:
        if not all(
            callable(getattr(credentials_service, method, None))
            for method in ("set", "get")
        ):
            raise MCPOAuthError("credentials_service_invalid")
        self._credentials = credentials_service
        self._http = http or self._default_http
        self._now = now
        self._ttl = max(60.0, min(float(flow_ttl_s), 1800.0))
        self._pending: Dict[str, _PendingFlow] = {}
        self._lock = threading.RLock()

    def fetch_metadata(self, metadata_url: str) -> OAuthServerMetadata:
        url = _https_url(metadata_url)
        status, _, body = self._http(
            "GET", url, {"Accept": "application/json"}, None, 10.0
        )
        if status != 200:
            raise MCPOAuthError("oauth_metadata_unavailable")
        raw = _json_object(body)
        authorization_endpoint = _https_url(raw.get("authorization_endpoint"))
        token_endpoint = _https_url(raw.get("token_endpoint"))
        registration = raw.get("registration_endpoint")
        if registration is not None:
            registration = _https_url(registration)
        scopes = raw.get("scopes_supported", [])
        if not isinstance(scopes, list) or len(scopes) > 128 or any(
            not isinstance(scope, str) or not scope or len(scope) > 256
            for scope in scopes
        ):
            raise MCPOAuthError("oauth_metadata_invalid")
        return OAuthServerMetadata(
            authorization_endpoint=authorization_endpoint,
            token_endpoint=token_endpoint,
            scopes_supported=tuple(scopes),
            registration_endpoint=registration,
        )

    def begin(
        self,
        *,
        server_id: str,
        metadata_url: str,
        client_id: str,
        redirect_uri: str,
        scopes: Tuple[str, ...] = (),
        access_secret_key: str = "OAUTH_ACCESS_TOKEN",
        refresh_secret_key: str = "OAUTH_REFRESH_TOKEN",
    ) -> OAuthStartResult:
        if not isinstance(server_id, str) or not server_id:
            raise MCPOAuthError("server_id_invalid")
        if not isinstance(client_id, str) or not client_id or len(client_id) > 512:
            raise MCPOAuthError("oauth_client_id_invalid")
        redirect = _https_url(redirect_uri, loopback_http=True)
        metadata = self.fetch_metadata(metadata_url)
        requested = tuple(scopes)
        if len(requested) > 64 or any(
            not isinstance(scope, str) or not scope or len(scope) > 256
            for scope in requested
        ):
            raise MCPOAuthError("oauth_scopes_invalid")
        if metadata.scopes_supported and not set(requested).issubset(
            metadata.scopes_supported
        ):
            raise MCPOAuthError("oauth_scope_not_supported")
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode("ascii")).digest()
        ).rstrip(b"=").decode("ascii")
        state = secrets.token_urlsafe(32)
        expires_at = self._now() + self._ttl
        with self._lock:
            self._purge_locked()
            if len(self._pending) >= MAX_PENDING_FLOWS:
                raise MCPOAuthError("oauth_pending_limit")
            self._pending[state] = _PendingFlow(
                server_id=server_id,
                verifier=verifier,
                redirect_uri=redirect,
                client_id=client_id,
                token_endpoint=metadata.token_endpoint,
                secret_key=access_secret_key,
                refresh_secret_key=refresh_secret_key,
                expires_at=expires_at,
            )
        query = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        if requested:
            query["scope"] = " ".join(requested)
        return OAuthStartResult(
            authorization_url=f"{metadata.authorization_endpoint}?{urlencode(query)}",
            state=state,
            expires_at=expires_at,
        )

    def complete(self, *, state: str, code: str) -> Tuple[str, Tuple[str, ...]]:
        if not isinstance(state, str) or not isinstance(code, str) or not code:
            raise MCPOAuthError("oauth_callback_invalid")
        with self._lock:
            pending = self._pending.pop(state, None)
        if pending is None or pending.expires_at < self._now():
            raise MCPOAuthError("oauth_state_invalid_or_expired")
        form = urlencode({
            "grant_type": "authorization_code",
            "code": code,
            "client_id": pending.client_id,
            "redirect_uri": pending.redirect_uri,
            "code_verifier": pending.verifier,
        }).encode("ascii")
        status, _, body = self._http(
            "POST",
            pending.token_endpoint,
            {"Accept": "application/json", "Content-Type": "application/x-www-form-urlencoded"},
            form,
            15.0,
        )
        if status != 200:
            raise MCPOAuthError("oauth_token_exchange_failed")
        payload = _json_object(body)
        access = payload.get("access_token")
        refresh = payload.get("refresh_token")
        if not isinstance(access, str) or not access:
            raise MCPOAuthError("oauth_access_token_missing")
        self._credentials.set(pending.server_id, pending.secret_key, access)
        stored = [pending.secret_key]
        if isinstance(refresh, str) and refresh:
            self._credentials.set(
                pending.server_id, pending.refresh_secret_key, refresh
            )
            stored.append(pending.refresh_secret_key)
        return pending.server_id, tuple(stored)

    def cancel(self, state: str) -> bool:
        with self._lock:
            return self._pending.pop(state, None) is not None

    def _purge_locked(self) -> None:
        now = self._now()
        for state in [
            key for key, flow in self._pending.items() if flow.expires_at < now
        ]:
            self._pending.pop(state, None)

    @staticmethod
    def _default_http(method, url, headers, body, timeout):
        request = Request(url, data=body, headers=dict(headers), method=method)
        with urlopen(request, timeout=timeout) as response:  # noqa: S310
            return (
                int(response.status),
                dict(response.headers.items()),
                response.read(MAX_METADATA_BYTES + 1),
            )


__all__ = [
    "MCPOAuthError", "MCPOAuthManager", "OAuthServerMetadata", "OAuthStartResult",
]
