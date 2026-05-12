"""Phase 5/6 — Vérification trust et scopes inter-instances Lumena."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from src.utils.paths import DATA_DIR

_PEER_REGISTRY_FILE = DATA_DIR / "peer_registry.json"

# Scopes autorisés pour la délégation (lecture seule au départ).
# Chaque scope supplémentaire devra être explicitement ajouté ici avec justification.
ALLOWED_DELEGATION_SCOPES: frozenset[str] = frozenset({"chat"})


def _load_peer(instance_id: str) -> Optional[dict]:
    try:
        if _PEER_REGISTRY_FILE.exists():
            data = json.loads(_PEER_REGISTRY_FILE.read_text(encoding="utf-8"))
            return data.get(instance_id)
    except Exception:
        pass
    return None


def get_peer_trust(instance_id: str) -> str:
    """Retourne 'trusted', 'blocked' ou 'unknown' pour un pair donné."""
    peer = _load_peer(instance_id)
    if peer is None:
        return "unknown"
    return peer.get("trust", "unknown")


def require_trusted_peer(instance_id: str) -> None:
    """Lève PermissionError si le pair n'est pas 'trusted'.

    Appelé avant toute opération de délégation pour garantir que seuls les
    pairs explicitement jumelés peuvent soumettre des tâches.
    """
    trust = get_peer_trust(instance_id)
    if trust == "blocked":
        raise PermissionError(f"Instance {instance_id!r} est bloquée.")
    if trust != "trusted":
        raise PermissionError(
            f"Instance {instance_id!r} n'est pas jumelée (trust={trust!r}). "
            "Utilisez POST /api/peers/pair pour établir la confiance."
        )


def validate_scope(scope: str) -> None:
    """Lève ValueError si le scope demandé n'est pas autorisé."""
    if scope not in ALLOWED_DELEGATION_SCOPES:
        raise ValueError(
            f"Scope {scope!r} non autorisé. Scopes valides : {sorted(ALLOWED_DELEGATION_SCOPES)}"
        )
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
