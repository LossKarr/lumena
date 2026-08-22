"""Lot 2.1 + 5.1 — File NEUTRE des missions, **pools par profondeur** (anti-deadlock).

Une mission = une boucle ReAct complète (« Lumena complète »). En lancer trop en
parallèle saturerait le LLM. Ce module borne le nombre de missions exécutées
**simultanément** ; les missions en trop **attendent leur tour** (`queued`).

Lot 5.1 — **un sémaphore PAR PROFONDEUR** (pas un global). Raison : un *lead*
(profondeur d) qui attend ses *workers* (profondeur d+1) tient un créneau de SON
pool ; les workers prennent le pool d+1, **indépendant** → ils ne sont JAMAIS bloqués
par leur lead. Vrai pour toute profondeur → **aucun deadlock cross-niveau**. C'est la
garantie « 24/7 sans blocage » de la collaboration.

Tailles : profondeur ≤ 1 (chat/leads top) = `LUMENA_MISSION_CONCURRENCY` (défaut 1) ;
profondeur ≥ 2 (workers) = `LUMENA_MISSION_WORKER_CONCURRENCY` (défaut 2).

Primitive **neutre** : aucun lien P2P (on ne réutilise PAS `peer_mission_worker`).
État en mémoire process (sémaphores asyncio FIFO). Concurrence lue à la création d'un
pool ; la changer nécessite un redémarrage (ou `reset_for_tests`).
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Dict, Optional

_DEFAULT_CONCURRENCY = 1
_DEFAULT_WORKER_CONCURRENCY = 2

# Pools PAR PROFONDEUR (créés à la demande).
_semaphores: Dict[int, asyncio.Semaphore] = {}
_waiting: Dict[int, int] = {}   # missions qui attendent un créneau, par profondeur
_running: Dict[int, int] = {}   # missions en cours, par profondeur


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, min(64, int(os.getenv(name, str(default)))))
    except (ValueError, TypeError):
        return default


def get_subagent_concurrency() -> int:
    """Missions top (profondeur ≤ 1) en parallèle (`LUMENA_MISSION_CONCURRENCY`, défaut 1, 1..64)."""
    return _env_int("LUMENA_MISSION_CONCURRENCY", _DEFAULT_CONCURRENCY)


def get_worker_concurrency() -> int:
    """Workers (profondeur ≥ 2) en parallèle (`LUMENA_MISSION_WORKER_CONCURRENCY`, défaut 2, 1..64)."""
    return _env_int("LUMENA_MISSION_WORKER_CONCURRENCY", _DEFAULT_WORKER_CONCURRENCY)


def _pool_size(depth: int) -> int:
    """Taille du pool d'une profondeur : top (≤1) vs worker (≥2)."""
    return get_subagent_concurrency() if depth <= 1 else get_worker_concurrency()


def _get_semaphore(depth: int) -> asyncio.Semaphore:
    sem = _semaphores.get(depth)
    if sem is None:
        sem = asyncio.Semaphore(_pool_size(depth))
        _semaphores[depth] = sem
    return sem


@asynccontextmanager
async def mission_slot(depth: int = 1):
    """Acquiert un créneau du pool de `depth` ; **attend** si ce pool est plein.

    Chaque profondeur a son propre pool → un lead n'épuise jamais le pool de ses
    workers. Relâche **toujours**, même sur exception.
    """
    try:
        depth = int(depth)
    except (ValueError, TypeError):
        depth = 1
    sem = _get_semaphore(depth)
    _waiting[depth] = _waiting.get(depth, 0) + 1
    try:
        await sem.acquire()
    finally:
        _waiting[depth] -= 1
    _running[depth] = _running.get(depth, 0) + 1
    try:
        yield
    finally:
        _running[depth] -= 1
        sem.release()


def queue_load() -> dict:
    """État courant de la file (sans I/O) — pour l'UI et le suivi.

    `running`/`waiting` = **agrégés** toutes profondeurs (compat) ; `by_depth` détaille
    chaque pool ; `concurrency`/`worker_concurrency` = tailles configurées.
    """
    depths = sorted(set(_semaphores) | set(_running) | set(_waiting))
    return {
        "running": sum(_running.values()),
        "waiting": sum(_waiting.values()),
        "concurrency": get_subagent_concurrency(),
        "worker_concurrency": get_worker_concurrency(),
        "by_depth": {
            d: {
                "running": _running.get(d, 0),
                "waiting": _waiting.get(d, 0),
                "concurrency": _pool_size(d),
            }
            for d in depths
        },
    }


def reset_for_tests() -> None:
    """Réinitialise l'état (pools + compteurs) — usage tests uniquement."""
    global _semaphores, _waiting, _running
    _semaphores = {}
    _waiting = {}
    _running = {}
