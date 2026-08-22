"""Lot 0.a — Câblage : une SEULE instance `TaskOrchestrator` (web ↔ cœur).

Diag (vérifié) : `core.py` crée un `TaskOrchestrator` **persistant**, tandis que
`web/routes/deps.py` en crée un **second, volatile** (sans `persistence_path`).
Les routes (chat/tasks/system) pilotent le volatile, la boucle ReAct écrit dans
le persistant → états divergents, et le web ne survit pas au reboot.

`unify_task_orchestrator` fait pointer l'instance du web sur celle du cœur
(persistante), au démarrage, après l'init de Lumena. Idempotent, jamais fatal.
"""
from __future__ import annotations

from typing import Any

from loguru import logger


def unify_task_orchestrator(deps_module: Any, lumena: Any) -> bool:
    """Partage l'unique `TaskOrchestrator` persistant du cœur avec le web.

    Args:
        deps_module: le module `web.routes.deps` (porte `_TASK_ORCHESTRATOR`).
        lumena: l'instance `LumenaCore` initialisée (porte `task_orchestrator`).

    Returns:
        True si l'unification a eu lieu, False sinon (lumena absente / pas
        d'orchestrateur). Aucune exception propagée.
    """
    if deps_module is None:
        return False
    orchestrator = getattr(lumena, "task_orchestrator", None) if lumena is not None else None
    if orchestrator is None:
        return False
    try:
        deps_module._TASK_ORCHESTRATOR = orchestrator
    except Exception as exc:  # ultra-défensif : ne jamais casser le boot
        logger.debug("[BOOT] unify_task_orchestrator: {}", exc)
        return False
    logger.info("[BOOT] TaskOrchestrator unifié (web ← cœur, persistant)")
    return True
