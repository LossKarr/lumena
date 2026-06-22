"""Brique 3 (M3) — Suivi des missions SORTANTES (côté A) + notification.

Quand A confie une mission à B (async), A l'enregistre ici puis **rend la main
immédiatement**. Une boucle de fond (peer_network_autonomy) appelle
`poll_outbound_missions()` à chaque cycle : elle interroge le statut de chaque
mission en cours chez le pair (appel **signé A2**), et à la complétion **notifie
l'utilisateur**.

Résilience : le store est **persisté sur disque** → survit au reboot de A et aux
coupures réseau (on re-poll au cycle suivant ; une erreur réseau ne lève jamais).

Routage de la notification (décision utilisateur) :
  - canal **Telegram** → notification **poussée** (`_notify_telegram_proactive`) ;
  - **tout le reste (web inclus)** → la mission terminée reste « à signaler »
    (`pending_web_reminders()`) → rappel au prochain message + visible dans l'UI.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from src.utils.paths import DATA_DIR

_TRACKER_FILE = DATA_DIR / "peer_missions" / "outbound_missions.json"
_LOCK = threading.Lock()

# Statuts terminaux d'une mission.
_TERMINAL: frozenset[str] = frozenset({"completed", "failed", "timeout", "cancelled", "interrupted", "refused"})

_MAX_RESULT_CHARS = 8000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load() -> Dict[str, dict]:
    try:
        if _TRACKER_FILE.exists():
            data = json.loads(_TRACKER_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save(data: Dict[str, dict]) -> None:
    try:
        _TRACKER_FILE.parent.mkdir(parents=True, exist_ok=True)
        _TRACKER_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


# ── CRUD ──────────────────────────────────────────────────────────────────────

def register_outbound_mission(
    *,
    task_id: str,
    peer_id: str,
    peer_name: str,
    host: str,
    port: int,
    objective: str,
    channel: str = "web",
    dest_id: str = "",
) -> dict:
    """Enregistre une mission confiée à un pair (statut initial `queued`)."""
    entry = {
        "task_id": task_id,
        "peer_id": peer_id,
        "peer_name": peer_name or (peer_id[:12] if peer_id else "?"),
        "host": host,
        "port": port,
        "objective": (objective or "")[:200],
        "channel": (channel or "web").strip().lower(),
        "dest_id": dest_id,
        "status": "queued",
        "result": "",
        "submitted_at": _now(),
        "last_poll": "",
        "notified": False,
        "web_ack": False,
    }
    with _LOCK:
        data = _load()
        data[task_id] = entry
        _save(data)
    _publish_mission_event(entry)  # Cran 2 : push « queued » immédiat
    return entry


def list_pending() -> List[dict]:
    """Missions encore en cours (non terminales)."""
    with _LOCK:
        return [m for m in _load().values() if m.get("status") not in _TERMINAL]


def get_mission(task_id: str) -> Optional[dict]:
    with _LOCK:
        return _load().get(task_id)


def list_all_missions() -> List[dict]:
    """Toutes les missions sortantes (read-only) — pour l'historique côté A."""
    with _LOCK:
        return list(_load().values())


def _publish_mission_event(mission: dict) -> None:
    """Cran 2 — pousse l'état d'une mission sur le bus SSE (best-effort, sans payload brut)."""
    try:
        from src.runtime.peer_event_bus import publish_peer_event
        publish_peer_event(
            "mission",
            task_id=mission.get("task_id", ""),
            peer_id=mission.get("peer_id", ""),
            peer_name=mission.get("peer_name", ""),
            objective=str(mission.get("objective") or "")[:200],
            status=mission.get("status", ""),
            submitted_at=mission.get("submitted_at", ""),
        )
    except Exception:
        pass


def update_status(task_id: str, status: str, result: str = "") -> Optional[dict]:
    snap: Optional[dict] = None
    with _LOCK:
        data = _load()
        m = data.get(task_id)
        if not m:
            return None
        m["status"] = status
        if result:
            m["result"] = str(result)[:_MAX_RESULT_CHARS]
        m["last_poll"] = _now()
        _save(data)
        snap = dict(m)
    _publish_mission_event(snap)
    return snap


def set_artifacts(task_id: str, dest: str, count: int) -> None:
    """Enregistre où les artefacts reçus ont atterri (workspace) + le nombre."""
    with _LOCK:
        data = _load()
        m = data.get(task_id)
        if m:
            m["artifacts_dir"] = dest
            m["artifacts_count"] = int(count)
            _save(data)


