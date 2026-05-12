"""Structured autonomy decision ledger.

This module records small factual events about autonomous decisions. It is not
used as memory and does not store prompts or secrets. The goal is to explain
what Lumena considered, executed, blocked, or should do next.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from ..utils.paths import DATA_DIR


LEDGER_RELATIVE_PATH = Path("autonomy") / "activity_ledger.jsonl"
MAX_LEDGER_READ_BYTES = 2_000_000

_LEDGER_LOCK = threading.Lock()


def get_ledger_path(data_dir: Path | None = None) -> Path:
    return (data_dir or DATA_DIR) / LEDGER_RELATIVE_PATH


def append_autonomy_event(
    event_type: str,
    *,
    data_dir: Path | None = None,
    action_type: str = "",
    description: str = "",
    reason: str = "",
    decision: str = "",
    priority: str = "",
    safe_to_execute: bool | None = None,
    requires_user_confirmation: bool | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Append one autonomy event to JSONL and return the stored payload."""
    payload: dict[str, Any] = {
        "timestamp": datetime.now().isoformat(),
        "event_type": str(event_type)[:80],
    }
    optional = {
        "action_type": action_type,
        "description": description,
        "reason": reason,
        "decision": decision,
        "priority": priority,
    }
    for key, value in optional.items():
        value = str(value or "").replace("\n", " ").strip()
        if value:
            payload[key] = value[:500]
    if safe_to_execute is not None:
        payload["safe_to_execute"] = bool(safe_to_execute)
    if requires_user_confirmation is not None:
        payload["requires_user_confirmation"] = bool(requires_user_confirmation)
    if isinstance(metadata, dict) and metadata:
        clean_meta: dict[str, Any] = {}
        for key, value in metadata.items():
            if key is None:
                continue
            if isinstance(value, (str, int, float, bool)) or value is None:
                clean_meta[str(key)[:80]] = value
        if clean_meta:
            payload["metadata"] = clean_meta

    path = get_ledger_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    with _LEDGER_LOCK:
        with path.open("a", encoding="utf-8", newline="\n") as f:
            f.write(line + "\n")
    return payload


def read_autonomy_events(
    *,
    data_dir: Path | None = None,
    date: str = "",
    limit: int = 200,
    max_bytes: int = MAX_LEDGER_READ_BYTES,
) -> list[dict[str, Any]]:
    """Read recent ledger entries, optionally filtered by YYYY-MM-DD."""
    path = get_ledger_path(data_dir)
    if not path.exists():
        return []
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            if size > max_bytes:
                f.seek(max(0, size - max_bytes))
                f.readline()
            raw = f.read().decode("utf-8", errors="replace")
    except Exception:
        return []

    events: list[dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        if date and not str(item.get("timestamp", "")).startswith(date):
            continue
        events.append(item)

    try:
        limit = max(1, min(1000, int(limit)))
    except (TypeError, ValueError):
        limit = 200
    return events[-limit:]

