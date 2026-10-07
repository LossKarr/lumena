"""Privacy-bounded audit trail for personal-model operations."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import RLock
from typing import Any

from filelock import FileLock

from .contracts import canonical_json, new_id, utc_now


class PersonalModelAudit:
    def __init__(self, root: Path) -> None:
        self.path = Path(root) / "audit" / "events.jsonl"
        self._thread_lock = RLock()
        self._file_lock = FileLock(str(self.path) + ".lock", timeout=10)

    def emit(self, event: str, *, actor: str, owner_scope: str, result: str, proof_id: str = "", details: dict[str, Any] | None = None) -> dict[str, Any]:
        allowed_details = {}
        for key, value in (details or {}).items():
            if key in {"run_id", "lineage_id", "version", "reason_code", "action", "state", "manifest_id"}:
                allowed_details[key] = str(value)[:128]
        record = {
            "schema_version": 1,
            "audit_id": new_id("audit"),
            "event": str(event)[:96],
            "actor": str(actor)[:96],
            "owner_scope": str(owner_scope)[:128],
            "result": str(result)[:64],
            "proof_id": str(proof_id)[:128],
            "details": allowed_details,
            "created_at": utc_now(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock, self._file_lock, open(self.path, "ab", buffering=0) as handle:
            handle.write((canonical_json(record) + "\n").encode("utf-8"))
            os.fsync(handle.fileno())
        return record

    def list(self, *, owner_scope: str, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        values: list[dict[str, Any]] = []
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(line)
                if item.get("owner_scope") == owner_scope:
                    values.append(item)
            except Exception:
                continue
        return values[-max(1, min(int(limit), 500)):][::-1]
