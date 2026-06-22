"""A2 — Canal signé inter-Lumena : intégrité + authenticité + anti-rejeu.

Chaque enveloppe inter-pairs est signée avec la **clé dérivée de flotte**
(`peer_fleet.derive_peer_token(from_id, to_id)`), déjà calculable des deux
côtés et **jamais transmise sur le réseau** :

    sig = HMAC( derive_peer_token(from_id, to_id),
                "peersig|<json_canonique>|<ts>|<nonce>" )

Garanties :
  - **Intégrité** : toute altération du payload casse la signature.
  - **Authenticité** : seul un pair de la même flotte peut produire la signature.
  - **Anti-rejeu** : fenêtre temporelle (horodatage) + nonce single-use.

Le secret ne voyage plus (contrairement au Bearer token, qui circulait en clair).

Compatibilité ascendante : la signature n'est exigée que pour les pairs de la
même flotte (`pairing_method == "fleet"`) ET si `LUMENA_PEER_SIGNING != 0`. Les
pairs jumelés par code (sans clé de flotte) gardent le Bearer seul.

Ce module est **pur** côté crypto (aucune I/O réseau/registre) ; seul le cache
de nonces garde un petit état process-local en mémoire.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import time
from typing import Any, Dict, Optional

from src.runtime.peer_fleet import derive_peer_token, get_fleet_key

# ── En-têtes HTTP de signature ────────────────────────────────────────────────
SIG_HEADER: str = "X-Lumena-Sig"
TS_HEADER: str = "X-Lumena-Ts"
NONCE_HEADER: str = "X-Lumena-Nonce"

# Fenêtre d'acceptation de l'horodatage (anti-rejeu horloge), en secondes.
_DEFAULT_WINDOW_SECONDS: int = 120


def get_signature_window_seconds() -> int:
    try:
        return max(5, int(os.getenv("LUMENA_PEER_SIG_WINDOW", str(_DEFAULT_WINDOW_SECONDS))))
    except (ValueError, TypeError):
        return _DEFAULT_WINDOW_SECONDS


def is_signing_enabled() -> bool:
    """True si la signature des messages est active (défaut : oui).

    Désactivable via `LUMENA_PEER_SIGNING=0` (transition / debug). Même
    désactivée comme *exigence*, une signature **présente** reste vérifiée par
    l'appelant — on ne tolère jamais une signature fausse.
    """
    return os.getenv("LUMENA_PEER_SIGNING", "1").strip() != "0"


# ── Sérialisation canonique (identique des 2 côtés, sinon sig ne matche pas) ──

def canonical_payload(payload: Any) -> str:
    """Sérialise un objet JSON de façon **déterministe et stable**.

    `sort_keys=True` + séparateurs compacts + `ensure_ascii=False` → l'émetteur
    et le récepteur produisent exactement la même chaîne quel que soit l'ordre
    d'insertion des clés. Indispensable pour que la signature matche.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


# ── Signature / vérification (temps constant) ────────────────────────────────

def _signing_message(canonical: str, ts: str, nonce: str) -> str:
    return f"peersig|{canonical}|{ts}|{nonce}"


