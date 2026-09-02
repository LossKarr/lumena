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
    """Liste les missions (en cours + passées), plus récentes d'abord.

    Chaque mission est annotée du POIDS de son journal archivé. Sans cela, le
    panneau ne peut pas dire lesquelles des missions terminées gardent une
    trace : il faudrait les déplier une par une pour le découvrir.

    Un SEUL balayage de répertoire pour toutes — mesuré à 8,7 ms pour 5 000
    fichiers avec leurs tailles, contre 17,9 ms pour 670 `exists()` un par un.
    """
    from src.telemetry import mission_journal

    items = _manager().list_missions(limit=limit)
    items = list(reversed(items))  # plus récent en tête

    # Le journal est un CONFORT, la liste des missions est le produit. Un
    # inventaire qui echoue ne doit pas emporter l'ecran entier : on annote a
    # zero et le panneau montre les missions sans leur marqueur d'archive.
    try:
        inv = mission_journal.inventaire()
    except Exception:
        inv = {"entries": {}, "files": 0, "bytes": 0}
    tailles = inv.get("entries") or {}
    for it in items:
        if isinstance(it, dict):
            try:
                it["journal_bytes"] = int(tailles.get(str(it.get("task_id") or ""), 0))
            except Exception:
                it["journal_bytes"] = 0

    return {
        "success": True,
        "missions": items,
        "count": len(items),
        # Le panneau doit pouvoir DIRE ce qu'il garde : un systeme qui archive
        # sans jamais montrer son empreinte n'est pas fini.
        "journal": {"files": inv.get("files", 0), "bytes": inv.get("bytes", 0)},
    }


@router.get("/api/missions/{mission_id}", dependencies=[Depends(deps.verify_admin_token)])
async def get_mission(mission_id: str) -> Dict[str, Any]:
    """Détail d'une mission (état, métadonnées, résultat, livrables)."""
    task = _manager().get_mission(mission_id)
    if not task:
        raise HTTPException(status_code=404, detail="Mission inconnue")
    return {"success": True, "mission": task}


@router.get("/api/missions/{mission_id}/journal",
            dependencies=[Depends(deps.verify_admin_token)])
async def get_mission_journal(mission_id: str, limit: int = 400) -> Dict[str, Any]:
    """Journal persiste d'une mission — ce qui reste quand elle est finie.

    Le raisonnement de l'agent ne vivait que sur le flux SSE et dans l'anneau
    serveur de 500 evenements : mesure faite sur 670 taches persistees, AUCUNE
    n'en gardait la moindre trace. Une mission terminee ne pouvait donc plus
    etre rouverte.

    Cet endpoint sert le fichier grave par `src.telemetry.mission_journal`, du
    plus ancien au plus recent, dans le MEME format que les evenements du flux
    live — pour que le panneau puisse le rejouer sans une ligne de rendu de
    plus.

    Ne verifie PAS que la mission existe encore : un journal survit a sa tache,
    et c'est precisement l'interet.
    """
    from src.telemetry import mission_journal

    borne = max(1, min(int(limit or 400), 5000))

    # LE LEAD ET SES WORKERS. Chaque tache a son propre `task_id`, donc son
    # propre fichier : ne servir que celui de la mission montrerait le lead
    # seul, avec des workers muets. « Rouvrir une mission » veut dire voir
    # l'equipe entiere.
    ids = [mission_id]
    try:
        for enfant in _manager()._orch.get_children(mission_id):
            tid = str((enfant or {}).get("task_id") or "").strip()
            if tid and tid not in ids:
                ids.append(tid)
    except Exception:
        pass                      # sans les enfants, le lead vaut mieux que rien

    events = []
    for tid in ids:
        for e in mission_journal.lis(tid, limit=borne):
            # `task_id` n'est pas grave dans le fichier (il EST le nom du
            # fichier) : on le remet, parce que le modele du panneau indexe
            # par lui.
            e["task_id"] = tid
            events.append(e)

    # Ordre chronologique REEL entre taches. `seq` est global au bus, donc
    # croissant — mais il repart de zero au redemarrage du serveur. `ts` est
    # un horodatage UTC ISO, donc triable comme une chaine et stable a travers
    # les redemarrages : il passe en premier.
    events.sort(key=lambda e: (str(e.get("ts") or ""), int(e.get("seq") or 0)))
    if len(events) > borne:
        events = events[-borne:]  # on garde la FIN : c'est la conclusion

    st = mission_journal.stats(mission_id)
    return {
        "success": True,
        "mission_id": mission_id,
        "tasks": ids,
        "events": events,
        "count": len(events),
        **st,
    }


@router.delete("/api/missions/{mission_id}", dependencies=[Depends(deps.verify_admin_token)])
async def cancel_mission(mission_id: str) -> Dict[str, Any]:
    """Annulation coopérative (s'arrête au prochain checkpoint, jamais en plein milieu)."""
    out = _manager().cancel_mission(mission_id)
    return {"success": bool(out.get("success")), **out}
