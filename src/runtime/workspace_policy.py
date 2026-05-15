"""Centralized workspace resolution policy."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional


@dataclass(frozen=True)
class WorkspaceResolution:
    workspace_policy: str
    resolved_workspace: str
    resolved_date: str
    resolution_reason: str
    used_fallback: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _normalize_existing_dir(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        p = Path(path).expanduser().resolve()
    except Exception:
        return None  # chemin non résolvable
    if p.exists() and p.is_dir():
        return str(p)
    return None


def _normalize_existing_file(path: Optional[str]) -> Optional[str]:
    if not path:
        return None
    try:
        p = Path(path).expanduser().resolve()
    except Exception:
        return None  # chemin non résolvable
    if p.exists() and p.is_file():
        return str(p)
    return None


def _infer_workspace_from_file_path(file_path: Optional[str]) -> Optional[str]:
    normalized = _normalize_existing_file(file_path)
    if not normalized:
        return None

    markers = (
        ".git",
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "Cargo.toml",
        "go.mod",
        "pom.xml",
        "composer.json",
    )
    current = Path(normalized).parent
    for _ in range(10):
        if any((current / marker).exists() for marker in markers):
            return str(current)
        if current.parent == current:
            break
        current = current.parent
    return str(Path(normalized).parent)


def _today_iso_local() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _resolve_default_day_workspace(default_workspace: str) -> str:
    base = Path(default_workspace).expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    day_workspace = base / _today_iso_local()
    day_workspace.mkdir(parents=True, exist_ok=True)
    return str(day_workspace)


def resolve_workspace_for_request(
    *,
    workspace_policy: Optional[str],
    requested_workspace: Optional[str],
    default_workspace: str,
    active_file_path: Optional[str],
    open_files: Optional[List[str]],
) -> WorkspaceResolution:
    policy = str(workspace_policy or "default").strip().lower()
    if policy not in {"default", "explicit", "strict_default"}:
        policy = "default"

    resolved_date = _today_iso_local()
    resolved_default = _resolve_default_day_workspace(default_workspace)

    requested_valid = _normalize_existing_dir(requested_workspace)

    if policy == "strict_default":
        return WorkspaceResolution(
            workspace_policy=policy,
            resolved_workspace=resolved_default,
            resolved_date=resolved_date,
            resolution_reason="strict_default_forced",
            used_fallback=bool(requested_workspace),
        )

    if policy == "explicit":
        if requested_valid:
            return WorkspaceResolution(
                workspace_policy=policy,
                resolved_workspace=requested_valid,
                resolved_date=resolved_date,
                resolution_reason="explicit_valid",
                used_fallback=False,
            )
        return WorkspaceResolution(
            workspace_policy=policy,
            resolved_workspace=resolved_default,
            resolved_date=resolved_date,
            resolution_reason="explicit_invalid_fallback_default",
            used_fallback=True,
        )

    if requested_valid:
        return WorkspaceResolution(
            workspace_policy=policy,
            resolved_workspace=requested_valid,
            resolved_date=resolved_date,
            resolution_reason="default_with_requested_workspace",
            used_fallback=False,
        )

    inferred = _infer_workspace_from_file_path(active_file_path)
    if not inferred:
        for item in (open_files or [])[:30]:
            inferred = _infer_workspace_from_file_path(item)
            if inferred:
                break

    if inferred:
        return WorkspaceResolution(
            workspace_policy=policy,
            resolved_workspace=inferred,
            resolved_date=resolved_date,
            resolution_reason="default_inferred_from_files",
            used_fallback=False,
        )

    return WorkspaceResolution(
        workspace_policy=policy,
        resolved_workspace=resolved_default,
        resolved_date=resolved_date,
        resolution_reason="default_fallback_default_workspace",
        used_fallback=True,
    )
def resolve_workspace_for_user(
    user_id: Optional[str] = None,
    *,
    workspace_policy: Optional[str] = None,
    requested_workspace: Optional[str] = None,
    data_dir: Optional[Path] = None,
    active_file_path: Optional[str] = None,
    open_files: Optional[List[str]] = None,
) -> WorkspaceResolution:
    """Résout le workspace pour un utilisateur donné.

    En mode LUMENA_MULTI_USER=1 : workspace par défaut = data/users/<safe_id>/workspaces/YYYY-MM-DD/
    En mode single-user : délègue à resolve_workspace_for_request() avec WORKSPACE_DIR.
    """
    from src.runtime.user_profile import MULTI_USER_ENABLED, _safe_user_id
    from src.utils.paths import DATA_DIR, WORKSPACE_DIR

    if MULTI_USER_ENABLED:
        base = data_dir or DATA_DIR
        safe = _safe_user_id(user_id)
        user_workspaces_base = base / "users" / safe / "workspaces"
        user_workspaces_base.mkdir(parents=True, exist_ok=True)
        default_workspace = str(user_workspaces_base)
    else:
        default_workspace = str(WORKSPACE_DIR)

    return resolve_workspace_for_request(
        workspace_policy=workspace_policy,
        requested_workspace=requested_workspace,
        default_workspace=default_workspace,
        active_file_path=active_file_path,
        open_files=open_files,
    )


# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
