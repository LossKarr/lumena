"""
Phase 2 — Profils et chemins utilisateur.

Fournit :
  - _safe_user_id()      : sanitize user_id pour usage comme nom de dossier
  - get_user_data_dir()  : data/users/<safe_id>/
  - get_user_workspace_dir() : data/users/<safe_id>/workspaces/YYYY-MM-DD/
  - get_user_memory_dir() : data/users/<safe_id>/memory/
  - MULTI_USER_ENABLED   : lu depuis LUMENA_MULTI_USER=1

Rétrocompatibilité : si LUMENA_MULTI_USER=0 (défaut), tous les helpers
retournent les chemins legacy (DATA_DIR, WORKSPACE_DIR, MEMORY_DIR) sans
créer de sous-dossier utilisateur.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

MULTI_USER_ENABLED: bool = os.getenv("LUMENA_MULTI_USER", "0").strip() == "1"

# user_id canonique pour le propriétaire de l'instance locale
LOCAL_OWNER_ID = "local:owner"

# Caractères autorisés dans un nom de dossier
_SAFE_RE = re.compile(r"[^a-zA-Z0-9_\-]")


def _safe_user_id(user_id: Optional[str]) -> str:
    """Convertit un user_id en nom de dossier safe.

    Exemples :
      "local:owner"      → "local__owner"
      "telegram:42"      → "telegram__42"
      "discord:g1:u99"   → "discord__g1__u99"
      ""  / None         → "local__owner"
    """
    uid = (user_id or LOCAL_OWNER_ID).strip() or LOCAL_OWNER_ID
    return _SAFE_RE.sub("__", uid)


def get_user_data_dir(
    user_id: Optional[str] = None,
    *,
    data_dir: Optional[Path] = None,
    create: bool = True,
) -> Path:
    """Retourne data/users/<safe_id>/ en mode multi-user, DATA_DIR sinon."""
    from src.utils.paths import DATA_DIR
    base = data_dir or DATA_DIR

    if not MULTI_USER_ENABLED:
        return base

    safe = _safe_user_id(user_id)
    path = base / "users" / safe
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def get_user_workspace_dir(
    user_id: Optional[str] = None,
    *,
    data_dir: Optional[Path] = None,
    create: bool = True,
) -> Path:
    """Retourne le workspace journalier de l'utilisateur.

    Multi-user : data/users/<safe_id>/workspaces/YYYY-MM-DD/
    Single-user : workspace/YYYY-MM-DD/  (chemin legacy)
    """
    from src.utils.paths import DATA_DIR, WORKSPACE_DIR

    today = datetime.now().strftime("%Y-%m-%d")

    if not MULTI_USER_ENABLED:
        path = WORKSPACE_DIR / today
        if create:
            path.mkdir(parents=True, exist_ok=True)
        return path

    base = data_dir or DATA_DIR
    safe = _safe_user_id(user_id)
    path = base / "users" / safe / "workspaces" / today
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def get_user_memory_dir(
    user_id: Optional[str] = None,
    *,
    data_dir: Optional[Path] = None,
    create: bool = True,
) -> Path:
    """Retourne le répertoire mémoire de l'utilisateur.

    Multi-user : data/users/<safe_id>/memory/
    Single-user : data/memory/  (chemin legacy)
    """
    from src.utils.paths import DATA_DIR, MEMORY_DIR

    if not MULTI_USER_ENABLED:
        return MEMORY_DIR

    base = data_dir or DATA_DIR
    safe = _safe_user_id(user_id)
    path = base / "users" / safe / "memory"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def get_user_conversations_dir(
    user_id: Optional[str] = None,
    *,
    data_dir: Optional[Path] = None,
    create: bool = True,
) -> Path:
    """Retourne le répertoire conversations de l'utilisateur."""
    from src.utils.paths import DATA_DIR

    if not MULTI_USER_ENABLED:
        return DATA_DIR / "web_contexts"

    base = data_dir or DATA_DIR
    safe = _safe_user_id(user_id)
    path = base / "users" / safe / "conversations"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def is_local_owner(user_id: Optional[str]) -> bool:
    """Retourne True si l'utilisateur est le propriétaire local de l'instance."""
    return (user_id or "").strip() == LOCAL_OWNER_ID
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
