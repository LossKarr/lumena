"""Runtime context propagated across channel, API, tools and telemetry."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
import uuid

WorkspacePolicyValue = Literal["default", "explicit", "strict_default"]


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _normalize_policy(value: Optional[str]) -> WorkspacePolicyValue:
    normalized = str(value or "default").strip().lower()
    if normalized not in {"default", "explicit", "strict_default"}:
        return "default"
    return normalized  # type: ignore[return-value]


@dataclass(frozen=True)
class RuntimeContext:
    channel: str
    client: str
    request_id: str
    conversation_id: str
    message_id: str
    workspace_policy: WorkspacePolicyValue = "default"
    task_id: Optional[str] = None
    client_caps: Dict[str, Any] = field(default_factory=dict)
    workspace_path: Optional[str] = None
    active_file_path: Optional[str] = None
    open_files: List[str] = field(default_factory=list)
    resolved_workspace: Optional[str] = None
    resolved_date: Optional[str] = None
    resolution_reason: Optional[str] = None
    ts: str = field(default_factory=lambda: datetime.now().isoformat())

    @classmethod
    def build(
        cls,
        *,
        channel: str,
        client: Optional[str],
        request_id: Optional[str],
        conversation_id: Optional[str],
        message_id: Optional[str],
        workspace_policy: Optional[str],
        task_id: Optional[str],
        client_caps: Optional[Dict[str, Any]],
        workspace_path: Optional[str],
        active_file_path: Optional[str],
        open_files: Optional[List[str]],
        resolved_workspace: Optional[str],
        resolved_date: Optional[str],
        resolution_reason: Optional[str],
    ) -> "RuntimeContext":
        return cls(
            channel=(channel or "web").strip().lower(),
            client=(client or "unknown").strip() or "unknown",
            request_id=(request_id or _new_id("req")).strip(),
            conversation_id=(conversation_id or _new_id("conv")).strip(),
            message_id=(message_id or _new_id("msg")).strip(),
            workspace_policy=_normalize_policy(workspace_policy),
            task_id=(task_id or "").strip() or None,
            client_caps=dict(client_caps or {}),
            workspace_path=(workspace_path or "").strip() or None,
            active_file_path=(active_file_path or "").strip() or None,
            open_files=[str(p).strip() for p in (open_files or []) if str(p).strip()][:30],
            resolved_workspace=(resolved_workspace or "").strip() or None,
            resolved_date=(resolved_date or "").strip() or None,
            resolution_reason=(resolution_reason or "").strip() or None,
        )

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_RUNTIME_CONTEXT_VAR: ContextVar[Optional[RuntimeContext]] = ContextVar(
    "lumena_runtime_context",
    default=None,
)


def push_runtime_context(runtime_context: RuntimeContext) -> Token:
    return _RUNTIME_CONTEXT_VAR.set(runtime_context)


def pop_runtime_context(token: Token) -> None:
    _RUNTIME_CONTEXT_VAR.reset(token)


def get_current_runtime_context() -> Optional[RuntimeContext]:
    return _RUNTIME_CONTEXT_VAR.get()


def get_current_runtime_context_dict() -> Dict[str, Any]:
    current = get_current_runtime_context()
    return current.to_dict() if current else {}
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
