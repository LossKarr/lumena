"""One-use, expiring deletion tickets stored only as SHA-256 digests."""

from __future__ import annotations

import hashlib
from pathlib import Path
import secrets
import time
from threading import RLock
from typing import Any

from filelock import FileLock

from src.utils.persistence import atomic_write_json, safe_read_json
from src.utils.paths import DATA_DIR

from .contracts import ModelReference, utc_now


class DeleteTicketError(ValueError):
    pass


class DeleteTicketStore:
    def __init__(self, path: Path | None = None, *, ttl_seconds: int = 300) -> None:
        self.path = path or DATA_DIR / "local_models" / "delete_tickets.json"
        self.ttl_seconds = max(30, min(ttl_seconds, 1800))
        self._lock = RLock()
        self._file_lock = FileLock(str(self.path) + ".lock", timeout=5)

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def _load(self) -> dict[str, Any]:
        data = safe_read_json(self.path, default={})
        return (
            data
            if type(data) is dict and data.get("version") == 1 and type(data.get("tickets")) is dict
            else {"version": 1, "tickets": {}}
        )

    def issue(self, reference: ModelReference, impact: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        token = secrets.token_urlsafe(32)
        digest = self._digest(token)
        now = time.time()
        record = {
            "source": reference.source.value,
            "canonical": reference.canonical,
            "pull_reference": reference.pull_reference,
            "issued_at": utc_now(),
            "expires_at_epoch": now + self.ttl_seconds,
            "impact_digest": hashlib.sha256(repr(sorted(impact.items())).encode()).hexdigest(),
        }
        with self._lock, self._file_lock:
            data = self._load()
            data["tickets"] = {
                key: value
                for key, value in data["tickets"].items()
                if type(value) is dict and float(value.get("expires_at_epoch", 0)) > now
            }
            data["tickets"][digest] = record
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.path, data)
        return token, record

    def consume(self, token: str, reference: ModelReference) -> dict[str, Any]:
        if type(token) is not str or len(token) < 32 or len(token) > 128:
            raise DeleteTicketError("delete_ticket_invalid")
        digest = self._digest(token)
        with self._lock, self._file_lock:
            data = self._load()
            record = data["tickets"].pop(digest, None)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self.path, data)
        if type(record) is not dict:
            raise DeleteTicketError("delete_ticket_invalid_or_used")
        if float(record.get("expires_at_epoch", 0)) <= time.time():
            raise DeleteTicketError("delete_ticket_expired")
        if record.get("source") != reference.source.value or record.get("canonical") != reference.canonical:
            raise DeleteTicketError("delete_ticket_target_mismatch")
        return record
