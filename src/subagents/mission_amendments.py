"""Versioned, review-gated mission scope amendments."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List
import uuid


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def request_scope_amendment(
    orchestrator: Any,
    mission_id: str,
    *,
    command_id: str,
    requested_files: Iterable[str],
) -> Dict[str, Any]:
    files = list(dict.fromkeys(
        str(path).strip().replace("\\", "/")
        for path in requested_files
        if str(path).strip()
    ))
    has_invalid_path = any(
        len(path) > 500 or path.startswith("/") or ".." in path.split("/")
        for path in files
    )
    if not files or len(files) > 100 or has_invalid_path:
        raise ValueError("invalid_amendment_scope")

    def transaction(metadata: Dict[str, Any], _task: Dict[str, Any]) -> Dict[str, Any]:
        amendments: List[Dict[str, Any]] = [
            dict(item) for item in metadata.get("mission_amendments") or [] if isinstance(item, dict)
        ]
        for existing in amendments:
            if existing.get("command_id") == command_id:
                return dict(existing)
        revision = int(metadata.get("mission_contract_revision", 0)) + 1
        amendment = {
            "amendment_id": f"amend_{uuid.uuid4().hex}",
            "command_id": command_id,
            "revision": revision,
            "requested_files": files,
            "status": "pending_review",
            "created_at": _now(),
        }
        amendments.append(amendment)
        metadata["mission_amendments"] = amendments[-100:]
        metadata["mission_contract_revision"] = revision
        return dict(amendment)

    return orchestrator.mutate_task_metadata(mission_id, transaction)