def sign_envelope(
    canonical: str,
    *,
    from_id: str,
    to_id: str,
    ts: str,
    nonce: str,
    fleet_key: Optional[str] = None,
) -> str:
    """Signe une enveloppe (payload déjà canonicalisé) pour `from_id`→`to_id`.

    Clé = `derive_peer_token(from_id, to_id)` (directionnelle, jamais transmise).
    Retourne une chaîne hex vide si aucune clé de flotte n'est disponible.
    """
    key = fleet_key if fleet_key is not None else get_fleet_key()
    if not key:
        return ""
    sig_key = derive_peer_token(from_id, to_id, fleet_key=key)
    msg = _signing_message(canonical, ts, nonce)
    return hmac.new(sig_key.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_envelope_signature(
    sig: str,
    canonical: str,
    *,
    from_id: str,
    to_id: str,
    ts: str,
    nonce: str,
    fleet_key: Optional[str] = None,
) -> bool:
    """Vérifie en **temps constant** une signature reçue d'un pair distant.

    `from_id` = pair distant (émetteur), `to_id` = nous. Retourne False si la
    clé de flotte est absente, la signature vide, ou la comparaison échoue.
    Ne contrôle PAS la fenêtre temporelle ni le rejeu (voir is_timestamp_fresh /
    le cache de nonces) — fait une seule chose : valider l'intégrité crypto.
    """
    key = fleet_key if fleet_key is not None else get_fleet_key()
    if not key or not sig:
        return False
    expected = sign_envelope(
        canonical, from_id=from_id, to_id=to_id, ts=ts, nonce=nonce, fleet_key=key
    )
    if not expected:
        return False
    return hmac.compare_digest(expected, sig)


# ── Fenêtre temporelle (anti-rejeu horloge) ──────────────────────────────────

def is_timestamp_fresh(ts: str, *, now: Optional[float] = None, window: Optional[int] = None) -> bool:
    """True si l'horodatage `ts` (epoch secondes, str) est dans la fenêtre.

    Rejette les messages trop vieux OU trop dans le futur (dérive d'horloge
    bornée). Fail-closed : un ts illisible est considéré non frais.
    """
    try:
        ts_val = float(ts)
    except (ValueError, TypeError):
        return False
    cur = now if now is not None else time.time()
    win = window if window is not None else get_signature_window_seconds()
    return abs(cur - ts_val) <= win


def now_ts() -> str:
    """Horodatage courant (epoch secondes, entier sous forme de str)."""
    return str(int(time.time()))


# ── Cache de nonces anti-rejeu (single-use, TTL mémoire, process-local) ───────

class NonceCache:
    """Cache borné de nonces déjà vus, avec expiration TTL et purge paresseuse.

    Un pair = un process → un cache mémoire suffit. Borné en taille pour éviter
    toute croissance non bornée sous flux soutenu (purge des plus anciens).
    """

    def __init__(self, ttl_seconds: Optional[int] = None, max_size: int = 50_000) -> None:
        self._ttl = ttl_seconds if ttl_seconds is not None else _DEFAULT_WINDOW_SECONDS
        self._max_size = max_size
        self._seen: Dict[str, float] = {}
        self._lock = threading.Lock()

    def _purge(self, now: float) -> None:
        expired = [n for n, exp in self._seen.items() if exp <= now]
        for n in expired:
            self._seen.pop(n, None)
        # Garde-fou taille : si toujours trop gros, drop les plus proches de l'expiration.
        if len(self._seen) > self._max_size:
            for n, _ in sorted(self._seen.items(), key=lambda kv: kv[1])[: len(self._seen) - self._max_size]:
                self._seen.pop(n, None)

    def seen(self, nonce: str, *, now: Optional[float] = None) -> bool:
        """Marque `nonce` comme vu et retourne True s'il l'était **déjà** (rejeu).

        Un nonce vide est traité comme rejeu (fail-closed) : pas de signature
        valable sans nonce.
        """
        if not nonce:
            return True
        cur = now if now is not None else time.time()
        with self._lock:
            self._purge(cur)
            if nonce in self._seen:
                return True
            self._seen[nonce] = cur + self._ttl
            if len(self._seen) > self._max_size:
                self._purge(cur)
            return False

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()


# Cache partagé par défaut (singleton process). TTL aligné sur la fenêtre.
_DEFAULT_NONCE_CACHE = NonceCache()


def seen_nonce(nonce: str, *, now: Optional[float] = None) -> bool:
    """Helper module-level : marque/teste un nonce sur le cache partagé."""
    return _DEFAULT_NONCE_CACHE.seen(nonce, now=now)


def reset_nonce_cache() -> None:
    """Vide le cache partagé (utilisé par les tests pour l'isolation)."""
    _DEFAULT_NONCE_CACHE.clear()


def generate_signature_nonce() -> str:
    """Nonce single-use pour signer un message (128 bits hex)."""
    import secrets as _secrets
    return _secrets.token_hex(16)


# ── Helper sortant : (content, headers) prêts pour httpx ─────────────────────

def build_signed_request(
    payload: Optional[Dict[str, Any]],
    *,
    from_id: str,
    to_id: str,
    peer_token: str = "",
    pairing_method: str = "",
    fleet_key: Optional[str] = None,
) -> tuple[bytes, Dict[str, str]]:
    """Construit `(content, headers)` pour un appel HTTP sortant vers un pair.

    - **Signe les bytes EXACTS envoyés** (`content`) → aucune divergence de
      sérialisation possible avec le récepteur (qui lit le body brut).
    - Ajoute toujours le `Authorization: Bearer` (compat ascendante).
    - Ajoute les en-têtes de signature **uniquement** si : clé de flotte
      présente ET pair `fleet` ET signing actif. Sinon → Bearer seul (les pairs
      jumelés par code restent inchangés).

    `payload=None` (ex. requête GET sans corps) → signe sur une chaîne vide.
    """
    canonical = canonical_payload(payload) if payload is not None else ""
    content = canonical.encode("utf-8")
    headers: Dict[str, str] = {"Content-Type": "application/json"}
    if peer_token:
        headers["Authorization"] = f"Bearer {peer_token}"

    key = fleet_key if fleet_key is not None else get_fleet_key()
    if key and pairing_method == "fleet" and is_signing_enabled():
        ts = now_ts()
        nonce = generate_signature_nonce()
        sig = sign_envelope(
            canonical, from_id=from_id, to_id=to_id, ts=ts, nonce=nonce, fleet_key=key
        )
        if sig:
            headers[SIG_HEADER] = sig
            headers[TS_HEADER] = ts
            headers[NONCE_HEADER] = nonce
    return content, headers
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
