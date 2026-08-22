"""C3-live — Garde-fou d'exécution autonome des délégations P2P.

Passe Lumena de « propose » (shadow) à « agit » (live) SANS runaway. Tout le filet
de sécurité d'envoi est déjà dans `submit_peer_task_handler` (trust, scope,
quarantaine, anti-SSRF, secret-scan, enveloppe sanitisée). Ce module ajoute les
3 freins qui manquaient pour exécuter en boucle 24/7 :

1. **halt** — un kill-switch coupe toute NOUVELLE délégation (jamais celles en cours) ;
2. **présence** — par défaut, n'agit QUE si l'utilisateur est absent
   (`LUMENA_PEER_AUTONOMY_WHEN_PRESENT=1` pour agir 24/7 même présent) ;
3. **dedup + budget** — jamais 2× le même objectif (en cours ou récent), et au plus
   `LUMENA_PEER_AUTONOMY_MAX_PER_HOUR` délégations/heure.

Principe Lumena 24/7 : on gate le FUTUR, jamais le PRÉSENT. État du budget en mémoire
process (fenêtre glissante) — un reboot ne fait que ré-autoriser, jamais sur-déléguer.
"""
from __future__ import annotations

import hashlib
import os
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, Iterable

_LOCK = threading.Lock()
_RECENT: Deque[datetime] = deque()       # horodatage des délégations exécutées (budget)
_DELEGATED: Dict[str, datetime] = {}     # signature objectif -> dernière délégation (dedup)
_DEDUP_COOLDOWN = timedelta(hours=6)
_TRUTHY = ("1", "true", "yes", "on")


def max_per_hour() -> int:
    """Plafond de délégations autonomes par heure (env, défaut 3, borné 1..60)."""
    try:
        return max(1, min(60, int(os.getenv("LUMENA_PEER_AUTONOMY_MAX_PER_HOUR", "3"))))
    except (ValueError, TypeError):
        return 3


def _act_when_present() -> bool:
    """Défaut False : n'agit QUE si l'utilisateur est absent (premier live prudent)."""
    return os.getenv("LUMENA_PEER_AUTONOMY_WHEN_PRESENT", "0").strip().lower() in _TRUTHY


def _sig(objective: str) -> str:
    return hashlib.sha256((objective or "").strip().lower().encode("utf-8")).hexdigest()[:16]


def _prune(now: datetime) -> None:
    cutoff = now - timedelta(hours=1)
    while _RECENT and _RECENT[0] < cutoff:
        _RECENT.popleft()
    for k in [k for k, v in _DELEGATED.items() if now - v > _DEDUP_COOLDOWN]:
        _DELEGATED.pop(k, None)


def block_reason(objective: str, *, in_flight_objectives: Iterable[str] = ()) -> str:
    """'' si on peut déléguer cet objectif MAINTENANT, sinon la raison du blocage.

    Ordre : halt → présence → dedup (en cours / récent) → budget horaire.
    """
    if not objective or not objective.strip():
        return "empty"
    # 1. Kill-switch : stoppe tout NOUVEAU (les missions en cours ne sont pas touchées).
    try:
        from src.runtime.peer_network_autonomy import is_peer_halt_enabled
        if is_peer_halt_enabled():
            return "halt"
    except Exception:
        pass
    # 2. Présence : par défaut on n'agit que si l'utilisateur est absent.
    if not _act_when_present():
        try:
            from src.autonomy.presence import is_user_present
            if is_user_present():
                return "user_present"
        except Exception:
            pass
    sig = _sig(objective)
    now = datetime.now(timezone.utc)
    with _LOCK:
        _prune(now)
        # 3. Dedup : objectif déjà en cours côté missions sortantes.
        if any(_sig(o) == sig for o in in_flight_objectives):
            return "in_flight"
        if sig in _DELEGATED:
            return "recently_delegated"
        # 4. Budget horaire.
        if len(_RECENT) >= max_per_hour():
            return "hourly_budget"
    return ""


def record_delegation(objective: str) -> None:
    """Comptabilise une délégation autonome exécutée (budget + dedup)."""
    now = datetime.now(timezone.utc)
    with _LOCK:
        _RECENT.append(now)
        _DELEGATED[_sig(objective)] = now


def remaining_budget() -> int:
    """Délégations autonomes encore permises dans l'heure courante (pour l'UI)."""
    now = datetime.now(timezone.utc)
    with _LOCK:
        _prune(now)
        return max(0, max_per_hour() - len(_RECENT))


def clear_for_tests() -> None:
    with _LOCK:
        _RECENT.clear()
        _DELEGATED.clear()
