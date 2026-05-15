"""Phase 8.5 — Gestion des peer tokens inter-Lumena.

Flux de jumelage par code (Phase 8.4 + 8.5) :
  1. A génère un code court (6 chars, TTL 5 min) via POST /api/peer/pairing-code.
  2. B appelle POST /api/peer/accept-pairing avec host:port + code.
  3. B envoie POST /api/peer/validate-pairing-code sur A avec le code + ses infos + un token B→A.
  4. A valide le code, génère token A→B, stocke hash(A→B) + raw(B→A), retourne raw(A→B).
  5. B stocke raw(A→B) comme peer_token_outbound + hash(B→A) comme peer_token_hash.

Invariants de sécurité :
  - peer_token_hash   = SHA-256 du token REÇU pendant le jumelage (pour valider les appels entrants).
  - peer_token_outbound = token brut GÉNÉRÉ PAR L'AUTRE lors du jumelage (pour s'authentifier sortant).
  - Le token admin ne traverse jamais le réseau entre pairs.
  - Les tokens sont révocables individuellement sans affecter les autres pairs.
"""
from __future__ import annotations

import hashlib
import secrets


TOKEN_BYTES = 32   # 256 bits → 43 chars base64url
CODE_TTL    = 300  # secondes — code de jumelage valide 5 minutes


def generate_peer_token() -> str:
    """Génère un peer token cryptographiquement sûr (256 bits)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_peer_token(token: str) -> str:
    """Hache un peer token avec SHA-256 pour stockage dans le registre."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def verify_peer_token(token: str, stored_hash: str) -> bool:
    """Vérifie qu'un token correspond au hash stocké — comparaison en temps constant."""
    return secrets.compare_digest(hash_peer_token(token), stored_hash)


def generate_pairing_code() -> str:
    """Génère un code de jumelage à 6 caractères alphanumériques non-ambigus.

    Alphabet sans 0/O, 1/I pour éviter les confusions visuelles.
    Entropie : log2(32^6) ≈ 30 bits — suffisant pour un code à usage unique TTL 5 min.
    """
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(6))
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
