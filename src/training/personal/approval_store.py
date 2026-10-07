"""Short-lived, single-use approvals for sensitive personal-model actions."""

from __future__ import annotations

import hashlib
import secrets
import time
from pathlib import Path
from threading import RLock
from typing import Any

from filelock import FileLock

from src.utils.persistence import atomic_write_json, safe_read_json


class ApprovalStore:
    def __init__(self, root: Path, *, ttl_seconds: int = 300) -> None:
        self.path = Path(root) / "approvals" / "tickets.json"
        self.ttl_seconds = max(30, min(int(ttl_seconds), 3600))
        self._thread_lock = RLock()
        self._file_lock = FileLock(str(self.path) + ".lock", timeout=10)

    def _load(self) -> dict[str, Any]:
        value = safe_read_json(self.path, default={})
        return value if isinstance(value, dict) and value.get("schema_version") == 1 else {"schema_version": 1, "tickets": {}}

    def issue(self, *, owner_scope: str, action: str, resource: str) -> dict[str, Any]:
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        expires = int(time.time()) + self.ttl_seconds
        with self._thread_lock, self._file_lock:
            data = self._load()
            now = int(time.time())
            data["tickets"] = {key: item for key, item in data["tickets"].items() if int(item.get("expires_at", 0)) >= now}
            data["tickets"][digest] = {"owner_scope": owner_scope, "action": action, "resource": resource, "expires_at": expires}
            atomic_write_json(self.path, data)
        return {"approval_token": token, "expires_at": expires, "action": action, "resource": resource}

    def consume(self, token: str, *, owner_scope: str, action: str, resource: str) -> str:
        digest = hashlib.sha256(str(token).encode()).hexdigest()
        with self._thread_lock, self._file_lock:
            data = self._load()
            item = data["tickets"].pop(digest, None)
            atomic_write_json(self.path, data)
        if not isinstance(item, dict):
            raise PermissionError("approval_missing_or_used")
        if int(item.get("expires_at", 0)) < int(time.time()):
            raise PermissionError("approval_expired")
        if (item.get("owner_scope"), item.get("action"), item.get("resource")) != (owner_scope, action, resource):
            raise PermissionError("approval_scope_mismatch")
        return f"approval:{digest[:16]}"
