"""A1 — Identité de flotte : auto-jumelage machine par preuve HMAC.

`LUMENA_FLEET_KEY` = secret partagé entre les Lumena d'une même flotte.
Deux instances avec la MÊME clé s'auto-jumellent **sans code humain** :

- preuve de connaissance de la clé via **HMAC challenge-response** mutuel
  (nonces anti-rejeu) ;
- **la clé ne traverse JAMAIS le réseau** (seules des preuves HMAC circulent) ;
- les peer tokens sont **DÉRIVÉS** de la clé (jamais transmis non plus).

Si `LUMENA_FLEET_KEY` est vide → tout est désactivé, le jumelage manuel par
code reste le seul chemin (comportement inchangé). Ce module est **pur**
(aucune I/O, aucune dépendance réseau/registre) → entièrement testable.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets as _secrets
from typing import Optional


def get_fleet_key() -> str:
    """Clé de flotte courante (env), ou chaîne vide si non configurée."""
    return os.getenv("LUMENA_FLEET_KEY", "").strip()


def is_fleet_pairing_enabled() -> bool:
    """True si une clé de flotte est configurée (non vide)."""
    return bool(get_fleet_key())


def generate_nonce() -> str:
    """Nonce aléatoire anti-rejeu (128 bits hex)."""
    return _secrets.token_hex(16)


def _hmac(key: str, message: str) -> str:
    return hmac.new(key.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


# ── Preuve de membre de flotte (challenge-response) ──────────────────────────

def compute_proof(
    prover_id: str,
    other_id: str,
    nonce_init: str,
    nonce_resp: str,
    *,
    fleet_key: Optional[str] = None,
) -> str:
    """Preuve HMAC qu'un acteur (`prover_id`) connaît la clé de flotte.

    Liaison stricte (anti-rejeu + anti-réflexion) :
      HMAC(key, "fleetproof|<nonce_init>|<nonce_resp>|<prover_id>|<other_id>")

    Les deux nonces (initiateur + répondeur) lient la preuve à CET échange.
    `prover_id` distingue la preuve de A de celle de B (mêmes nonces, preuves
    différentes) → un pair ne peut pas réfléchir la preuve de l'autre.
    """
    key = fleet_key if fleet_key is not None else get_fleet_key()
    msg = f"fleetproof|{nonce_init}|{nonce_resp}|{prover_id}|{other_id}"
    return _hmac(key, msg)


def verify_proof(
    proof: str,
    prover_id: str,
    other_id: str,
    nonce_init: str,
    nonce_resp: str,
    *,
    fleet_key: Optional[str] = None,
) -> bool:
    """Vérifie en **temps constant** une preuve reçue d'un pair distant.

    `prover_id` = id du pair distant (l'émetteur de la preuve), `other_id` =
    notre id. Retourne False si la clé est absente ou la preuve vide.
    """
    key = fleet_key if fleet_key is not None else get_fleet_key()
    if not key or not proof:
        return False
    expected = compute_proof(prover_id, other_id, nonce_init, nonce_resp, fleet_key=key)
    return hmac.compare_digest(expected, proof)


# ── Dérivation des peer tokens (jamais transmis) ─────────────────────────────

def derive_peer_token(
    from_id: str,
    to_id: str,
    *,
    fleet_key: Optional[str] = None,
) -> str:
    """Token que `from_id` présente à `to_id`. Déterministe, dérivé de la clé.

    Asymétrique : A→B ≠ B→A. Calculable des deux côtés (qui ont la clé) →
    aucun token ne circule sur le réseau.
    """
    key = fleet_key if fleet_key is not None else get_fleet_key()
    return _hmac(key, f"fleettoken|{from_id}|{to_id}")
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
