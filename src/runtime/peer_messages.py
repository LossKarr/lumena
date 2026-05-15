"""Lot C Phase 10 — Envelope commune inter-Lumena.

Formalise tous les échanges inter-instances dans une structure unique et validée.
Prépare knowledge query, tasks et orchestration sans casser /api/peer/delegate Phase 8.

Variables d'environnement :
  LUMENA_PEER_MAX_HOPS   — max hop_count autorisé (défaut : 5)
  LUMENA_PEER_MAX_TTL    — TTL max en secondes (défaut : 3600)
  LUMENA_PEER_MAX_PAYLOAD_BYTES — taille max du payload sérialisé JSON (défaut : 65536)

Aucun secret, token ou clé admin ne doit traverser une enveloppe :
sanitize_peer_message() s'en assure.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.runtime.peer_scopes import VALID_SCOPES

# ── Constantes ────────────────────────────────────────────────────────────────

_DEFAULT_MAX_HOPS: int = 5
_DEFAULT_MAX_TTL: int = 3600          # 1 heure
_DEFAULT_TTL: int = 300               # 5 minutes
_DEFAULT_MAX_PAYLOAD_BYTES: int = 65536  # 64 KiB

# Types de messages autorisés en V1.
VALID_MESSAGE_TYPES: frozenset[str] = frozenset({
    "chat_delegate",
    "knowledge_query",
    "knowledge_answer",
    "task_request",
    "task_progress",
    "task_result",
    "task_cancel",
    "error",
    "heartbeat",
})

# Clés de payload qui ne doivent jamais traverser l'enveloppe.
_FORBIDDEN_PAYLOAD_KEYS: frozenset[str] = frozenset({
    "token", "secret", "password", "api_key", "apikey", "private_key",
    "admin_token", "peer_token", "peer_token_outbound", "peer_token_hash",
    "authorization", "bearer", "access_token", "refresh_token",
    "auth", "credentials", "credential", "passwd",
})

# Pattern de valeur ressemblant à un secret (hex 32+ chars, jwt, Bearer…)
_SECRET_VALUE_RE = re.compile(
    r"(Bearer\s+\S+|eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}|[0-9a-fA-F]{32,})",
    re.IGNORECASE,
)


def has_secret_pattern(text: str) -> bool:
    """Retourne True si *text* contient un pattern ressemblant à un secret.

    Utilisé pour refuser un prompt avant de le transmettre à un pair.
    Détecte : Bearer tokens, JWTs, chaînes hex ≥ 32 chars.
    """
    return bool(_SECRET_VALUE_RE.search(text))


def redact_string(text: str) -> str:
    """Redacte les patterns secrets dans une chaîne de texte.

    Utilisé pour nettoyer un résultat reçu d'un pair avant injection LLM.
    Remplace chaque occurrence par '[REDACTED]'.
    """
    return _SECRET_VALUE_RE.sub("[REDACTED]", text)


def _get_max_hops() -> int:
    try:
        return max(1, int(os.getenv("LUMENA_PEER_MAX_HOPS", str(_DEFAULT_MAX_HOPS))))
    except (ValueError, TypeError):
        return _DEFAULT_MAX_HOPS


def _get_max_ttl() -> int:
    try:
        return max(10, int(os.getenv("LUMENA_PEER_MAX_TTL", str(_DEFAULT_MAX_TTL))))
    except (ValueError, TypeError):
        return _DEFAULT_MAX_TTL


def _get_max_payload_bytes() -> int:
    try:
        # Minimum absolu : 64 octets (assez pour un heartbeat vide).
        # La config UI enforce min=1024 ; les tests peuvent descendre sous ce seuil.
        return max(64, int(os.getenv(
            "LUMENA_PEER_MAX_PAYLOAD_BYTES", str(_DEFAULT_MAX_PAYLOAD_BYTES)
        )))
    except (ValueError, TypeError):
        return _DEFAULT_MAX_PAYLOAD_BYTES


# ── Dataclass envelope ────────────────────────────────────────────────────────

@dataclass
class PeerMessage:
    """Enveloppe commune pour tous les échanges inter-instances Lumena.

    Champs obligatoires : type, scope, from_instance_id, to_instance_id, payload.
    Champs auto-générés si absents : message_id, conversation_id, trace_id, created_at.
    """
    type: str
    scope: str
    from_instance_id: str
    to_instance_id: str
    payload: Dict[str, Any]

    message_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    conversation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    ttl_seconds: int = _DEFAULT_TTL
    parent_message_id: Optional[str] = None
    hop_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "message_id": self.message_id,
            "conversation_id": self.conversation_id,
            "trace_id": self.trace_id,
            "from_instance_id": self.from_instance_id,
            "to_instance_id": self.to_instance_id,
            "type": self.type,
            "scope": self.scope,
            "created_at": self.created_at,
            "ttl_seconds": self.ttl_seconds,
            "parent_message_id": self.parent_message_id,
            "hop_count": self.hop_count,
            "payload": self.payload,
        }

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "PeerMessage":
        return PeerMessage(
            type=d.get("type", ""),
            scope=d.get("scope", ""),
            from_instance_id=d.get("from_instance_id", ""),
            to_instance_id=d.get("to_instance_id", ""),
            payload=d.get("payload") or {},
            message_id=d.get("message_id") or uuid.uuid4().hex,
            conversation_id=d.get("conversation_id") or uuid.uuid4().hex,
            trace_id=d.get("trace_id") or uuid.uuid4().hex,
            created_at=d.get("created_at") or datetime.now(timezone.utc).isoformat(),
            ttl_seconds=int(d.get("ttl_seconds") or _DEFAULT_TTL),
            parent_message_id=d.get("parent_message_id"),
            hop_count=int(d.get("hop_count") or 0),
        )


# ── Helpers publics ───────────────────────────────────────────────────────────

def create_peer_message(
    *,
    type: str,
    scope: str,
    from_instance_id: str,
    to_instance_id: str,
    payload: Dict[str, Any],
    conversation_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    ttl_seconds: int = _DEFAULT_TTL,
    parent_message_id: Optional[str] = None,
) -> PeerMessage:
    """Crée une PeerMessage valide avec IDs auto-générés si absents.

    TTL est borné à [10, LUMENA_PEER_MAX_TTL].
    Lève ValueError si type, scope, from/to sont invalides.
    Ne sanitize pas le payload — utiliser create_sanitized_peer_message()
    pour le chemin réseau sortant.
    """
    max_ttl = _get_max_ttl()
    safe_ttl = max(10, min(max_ttl, ttl_seconds))

    msg = PeerMessage(
        type=type,
        scope=scope,
        from_instance_id=from_instance_id,
        to_instance_id=to_instance_id,
        payload=payload,
        conversation_id=conversation_id or uuid.uuid4().hex,
        trace_id=trace_id or uuid.uuid4().hex,
        ttl_seconds=safe_ttl,
        parent_message_id=parent_message_id,
    )
    validate_peer_message(msg)
    return msg


def create_sanitized_peer_message(
    *,
    type: str,
    scope: str,
    from_instance_id: str,
    to_instance_id: str,
    payload: Dict[str, Any],
    conversation_id: Optional[str] = None,
    trace_id: Optional[str] = None,
    ttl_seconds: int = _DEFAULT_TTL,
    parent_message_id: Optional[str] = None,
) -> PeerMessage:
    """Crée, sanitize et re-valide une PeerMessage sûre pour le réseau sortant.

    Équivalent de : create_peer_message() → sanitize_peer_message() → validate_peer_message().
    Lève ValueError si le payload contient un secret détecté ou une clé interdite.
    À utiliser obligatoirement sur le chemin réseau sortant.
    """
    msg = create_peer_message(
        type=type,
        scope=scope,
        from_instance_id=from_instance_id,
        to_instance_id=to_instance_id,
        payload=payload,
        conversation_id=conversation_id,
        trace_id=trace_id,
        ttl_seconds=ttl_seconds,
        parent_message_id=parent_message_id,
    )
    sanitized = sanitize_peer_message(msg)
    # Re-valider après sanitization (la taille peut avoir changé).
    validate_peer_message(sanitized)
    return sanitized


def validate_peer_message(msg: PeerMessage) -> None:
    """Valide une PeerMessage. Lève ValueError si invalide.

    Vérifie dans l'ordre :
    1. type dans VALID_MESSAGE_TYPES
    2. scope dans VALID_SCOPES
    3. from_instance_id non vide
    4. to_instance_id non vide
    5. message_id / conversation_id / trace_id non vides
    6. created_at parseable (ISO 8601)
    7. ttl_seconds borné [10, LUMENA_PEER_MAX_TTL]
    8. TTL non expiré
    9. hop_count <= LUMENA_PEER_MAX_HOPS
    10. payload sérialisable et taille <= LUMENA_PEER_MAX_PAYLOAD_BYTES
    """
    # 1. Type
    if msg.type not in VALID_MESSAGE_TYPES:
        raise ValueError(
            f"Type de message {msg.type!r} inconnu. "
            f"Types valides : {sorted(VALID_MESSAGE_TYPES)}"
        )

    # 2. Scope (heartbeat n'a pas besoin d'un scope applicatif — on accepte "chat" ou vide)
    if msg.type != "heartbeat":
        if msg.scope not in VALID_SCOPES:
            raise ValueError(
                f"Scope {msg.scope!r} inconnu. Scopes valides : {sorted(VALID_SCOPES)}"
            )

    # 3. from_instance_id
    if not msg.from_instance_id or not msg.from_instance_id.strip():
        raise ValueError("from_instance_id est requis.")

    # 4. to_instance_id
    if not msg.to_instance_id or not msg.to_instance_id.strip():
        raise ValueError("to_instance_id est requis.")

    # 5. IDs non vides
    if not msg.message_id or not msg.message_id.strip():
        raise ValueError("message_id est requis.")
    if not msg.conversation_id or not msg.conversation_id.strip():
        raise ValueError("conversation_id est requis.")
    if not msg.trace_id or not msg.trace_id.strip():
        raise ValueError("trace_id est requis.")

    # 6. created_at parseable
    try:
        dt = datetime.fromisoformat(msg.created_at)
        if dt.tzinfo is None:
            raise ValueError("created_at doit inclure une timezone (UTC de préférence).")
    except (ValueError, TypeError) as exc:
        raise ValueError(f"created_at invalide : {exc}") from exc

    # 7. TTL borné
    max_ttl = _get_max_ttl()
    if not (10 <= msg.ttl_seconds <= max_ttl):
        raise ValueError(
            f"ttl_seconds={msg.ttl_seconds} hors plage [10, {max_ttl}]."
        )

    # 8. TTL non expiré
    if is_expired(msg):
        raise ValueError(
            f"Message {msg.message_id!r} expiré (TTL={msg.ttl_seconds}s, "
            f"créé le {msg.created_at})."
        )

    # 9. hop_count
    max_hops = _get_max_hops()
    if msg.hop_count > max_hops:
        raise ValueError(
            f"hop_count={msg.hop_count} dépasse LUMENA_PEER_MAX_HOPS={max_hops}."
        )

    # 10. Payload sérialisable + taille
    try:
        serialized = json.dumps(msg.payload, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"payload non sérialisable en JSON : {exc}") from exc

    max_bytes = _get_max_payload_bytes()
    payload_bytes = len(serialized.encode("utf-8"))
    if payload_bytes > max_bytes:
        raise ValueError(
            f"payload trop volumineux ({payload_bytes} octets > max {max_bytes} octets)."
        )


def is_expired(msg: PeerMessage) -> bool:
    """Retourne True si le message a dépassé son TTL depuis created_at."""
    try:
        dt = datetime.fromisoformat(msg.created_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - dt).total_seconds()
        return age_seconds > msg.ttl_seconds
    except Exception:
        return True  # created_at illisible → expiré par sécurité


def increment_hop(msg: PeerMessage) -> PeerMessage:
    """Retourne une nouvelle PeerMessage avec hop_count + 1.

    Lève ValueError si le nouveau hop_count dépasse LUMENA_PEER_MAX_HOPS.
    """
    new_hop = msg.hop_count + 1
    max_hops = _get_max_hops()
    if new_hop > max_hops:
        raise ValueError(
            f"Impossible de transmettre : hop_count atteindrait {new_hop} "
            f"(max={max_hops}). Message {msg.message_id!r} rejeté."
        )
    d = msg.to_dict()
    d["hop_count"] = new_hop
    return PeerMessage.from_dict(d)


def make_error_message(
    *,
    original: PeerMessage,
    error_detail: str,
    from_instance_id: str,
) -> PeerMessage:
    """Crée un message d'erreur en réponse à *original*.

    Conserve conversation_id, trace_id et message_id de l'original en parent.
    Le scope est hérité de l'original (ou "chat" si heartbeat).
    """
    scope = original.scope if original.scope in VALID_SCOPES else "chat"
    return PeerMessage(
        type="error",
        scope=scope,
        from_instance_id=from_instance_id,
        to_instance_id=original.from_instance_id,
        payload={"error": error_detail, "original_type": original.type},
        conversation_id=original.conversation_id,
        trace_id=original.trace_id,
        ttl_seconds=_DEFAULT_TTL,
        parent_message_id=original.message_id,
    )


def sanitize_peer_message(msg: PeerMessage) -> PeerMessage:
    """Retourne une PeerMessage dont le payload est épuré de tout secret.

    Supprime les clés interdites (_FORBIDDEN_PAYLOAD_KEYS) et remplace
    les valeurs ressemblant à un secret par "[REDACTED]".
    Opère récursivement sur les dicts/listes imbriqués.
    Lève ValueError si une clé de premier niveau interdite est détectée
    (fail-closed : on préfère rejeter que laisser passer).
    """
    cleaned = _sanitize_dict(msg.payload, depth=0)
    d = msg.to_dict()
    d["payload"] = cleaned
    return PeerMessage.from_dict(d)


# ── Helpers privés ────────────────────────────────────────────────────────────

def _sanitize_dict(obj: Any, depth: int) -> Any:
    """Nettoie récursivement un objet JSON."""
    if depth > 10:
        return "[TRUNCATED]"
    if isinstance(obj, dict):
        result: Dict[str, Any] = {}
        for k, v in obj.items():
            key_lower = str(k).lower()
            if key_lower in _FORBIDDEN_PAYLOAD_KEYS:
                raise ValueError(
                    f"Clé interdite {k!r} détectée dans le payload — "
                    "aucun secret ne doit traverser une enveloppe inter-instances."
                )
            result[k] = _sanitize_dict(v, depth + 1)
        return result
    if isinstance(obj, list):
        return [_sanitize_dict(item, depth + 1) for item in obj]
    if isinstance(obj, str):
        if _SECRET_VALUE_RE.search(obj):
            return "[REDACTED]"
    return obj
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
