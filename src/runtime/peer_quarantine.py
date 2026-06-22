"""Bloc C-1.b — Quarantaine automatique des pairs Lumena (filet de sécurité).

Un pair qui ENCHAÎNE les échecs (injoignable, timeout, erreur) est isolé tout
seul : on empêche les NOUVELLES délégations vers lui, on alerte (SSE + audit),
et on l'affiche en « quarantaine » dans l'UI.

Principe Lumena 24/7 — on bloque le FUTUR, jamais le PRÉSENT :
- la quarantaine n'est consultée qu'à l'AMORCE d'une nouvelle délégation ;
- elle ne touche PAS la boucle de poll/health → une mission EN COURS avec ce pair
  se termine normalement et son résultat revient.

État stocké dans un fichier DÉDIÉ (pas le registre partagé) pour éviter tout
clobber concurrent : `data/peer_quarantine.json`.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from typing import Dict, List

from src.utils.paths import DATA_DIR

_FILE = DATA_DIR / "peer_quarantine.json"
_LOCK = threading.Lock()


def _threshold() -> int:
    """Nombre d'échecs consécutifs avant quarantaine (env, défaut 5, borné 2..50)."""
    try:
        v = int(os.getenv("LUMENA_PEER_QUARANTINE_THRESHOLD", "5"))
        return max(2, min(50, v))
    except (ValueError, TypeError):
        return 5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> Dict[str, dict]:
    try:
        if _FILE.exists():
            data = json.loads(_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save(data: Dict[str, dict]) -> None:
    try:
        _FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_FILE)
    except Exception:
        pass


def is_quarantined(peer_id: str) -> bool:
    if not peer_id:
        return False
    with _LOCK:
        return bool(_load().get(peer_id, {}).get("quarantined"))


def _publish(event: str, peer_id: str, **extra) -> None:
    try:
        from src.runtime.peer_event_bus import publish_peer_event
        publish_peer_event("quarantine", action=event, peer_id=peer_id, **extra)
    except Exception:
        pass


def _audit(event: str, peer_id: str, detail: str = "") -> None:
    try:
        from src.runtime.peer_protocol import write_audit_log
        write_audit_log(event=event, from_instance_id=peer_id, task_id="quarantine",
                        scope="", status="quarantined" if "set" in event else "ok", detail=detail)
    except Exception:
        pass


def record_anomaly(peer_id: str, kind: str = "failure") -> dict:
    """Incrémente le compteur d'échecs CONSÉCUTIFS. Au seuil → quarantaine.

    Retourne {peer_id, count, quarantined, threshold}.
    """
    if not peer_id:
        return {"peer_id": peer_id, "count": 0, "quarantined": False, "threshold": _threshold()}
    thr = _threshold()
    newly = False
    with _LOCK:
        data = _load()
        entry = data.get(peer_id) or {"count": 0, "quarantined": False}
        if not entry.get("quarantined"):
            entry["count"] = int(entry.get("count", 0)) + 1
            entry["last_kind"] = str(kind)[:40]
            entry["last_at"] = _now()
            if entry["count"] >= thr:
                entry["quarantined"] = True
                entry["reason"] = f"{entry['count']} échecs consécutifs ({kind})"
                entry["since"] = _now()
                newly = True
        data[peer_id] = entry
        _save(data)
        snap = dict(entry)
    if newly:
        _audit("peer_quarantine_set", peer_id, snap.get("reason", ""))
        _publish("set", peer_id, reason=snap.get("reason", ""), count=snap.get("count", 0))
    return {"peer_id": peer_id, "count": snap.get("count", 0),
            "quarantined": bool(snap.get("quarantined")), "threshold": thr}


def record_success(peer_id: str) -> None:
    """Un succès remet le compteur à zéro (n'enlève PAS une quarantaine déjà posée :
    la levée reste un acte explicite, conservateur)."""
    if not peer_id:
        return
    with _LOCK:
        data = _load()
        entry = data.get(peer_id)
        if entry and not entry.get("quarantined") and entry.get("count"):
            entry["count"] = 0
            data[peer_id] = entry
            _save(data)


def release(peer_id: str) -> bool:
    """Lève la quarantaine d'un pair (acte humain) + reset compteur."""
    if not peer_id:
        return False
    with _LOCK:
        data = _load()
        if peer_id in data:
            data[peer_id] = {"count": 0, "quarantined": False}
            _save(data)
    _audit("peer_quarantine_released", peer_id, "Quarantaine levée")
    _publish("released", peer_id)
    return True


def list_quarantined() -> List[dict]:
    """Liste des pairs actuellement en quarantaine (pour l'UI)."""
    with _LOCK:
        data = _load()
    out = []
    for pid, e in data.items():
        if e.get("quarantined"):
            out.append({"peer_id": pid, "reason": e.get("reason", ""),
                        "since": e.get("since", ""), "count": e.get("count", 0)})
    out.sort(key=lambda x: x.get("since", ""), reverse=True)
    return out


def clear_for_tests() -> None:
    with _LOCK:
        _save({})
