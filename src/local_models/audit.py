"""Sanitized append-only audit events for local model lifecycle operations."""

from __future__ import annotations

import json
from pathlib import Path
from threading import RLock
from typing import Any

from src.utils.paths import DATA_DIR

from .contracts import utc_now

_ALLOWED = {
    "operation_id",
    "model",
    "source",
    "state",
    "percent",
    "completed_bytes",
    "total_bytes",
    "duration_ms",
    "digest",
    "error_code",
    "caller_kind",
    "verified",
}


class LocalModelAudit:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or DATA_DIR / "local_models" / "audit.jsonl"
        self._lock = RLock()

    def emit(self, event: str, **fields: Any) -> None:
        record = {"event": str(event)[:96], "timestamp": utc_now()}
        for key, value in fields.items():
            if key not in _ALLOWED or value is None:
                continue
            if isinstance(value, str):
                record[key] = value[:384]
            elif isinstance(value, (int, float, bool)):
                record[key] = value
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as stream:
                stream.write(line + "\n")

    def recent(self, limit: int = 100) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        with self._lock:
            lines = self.path.read_text(encoding="utf-8", errors="replace").splitlines()[-max(1, min(limit, 500)) :]
        result = []
        for line in lines:
            try:
                item = json.loads(line)
                if type(item) is dict:
                    result.append(item)
            except json.JSONDecodeError:
                continue
        return result
