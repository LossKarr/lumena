"""Lot E3 Phase 10 — Rate limiting inter-pairs V1.

Compteur glissant par (peer_id, scope), fenêtre de 60 secondes.
Thread-safe, aucune dépendance externe.

Variables d'environnement :
  LUMENA_RATE_KNOWLEDGE_QUERY   — limite req/min pour knowledge.query (défaut : 30)
  LUMENA_RATE_TASK_DELEGATE     — limite req/min pour task.delegate   (défaut : 10)
  LUMENA_PEER_MAX_PARALLEL_TASKS — max tâches async simultanées/pair  (défaut : 2)

Usage :
    from src.runtime.peer_rate_limit import check_rate_limit, check_max_parallel_tasks

    allowed, retry_after = check_rate_limit("peer-id", "task.delegate")
    if not allowed:
        raise HTTPException(429, headers={"Retry-After": str(retry_after)})

    allowed, retry_after = check_max_parallel_tasks("peer-id", active_count)
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict
from typing import Dict, List, Tuple

# ── Constantes ─────────────────────────────────────────────────────────────────

_WINDOW_SECONDS: int = 60

_DEFAULT_LIMITS: Dict[str, int] = {
    "knowledge.query": 30,
    "task.delegate": 10,
}

_ENV_KEYS: Dict[str, str] = {
    "knowledge.query": "LUMENA_RATE_KNOWLEDGE_QUERY",
    "task.delegate": "LUMENA_RATE_TASK_DELEGATE",
}

# ── État interne ───────────────────────────────────────────────────────────────

# (peer_id, scope) → liste de timestamps monotoniques
_counters: Dict[Tuple[str, str], List[float]] = defaultdict(list)
_counter_lock = threading.Lock()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_limit(scope: str) -> int:
    """Retourne la limite req/min pour un scope donné (env override ou défaut)."""
    default = _DEFAULT_LIMITS.get(scope, 60)
    env_key = _ENV_KEYS.get(scope)
    if env_key:
        try:
            return max(1, int(os.getenv(env_key, str(default))))
        except (ValueError, TypeError):
            pass
    return default


def get_max_parallel_tasks() -> int:
    """Retourne le max de tâches async simultanées par pair."""
    try:
        return max(1, int(os.getenv("LUMENA_PEER_MAX_PARALLEL_TASKS", "2")))
    except (ValueError, TypeError):
        return 2


# ── API publique ───────────────────────────────────────────────────────────────

def check_rate_limit(peer_id: str, scope: str) -> Tuple[bool, int]:
    """Vérifie le rate limit pour (peer_id, scope).

    Retourne :
        (True, 0)              — requête autorisée, compteur incrémenté
        (False, retry_after)   — limite atteinte, retry_after en secondes
    """
    now = time.monotonic()
    limit = _get_limit(scope)
    key = (peer_id, scope)

    with _counter_lock:
        cutoff = now - _WINDOW_SECONDS
        _counters[key] = [t for t in _counters[key] if t > cutoff]

        if len(_counters[key]) >= limit:
            oldest = min(_counters[key])
            retry_after = int(_WINDOW_SECONDS - (now - oldest)) + 1
            return False, max(1, retry_after)

        _counters[key].append(now)
        return True, 0


def check_max_parallel_tasks(peer_id: str, active_count: int) -> Tuple[bool, int]:
    """Vérifie si le pair a atteint la limite de tâches async simultanées.

    active_count est passé par l'appelant (depuis _async_task_store) pour
    éviter une dépendance circulaire avec peers.py.

    Retourne :
        (True, 0)              — nouvelle tâche autorisée
        (False, retry_after)   — limite atteinte, retry_after en secondes
    """
    max_parallel = get_max_parallel_tasks()
    if active_count >= max_parallel:
        return False, 60
    return True, 0


def reset_peer_counters(peer_id: str) -> None:
    """Remet à zéro tous les compteurs d'un pair. Réservé aux tests."""
    with _counter_lock:
        keys_to_delete = [k for k in _counters if k[0] == peer_id]
        for k in keys_to_delete:
            del _counters[k]


def current_count(peer_id: str, scope: str) -> int:
    """Retourne le nombre de requêtes dans la fenêtre courante. Réservé aux tests."""
    now = time.monotonic()
    key = (peer_id, scope)
    with _counter_lock:
        cutoff = now - _WINDOW_SECONDS
        return sum(1 for t in _counters[key] if t > cutoff)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
