"""Lot 4.2 — Endpoints du panneau Missions (vue + contrôle).

Vue du système de missions (sous-agents) : liste, détail, annulation. Le **streaming
live** par mission réutilise `/api/trace/stream` (events tagués `task_id=mission_id`,
cf. Lot 4.1) — aucun flux à créer ici.
"""
from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException

from web.routes import deps

router = APIRouter()


def _manager():
    core = getattr(deps, "lumena", None)
    if core is None or getattr(core, "task_orchestrator", None) is None:
        raise HTTPException(status_code=503, detail="Système de missions indisponible")
    from src.subagents.manager import get_mission_manager
    return get_mission_manager(core)


@router.get("/api/missions", dependencies=[Depends(deps.verify_admin_token)])
async def list_missions(limit: int = 100) -> Dict[str, Any]:
    """Liste les missions (en cours + passées), plus récentes d'abord."""
    items = _manager().list_missions(limit=limit)
    items = list(reversed(items))  # plus récent en tête
    return {"success": True, "missions": items, "count": len(items)}


@router.get("/api/missions/{mission_id}", dependencies=[Depends(deps.verify_admin_token)])
async def get_mission(mission_id: str) -> Dict[str, Any]:
    """Détail d'une mission (état, métadonnées, résultat, livrables)."""
    task = _manager().get_mission(mission_id)
    if not task:
        raise HTTPException(status_code=404, detail="Mission inconnue")
    return {"success": True, "mission": task}


@router.delete("/api/missions/{mission_id}", dependencies=[Depends(deps.verify_admin_token)])
async def cancel_mission(mission_id: str) -> Dict[str, Any]:
    """Annulation coopérative (s'arrête au prochain checkpoint, jamais en plein milieu)."""
    out = _manager().cancel_mission(mission_id)
    return {"success": bool(out.get("success")), **out}
