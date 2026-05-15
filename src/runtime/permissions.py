"""
Phase 1 — Permissions minimales Lumena.

Pour les phases 0-3, deux états suffisent dans la majorité des chemins :
  - is_owner  : peut tout faire
  - is_guest  : accès restreint, outils dangereux bloqués

Rôles définis :
  owner > admin > user > guest | peer

Scopes critiques (enforcer dès maintenant) :
  - dangerous_tools
  - manage_users
  - manage_peers
  - admin_routes

La matrice complète roles × scopes est reportée à la phase 4+.
"""
from __future__ import annotations

from typing import Optional, Set

# Rôles valides (ordre croissant de privilège)
ROLES = ("guest", "peer", "user", "admin", "owner")

# Scopes réservés aux rôles >= admin
_ADMIN_SCOPES: Set[str] = {"manage_users", "manage_peers", "admin_routes"}

# Scopes réservés aux rôles >= owner uniquement
_OWNER_SCOPES: Set[str] = set()

# Scopes bloqués pour guest et peer
_BLOCKED_FOR_GUEST: Set[str] = {"dangerous_tools", "manage_users", "manage_peers", "admin_routes"}


def _role_level(role: Optional[str]) -> int:
    r = (role or "guest").strip().lower()
    try:
        return ROLES.index(r)
    except ValueError:
        return 0  # inconnu → guest


def is_owner(role: Optional[str]) -> bool:
    """Retourne True si le rôle est owner."""
    return (role or "").strip().lower() == "owner"


def is_guest(role: Optional[str]) -> bool:
    """Retourne True si le rôle est guest ou peer (accès minimal)."""
    return (role or "").strip().lower() in {"guest", "peer"}


def can_use_scope(role: Optional[str], scope: str) -> bool:
    """Retourne True si le rôle peut utiliser le scope demandé."""
    level = _role_level(role)
    peer_level = _role_level("peer")
    admin_level = _role_level("admin")
    owner_level = _role_level("owner")

    # guest ET peer sont tous les deux bloqués pour les scopes sensibles
    if scope in _BLOCKED_FOR_GUEST and level <= peer_level:
        return False
    if scope in _ADMIN_SCOPES and level < admin_level:
        return False
    if scope in _OWNER_SCOPES and level < owner_level:
        return False
    return True


def require_owner(role: Optional[str]) -> None:
    """Lève PermissionError si le rôle n'est pas owner."""
    if not is_owner(role):
        raise PermissionError(f"Rôle 'owner' requis, rôle actuel : '{role}'")


def require_scope(role: Optional[str], scope: str) -> None:
    """Lève PermissionError si le rôle ne peut pas utiliser le scope."""
    if not can_use_scope(role, scope):
        raise PermissionError(
            f"Scope '{scope}' refusé pour le rôle '{role}'"
        )


def require_not_guest(role: Optional[str]) -> None:
    """Lève PermissionError si le rôle est guest ou peer."""
    if is_guest(role):
        raise PermissionError(
            f"Accès refusé pour le rôle '{role}' (guest/peer)"
        )
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
