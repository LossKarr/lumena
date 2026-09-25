"""Closed audit projection for execution facts; no free text or payloads."""

from __future__ import annotations

import re

_EFFECTS = frozenset(
    {
        "READ_ONLY",
        "UI_STATE_ONLY",
        "WORKSPACE_CONTEXT",
        "BUFFER_MUTATION",
        "FILE_WRITE",
        "FILESYSTEM_DESTRUCTIVE",
        "PROCESS_LAUNCH",
        "PROCESS_CONTROL",
        "PROCESS_COMPLETION",
        "TEST_EXECUTION",
        "DEBUG_CONTROL",
        "GIT_LOCAL_MUTATION",
        "DEPLOY_MUTATION",
        "SETTINGS_MUTATION",
        "DESTRUCTIVE_SYSTEM",
        "PARAMETER_DEPENDENT",
        "UNKNOWN",
    }
)
_STATUS = frozenset({"queued", "running", "succeeded", "failed", "cancelled", "timed_out", "conflict"})
_IDS = {"attempt_id": 32, "operation_id": 32, "instance_id": 32, "catalog_revision": 64, "workspace_id": 64}
_REASONS = frozenset({"preparing", "refused", "dispatch_failed", "completed", "pending"})


def execution_metadata(value: object) -> dict | None:
    if type(value) is not dict or not set(value) <= set(_IDS) | {"effect", "status", "verified", "reason"}:
        return None
    if (
        type(value.get("effect")) is not str
        or value["effect"] not in _EFFECTS
        or type(value.get("status")) is not str
        or value["status"] not in _STATUS
        or type(value.get("verified")) is not bool
        or type(value.get("reason")) is not str
        or value["reason"] not in _REASONS
    ):
        return None
    clean = {key: value[key] for key in ("effect", "status", "verified", "reason")}
    for key, length in _IDS.items():
        item = value.get(key)
        if item is not None:
            if type(item) is not str or re.fullmatch(r"[0-9a-f]{%d}" % length, item) is None:
                return None
            clean[key] = item
    return clean