def mark_notified(task_id: str) -> None:
    with _LOCK:
        data = _load()
        m = data.get(task_id)
        if m:
            m["notified"] = True
            _save(data)


def pending_web_reminders() -> List[dict]:
    """Missions terminées sur canal **non-Telegram**, pas encore vues (web).

    Sert au rappel « au fait, B a fini la mission X » au prochain message.
    """
    with _LOCK:
        return [
            m for m in _load().values()
            if m.get("status") in _TERMINAL
            and m.get("channel") != "telegram"
            and not m.get("web_ack")
        ]


def ack_web_reminders(task_ids: List[str]) -> None:
    """Marque des rappels web comme vus (après les avoir présentés à l'utilisateur)."""
    with _LOCK:
        data = _load()
        changed = False
        for tid in task_ids:
            m = data.get(tid)
            if m and not m.get("web_ack"):
                m["web_ack"] = True
                changed = True
        if changed:
            _save(data)


def reset_for_tests(path=None) -> None:
    """Vide le store (tests). Optionnellement redirige le fichier."""
    global _TRACKER_FILE
    if path is not None:
        _TRACKER_FILE = path
    with _LOCK:
        _save({})


# ── Notification ──────────────────────────────────────────────────────────────

def _build_completion_text(mission: dict) -> str:
    peer = mission.get("peer_name") or (mission.get("peer_id", "")[:8])
    obj = mission.get("objective", "")
    status = mission.get("status")
    if status == "completed":
        summary = (mission.get("result") or "").strip()[:600]
        text = f"✅ {peer} a terminé la mission : {obj}\n\n{summary}".strip()
        n = int(mission.get("artifacts_count") or 0)
        if n:
            text += f"\n\n📦 {n} fichier(s) reçu(s) dans : {mission.get('artifacts_dir', '')}"
        return text
    if status == "interrupted":
        return f"⚠️ La mission confiée à {peer} (« {obj} ») a été interrompue (redémarrage). Tu peux la relancer."
    if status == "refused":
        # Refus : le pair était en lecture seule (niveau chat). Message honnête +
        # action concrète pour l'utilisateur (jamais un faux « ✅ terminé »).
        # Explique POURQUOI, COMMENT débloquer, et QUI (humain only — l'IA ne peut
        # pas changer le réglage, c'est protégé par sécurité).
        return (
            f"⛔ {peer} n'a pas pu réaliser la mission (« {obj} »).\n\n"
            "**Pourquoi** : ce pair est réglé sur le niveau « chat » (lecture seule). "
            "Il peut lire et chercher, mais ne peut rien écrire, créer ni envoyer — "
            "donc aucun livrable n'a été produit.\n\n"
            "**Comment le débloquer** : ouvre le panneau « Pairs », sélectionne ce pair "
            "et passe-le au niveau « mission ». C'est une action volontaire de ta part — "
            "je n'ai aucun moyen de changer ce réglage moi-même (c'est protégé exprès, "
            "par sécurité)."
        )
    return f"⚠️ {peer} n'a pas pu finir la mission (« {obj} ») — statut : {status}."


async def notify_mission_done(mission: dict) -> bool:
    """Notifie la complétion d'une mission. Telegram = push ; sinon → rappel web.

    Retourne True si une notification poussée a été envoyée (Telegram).
    """
    text = _build_completion_text(mission)
    channel = (mission.get("channel") or "web").strip().lower()
    if channel == "telegram":
        try:
            from src.autonomy.ops_handlers import _notify_telegram_proactive
            return bool(await _notify_telegram_proactive(text))
        except Exception:
            return False
    # Web / autres : pas de push → reste dans pending_web_reminders() (rappel + UI).
    return False


# ── Récupération des artefacts (côté A) → workspace ──────────────────────────

