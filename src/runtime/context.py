"""Runtime context propagated across channel, API, tools and telemetry."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
import uuid

WorkspacePolicyValue = Literal["default", "explicit", "strict_default"]
UserRoleValue = Literal["owner", "admin", "user", "guest", "peer"]

FALLBACK_USER_ID = "local:owner"
FALLBACK_OWNER_USER_ID = "local:owner"
# Rôle par défaut pour le web local (propriétaire de l'instance)
_DEFAULT_LOCAL_ROLE: UserRoleValue = "owner"
# Rôle de repli pour tout rôle inconnu/invalide (le moins de droits possible)
_FALLBACK_UNKNOWN_ROLE: UserRoleValue = "guest"
_VALID_ROLES = {"owner", "admin", "user", "guest", "peer"}


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _normalize_policy(value: Optional[str]) -> WorkspacePolicyValue:
    normalized = str(value or "default").strip().lower()
    if normalized not in {"default", "explicit", "strict_default"}:
        return "default"
    return normalized  # type: ignore[return-value]


def _normalize_role(value: Optional[str], *, default: UserRoleValue = _DEFAULT_LOCAL_ROLE) -> UserRoleValue:
    """Normalise un rôle.

    - None ou vide → default (owner pour le web local, guest pour les canaux externes)
    - Rôle inconnu → guest (jamais owner, pour ne pas escalader les droits par erreur)
    """
    if not value or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized not in _VALID_ROLES:
        return _FALLBACK_UNKNOWN_ROLE
    return normalized  # type: ignore[return-value]


def _safe_str(value: Optional[str], fallback: str) -> str:
    return (value or "").strip() or fallback


@dataclass(frozen=True)
class RuntimeContext:
    channel: str
    client: str
    request_id: str
    conversation_id: str
    message_id: str
    # ── Identité utilisateur (Phase 0) ────────────────────────────────────
    user_id: str = FALLBACK_USER_ID
    owner_user_id: str = FALLBACK_OWNER_USER_ID
    user_role: UserRoleValue = "owner"
    profile_id: Optional[str] = None
    instance_id: str = "default"
    # ── Workspace ─────────────────────────────────────────────────────────
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
        # Nouveaux champs Phase 0 — tous optionnels pour rétrocompatibilité
        user_id: Optional[str] = None,
        owner_user_id: Optional[str] = None,
        user_role: Optional[str] = None,
        profile_id: Optional[str] = None,
        instance_id: Optional[str] = None,
    ) -> "RuntimeContext":
        from src.utils.paths import INSTANCE_ID as _INSTANCE_ID
        resolved_channel = (channel or "web").strip().lower()
        # Pour le web local sans user_id explicite → owner ; pour les canaux
        # externes (telegram, discord, whatsapp, api) sans rôle → guest.
        _is_local_web = resolved_channel in {"web", "ide"}
        _role_default: UserRoleValue = "owner" if _is_local_web else "guest"
        return cls(
            channel=resolved_channel,
            client=(client or "unknown").strip() or "unknown",
            request_id=(request_id or _new_id("req")).strip(),
            conversation_id=(conversation_id or _new_id("conv")).strip(),
            message_id=(message_id or _new_id("msg")).strip(),
            user_id=_safe_str(user_id, FALLBACK_USER_ID),
            owner_user_id=_safe_str(owner_user_id, FALLBACK_OWNER_USER_ID),
            user_role=_normalize_role(user_role, default=_role_default),
            profile_id=(profile_id or "").strip() or None,
            instance_id=_safe_str(instance_id, _INSTANCE_ID),
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
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
