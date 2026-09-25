"""Local IDE authentication, independent from secret storage and WebSockets."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import hmac
import ipaddress
import re
import secrets
import time
from typing import Callable


class PairingError(Exception):
    """A fixed, non-sensitive authentication failure reason."""


def _hex(value: object, length: int) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{" + str(length) + r"}", value))


@dataclass(frozen=True)
class PairingCredentials:
    key_id: str
    secret: bytes = field(repr=False)

    def __post_init__(self) -> None:
        if not _hex(self.key_id, 32) or not isinstance(self.secret, bytes) or len(self.secret) != 32:
            raise ValueError("Pairing requires a 128-bit key id and a 256-bit secret.")


@dataclass(frozen=True)
class PairingSession:
    session_id: str
    key_id: str


@dataclass(frozen=True)
class _Challenge:
    session_id: str
    key_id: str
    nonce: str
    expires_at: float


def _proof(role: str, key: PairingCredentials, challenge: _Challenge, client_nonce: str) -> str:
    # Domain and role separation prevent client proofs being reflected as server proofs.
    transcript = "\n".join((
        "lumena-ide/auth/1", role, challenge.key_id,
        challenge.session_id, challenge.nonce, client_nonce,
    )).encode("ascii")
    return hmac.new(key.secret, transcript, hashlib.sha256).hexdigest()


class PairingAuthority:
    """Single-event-loop verifier; the storage owner supplies the current key."""

    def __init__(
        self,
        credentials: Callable[[], PairingCredentials | None],
        *,
        monotonic: Callable[[], float] = time.monotonic,
        challenge_ttl: float = 15.0,
        max_pending: int = 64,
    ) -> None:
        if not 0 < challenge_ttl <= 60 or not 0 < max_pending <= 1024:
            raise ValueError("Invalid pairing challenge limits.")
        self._credentials = credentials
        self._monotonic = monotonic
        self._ttl = challenge_ttl
        self._limit = max_pending
        self._pending: dict[str, _Challenge] = {}

    def _key(self) -> PairingCredentials | None:
        try:
            return self._credentials()
        except Exception:
            raise PairingError("pairing_unavailable") from None

    def begin(self, peer: str) -> dict:
        try:
            address = ipaddress.ip_address(peer)
        except ValueError:
            raise PairingError("loopback_required") from None
        if not address.is_loopback:
            raise PairingError("loopback_required")
        key = self._key()
        if key is None:
            raise PairingError("pairing_unavailable")
        now = self._monotonic()
        self._pending = {
            session_id: challenge for session_id, challenge in self._pending.items()
            if challenge.expires_at > now and challenge.key_id == key.key_id
        }
        if len(self._pending) >= self._limit:
            raise PairingError("pairing_busy")
        challenge = _Challenge(secrets.token_hex(16), key.key_id, secrets.token_hex(32), now + self._ttl)
        self._pending[challenge.session_id] = challenge
        return {
            "type": "auth_challenge", "auth_version": 1, "key_id": key.key_id,
            "session_id": challenge.session_id, "nonce": challenge.nonce,
        }

    def finish(self, session_id: str, response: object) -> tuple[PairingSession, dict]:
        # The connection supplies its own challenge id; the peer cannot select a different one.
        challenge = self._pending.pop(session_id, None)
        if challenge is None or challenge.expires_at <= self._monotonic():
            raise PairingError("challenge_invalid")
        key = self._key()
        if key is None or key.key_id != challenge.key_id:
            raise PairingError("pairing_revoked")
        if (
            not isinstance(response, dict)
            or set(response) != {"type", "session_id", "client_nonce", "proof"}
            or response["type"] != "auth_response"
            or response["session_id"] != challenge.session_id
            or not _hex(response["client_nonce"], 64)
            or not _hex(response["proof"], 64)
        ):
            raise PairingError("response_invalid")
        expected = _proof("client", key, challenge, response["client_nonce"])
        if not hmac.compare_digest(expected, response["proof"]):
            raise PairingError("proof_invalid")
        session = PairingSession(challenge.session_id, key.key_id)
        return session, {
            "type": "auth_ok", "session_id": session.session_id, "key_id": session.key_id,
            "proof": _proof("server", key, challenge, response["client_nonce"]),
        }

    def discard(self, session_id: str) -> None:
        """Release a disconnected socket's unused challenge without authenticating."""
        self._pending.pop(session_id, None)

    def is_current(self, session: PairingSession) -> bool:
        key = self._key()
        return key is not None and key.key_id == session.key_id
