"""Brique 3 (M1) — File d'attente des missions inter-Lumena (anti-saturation).

Une mission lourde reçue d'un pair = une **boucle ReAct complète**. Si plusieurs
missions arrivent en même temps, les lancer toutes en parallèle saturerait le LLM
et entrerait en concurrence avec l'agent interactif local.

Ce module borne le nombre de missions exécutées **simultanément** (défaut : **1**,
soit une à la fois). Les missions en trop **attendent leur tour** (elles restent au
statut `queued` côté registre) au lieu de tout démarrer d'un coup. C'est le
« worker / gestionnaire de file d'attente » décrit dans le plan.

Implémentation : un sémaphore asyncio (FIFO des attentes). Léger, sans thread,
sans I/O. La concurrence est lue depuis l'env au premier usage ; la changer
nécessite un redémarrage (le sémaphore est créé une seule fois).
"""
from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from typing import Optional

_DEFAULT_CONCURRENCY = 1

_semaphore: Optional[asyncio.Semaphore] = None
_waiting = 0   # missions qui attendent un créneau
_running = 0   # missions en cours d'exécution


def get_mission_concurrency() -> int:
    """Nombre de missions exécutables en parallèle (env, défaut 1, min 1)."""
    try:
        return max(1, int(os.getenv("LUMENA_PEER_MISSION_CONCURRENCY", str(_DEFAULT_CONCURRENCY))))
    except (ValueError, TypeError):
        return _DEFAULT_CONCURRENCY


def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(get_mission_concurrency())
    return _semaphore


@asynccontextmanager
async def mission_slot():
    """Acquiert un créneau d'exécution de mission ; **attend** si la file est pleine.

    À utiliser autour de l'exécution lourde (ReAct). Tant que le créneau n'est pas
    obtenu, la mission patiente — c'est la file d'attente.
    """
    global _waiting, _running
    sem = _get_semaphore()
    _waiting += 1
    try:
        await sem.acquire()
    finally:
        _waiting -= 1
    _running += 1
    try:
        yield
    finally:
        _running -= 1
        sem.release()


def mission_load() -> dict:
    """État courant de la file (sans I/O) — pour l'UI et l'accusé de réception."""
    return {
        "running": _running,
        "waiting": _waiting,
        "concurrency": get_mission_concurrency(),
    }


def reset_for_tests() -> None:
    """Réinitialise l'état (sémaphore + compteurs) — usage tests uniquement."""
    global _semaphore, _waiting, _running
    _semaphore = None
    _waiting = 0
    _running = 0
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
