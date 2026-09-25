"""Authorization-aware resolution of active work targets."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .work_registry import ActiveWorkRegistry, WorkSnapshot


@dataclass(frozen=True, slots=True)
class WorkTargetResolution:
    code: str
    target: Optional[WorkSnapshot]
    candidates: Tuple[str, ...] = ()

    @property
    def resolved(self) -> bool:
        return self.code == "resolved" and self.target is not None


def task_owner(task: Dict[str, Any]) -> str:
    metadata = task.get("metadata") or {}
    requester = metadata.get("requester") if isinstance(metadata.get("requester"), dict) else {}
    return str(metadata.get("owner_user_id") or requester.get("owner_user_id") or "").strip()


class WorkTargetResolver:
    """Select only a target visible to an authenticated owner/conversation."""

    def __init__(self, orchestrator: Any) -> None:
        self.orchestrator = orchestrator

    def resolve(
        self,
        *,
        owner_user_id: str,
        conversation_id: Optional[str] = None,
        preferred_task_id: Optional[str] = None,
    ) -> WorkTargetResolution:
        owner = str(owner_user_id or "").strip()
        if not owner:
            return WorkTargetResolution("identity_required", None)
        registry = ActiveWorkRegistry(
            self.orchestrator,
            conversation_id=conversation_id,
            owner_user_id=owner,
        )
        if preferred_task_id:
            task = self.orchestrator.get_task(preferred_task_id)
            if not task:
                return WorkTargetResolution("task_not_found", None)
            stored_owner = task_owner(task)
            if not stored_owner or stored_owner != owner:
                return WorkTargetResolution("owner_mismatch", None)
            snapshot = registry.snapshot(preferred_task_id)
            if snapshot is None:
                return WorkTargetResolution("task_not_found", None)
            if snapshot.state not in {"queued", "running", "waiting_io", "checkpointed"}:
                return WorkTargetResolution("task_terminal", snapshot)
            if preferred_task_id not in registry.active_ids():
                return WorkTargetResolution("conversation_mismatch", None)
            return WorkTargetResolution("resolved", snapshot)
        active_ids = tuple(registry.active_ids())
        if not active_ids:
            return WorkTargetResolution("no_active_work", None)
        if len(active_ids) > 1:
            return WorkTargetResolution("ambiguous_target", None, active_ids)
        return WorkTargetResolution("resolved", registry.snapshot(active_ids[0]))
