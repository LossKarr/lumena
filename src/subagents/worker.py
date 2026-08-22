"""Lot 4 (correctif) — Worker de missions à durée de vie APPLICATION.

Bug runtime trouvé : une mission lancée via `asyncio.create_task` DEPUIS la requête
chat ne survit pas à la fin du `StreamingResponse` → elle s'arrête en plein milieu
(observée `checkpointed`, jamais `done`).

Correctif : le manager **met en file** (`MissionManager.launch`) ; ce worker, démarré
au `lifespan` (comme le heartbeat), consomme la file et exécute `run_mission`
**indépendamment de toute requête**. Concurrence bornée par `mission_slot`.

⚠️ Aucun lien avec le CodeAgent. L'exécution reste `runner.run_mission` (sous-agent
« Lumena complète »).
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from loguru import logger

from src.subagents.manager import get_mission_manager
from src.subagents.queue import mission_slot
from src.subagents.runner import run_mission

_worker_task: Optional["asyncio.Task"] = None


def _mission_depth(core: Any, mission_id: str) -> int:
    """Profondeur de la mission (1 = lead/top). Lue dans le metadata, défensive → 1."""
    try:
        orch = getattr(core, "task_orchestrator", None)
        task = orch.get_task(mission_id) if orch else None
        return max(1, int(((task or {}).get("metadata") or {}).get("depth") or 1))
    except Exception:
        return 1


async def _run_item(core: Any, mgr: Any, item: tuple) -> None:
    """Exécute une mission en file, bornée par le créneau de concurrence de SA profondeur."""
    mission_id, objective, timeout, allowed_tools = item
    depth = _mission_depth(core, mission_id)
    try:
        async with mission_slot(depth):  # pool PAR profondeur → un lead ne bloque pas ses workers
            await run_mission(
                core, mission_id=mission_id, objective=objective,
                timeout=timeout, allowed_tools=allowed_tools,
            )
    except asyncio.CancelledError:
        raise  # annulation de la tâche worker (shutdown) → propage
    except BaseException as exc:  # noqa: BLE001 — y compris SystemExit (cancel) : ne JAMAIS tuer l'app
        logger.warning("[mission-worker] run {} échec: {}", mission_id, exc)
    finally:
        try:
            mgr._inflight.discard(mission_id)
        except Exception:
            pass


async def mission_worker_loop(core: Any) -> None:
    """Boucle app-lifetime : consomme la file et lance chaque mission (non bloquant)."""
    mgr = get_mission_manager(core)
    q = mgr.queue()
    logger.info("[mission-worker] démarré")
    while True:
        try:
            item = await q.get()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.debug("[mission-worker] get: {}", exc)
            await asyncio.sleep(0.5)
            continue
        # spawn app-lifetime (le worker survit aux requêtes) ; mission_slot borne la concurrence
        asyncio.create_task(_run_item(core, mgr, item))


def start_mission_worker(core: Any) -> Optional["asyncio.Task"]:
    """Démarre le worker (idempotent). À appeler au `lifespan`. Jamais fatal."""
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        return _worker_task
    try:
        _worker_task = asyncio.create_task(mission_worker_loop(core))
    except Exception as exc:
        logger.debug("[mission-worker] start skip: {}", exc)
        _worker_task = None
    return _worker_task


# ── tests ───────────────────────────────────────────────────────────────────────

async def drain_for_tests(core: Any) -> None:
    """Tests : exécute séquentiellement toutes les missions en file (sans worker de fond)."""
    mgr = get_mission_manager(core)
    q = mgr.queue()
    while not q.empty():
        await _run_item(core, mgr, await q.get())


def reset_worker_for_tests() -> None:
    global _worker_task
    if _worker_task is not None and not _worker_task.done():
        _worker_task.cancel()
    _worker_task = None
