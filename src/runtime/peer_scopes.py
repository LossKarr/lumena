"""Lot 0 Phase 10 — Scopes inter-instances Lumena.

Définit la liste canonique des scopes V1, le défaut après pairing,
et la validation par peer (whitelist globale + allowed_scopes du pair).
"""
from __future__ import annotations

# ── Scopes valides (whitelist canonique V1) ───────────────────────────────────
# Tout scope non listé ici est refusé quelle que soit la config du peer.
VALID_SCOPES: frozenset[str] = frozenset({
    "chat",             # délégation d'une question/réponse
    "knowledge.query",  # interroger la mémoire distante (lecture seule, résumé)
    "knowledge.share",  # proposer un partage de connaissance
    "task.delegate",    # soumettre une tâche bornée
    "task.status",      # consulter le statut d'une tâche déléguée
    "task.cancel",      # annuler une tâche déléguée
    "artifact.share",   # proposer le partage d'un artefact (fichier, résultat)
})

# Scopes accordés automatiquement après un jumelage réussi.
# Tout scope supplémentaire doit être activé explicitement par l'admin.
DEFAULT_SCOPES: list[str] = ["chat"]


def validate_peer_scope(peer: dict, scope: str) -> None:
    """Vérifie que *scope* est valide ET autorisé pour ce pair spécifique.

    Deux conditions nécessaires :
    1. Le scope doit figurer dans VALID_SCOPES (whitelist canonique).
    2. Le scope doit figurer dans peer["allowed_scopes"].

    Lève PermissionError avec un message lisible dans les deux cas.
    """
    if scope not in VALID_SCOPES:
        raise PermissionError(
            f"Scope {scope!r} inconnu. Scopes valides : {sorted(VALID_SCOPES)}"
        )
    allowed: list[str] = peer.get("allowed_scopes") or []
    if scope not in allowed:
        raise PermissionError(
            f"Scope {scope!r} non autorisé pour le pair "
            f"{peer.get('instance_id', '?')!r}. "
            f"Scopes actifs : {sorted(allowed)}"
        )
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