async def fetch_mission_artifacts(mission: dict, peer: dict, *, timeout: float = 15.0) -> dict:
    """Télécharge les artefacts d'une mission terminée et les **place dans le
    workspace** (`workspace/inbound/<pair>/<task>/`). Appels **signés A2**,
    hash vérifié, dézip sandboxé. Jamais fatal. Retourne `{received, count, dest}`.
    """
    import httpx
    from src.runtime.peer_signing import build_signed_request
    from src.runtime.peer_artifacts import receive_artifact, reception_dir_for
    from src.utils.paths import INSTANCE_ID as _own_id

    token = (peer or {}).get("peer_token_outbound", "")
    if not token:
        return {"received": False, "reason": "no_token"}
    peer_id = str(mission.get("peer_id") or "")
    base = f"http://{mission['host']}:{mission['port']}/api/peer/artifact/{mission['task_id']}"
    pm = (peer or {}).get("pairing_method", "")
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            _c, h = build_signed_request(None, from_id=_own_id, to_id=peer_id, peer_token=token, pairing_method=pm)
            rm = await client.get(f"{base}/manifest", headers=h)
            if rm.status_code != 200:
                return {"received": False, "reason": f"manifest_{rm.status_code}"}
            man = rm.json()
            if not man.get("available"):
                return {"received": False, "reason": "none"}
            kind = str(man.get("kind") or "zip")
            filename = str(man.get("filename") or "bundle")
            sha = str(man.get("sha256") or "")
            _c2, h2 = build_signed_request(None, from_id=_own_id, to_id=peer_id, peer_token=token, pairing_method=pm)
            rf = await client.get(f"{base}/file", headers=h2)
            if rf.status_code != 200:
                return {"received": False, "reason": f"file_{rf.status_code}"}
            content = rf.content
    except Exception as exc:
        return {"received": False, "reason": type(exc).__name__}

    dest = reception_dir_for(mission.get("peer_name") or peer_id)
    out = receive_artifact(content, kind=kind, filename=filename, expected_sha256=sha, dest_dir=dest)
    if out.get("ok"):
        _register_received_projects(dest, out.get("files") or [])
        return {"received": True, "count": out.get("count", 0), "dest": out.get("dest", str(dest))}
    return {"received": False, "reason": out.get("error")}


def _register_received_projects(dest: Path, files: list) -> None:
    """Enregistre les projets reçus dans le `project_registry` (comme le CodeAgent)
    → Lumena les retrouve par `find_project` (« reprends le projet … »). Jamais fatal.
    """
    try:
        from src.utils.project_registry import register_project
        tops: dict = {}
        for f in files or []:
            try:
                rel = Path(f).resolve().relative_to(dest.resolve())
            except Exception:
                continue
            top = rel.parts[0] if rel.parts else ""
            if not top:
                continue
            tops.setdefault(top, str((dest / top).resolve()))
        for name, path in tops.items():
            try:
                register_project(path, description=f"Reçu d'un pair ({name})", slug=name)
            except Exception:
                pass
    except Exception:
        pass


# ── Poll (réseau) ─────────────────────────────────────────────────────────────

async def poll_outbound_missions(*, timeout: float = 5.0) -> dict:
    """Interroge le statut de chaque mission en cours chez son pair (signé A2).

    À la complétion (statut terminal) → notifie une seule fois. Toute erreur
    réseau est avalée (backoff implicite : on retentera au cycle suivant).
    """
    pending = list_pending()
    if not pending:
        return {"polled": 0, "terminated": 0}

    import httpx
    from src.runtime.peer_network_autonomy import _load_peers
    from src.runtime.peer_signing import build_signed_request
    from src.utils.paths import INSTANCE_ID as _own_id

    peers = _load_peers()
    terminated = 0

    for m in pending:
        peer = peers.get(m.get("peer_id")) or {}
        token = peer.get("peer_token_outbound", "")
        if not token:
            continue
        url = f"http://{m['host']}:{m['port']}/api/peer/tasks/{m['task_id']}/status"
        try:
            _content, headers = build_signed_request(
                None, from_id=_own_id, to_id=m.get("peer_id", ""),
                peer_token=token, pairing_method=peer.get("pairing_method", ""),
            )
            async with httpx.AsyncClient(timeout=timeout) as client:
                r = await client.get(url, headers=headers)
            if r.status_code != 200:
                continue
            data = r.json()
            status = str(data.get("status") or "")
            if not status:
                continue
            update_status(m["task_id"], status, str(data.get("result") or ""))
            if status in _TERMINAL:
                fresh = get_mission(m["task_id"])
                if fresh and not fresh.get("notified"):
                    # Mission réussie → récupérer ses artefacts dans le workspace.
                    if status == "completed":
                        try:
                            art = await fetch_mission_artifacts(fresh, peer)
                            if art.get("received") and art.get("count"):
                                set_artifacts(m["task_id"], art.get("dest", ""), art.get("count", 0))
                                fresh = get_mission(m["task_id"])
                        except Exception:
                            pass
                    await notify_mission_done(fresh)
                    mark_notified(m["task_id"])
                    terminated += 1
        except Exception:
            continue  # erreur réseau (coupure wifi…) → on retentera

    return {"polled": len(pending), "terminated": terminated}
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
