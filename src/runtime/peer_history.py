"""Refonte UI réseau — agrégation READ-ONLY de l'historique des échanges Lumena.

Transforme les sources persistées éparpillées (audit inter-instances,
événements de tâches, connaissances partagées) en **fils lisibles** pour la
vue produit « Historique des échanges ».

Invariants (validés) :
  - **READ-ONLY strict** : ce module n'écrit/ne mute/ne déclenche RIEN. Il lit
    des structures déjà chargées et produit une vue.
  - **Sanitizé** : aucun token / peer_token / fleet_key / en-tête signé /
    payload brut ne doit ressortir ; les `detail` sont rédigés + tronqués.

Module **pur** (aucune I/O) → entièrement testable.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

# Préfixe d'événement → type d'échange (ordre = priorité de correspondance).
_HISTORY_TYPE_BY_PREFIX: List[tuple[str, str]] = [
    ("delegate", "delegation"),
    ("task_sync", "task"),
    ("task_async", "task"),
    ("knowledge_query", "knowledge_query"),
    ("knowledge_share", "knowledge_share"),
    ("mission", "mission"),
]

_MAX_DETAIL_CHARS = 300

# Champs qui ne doivent JAMAIS ressortir dans un item d'historique (défense en
# profondeur — même si les sources ne sont pas censées en contenir).
_FORBIDDEN_KEYS = frozenset({
    "token", "peer_token", "peer_token_hash", "peer_token_outbound",
    "fleet_key", "authorization", "x-lumena-sig", "payload", "secret",
})


def history_type_for_event(event: str) -> Optional[str]:
    """Type d'échange pour un événement d'audit, ou None si à exclure.

    Les événements sans correspondance (jumelage, système…) sont exclus de
    l'historique des *échanges* (ils ne sont pas des conversations).
    """
    ev = str(event or "")
    for prefix, kind in _HISTORY_TYPE_BY_PREFIX:
        if ev.startswith(prefix):
            return kind
    return None


def sanitize_detail(detail: Any) -> str:
    """Rédige tout pattern secret et tronque un champ `detail` libre."""
    from src.runtime.peer_messages import redact_string
    text = redact_string(str(detail or ""))
    if len(text) > _MAX_DETAIL_CHARS:
        text = text[: _MAX_DETAIL_CHARS - 1] + "…"
    return text


def _direction(peer_id: str, own_id: str) -> str:
    if peer_id and peer_id == own_id:
        return "outbound"
    return "inbound"


def build_peer_history(
    *,
    audit: List[dict],
    task_events: List[dict],
    knowledge: List[dict],
    own_id: str,
    peer_names: Dict[str, str],
    missions: Optional[List[dict]] = None,
    limit: int = 200,
) -> List[dict]:
    """Construit la liste de fils d'échanges, triés du plus récent au plus ancien.

    `audit` et `task_events` : entrées `{ts, event, from_instance_id, task_id,
    scope, status, detail}`. `knowledge` : vues publiques `{id, title, summary,
    origin_instance_id, shared_with_peer_id, created_at, tags}`. `missions` :
    missions SORTANTES suivies par A (tracker) `{task_id, peer_id, peer_name,
    objective, status, submitted_at, last_poll, result}`.
    """
    threads: Dict[str, dict] = {}

    # Noms des pairs : on enrichit avec ceux portés par les missions (le tracker
    # connaît le nom même si le registre ne l'a plus).
    for _m in (missions or []):
        _pid = str(_m.get("peer_id") or "")
        if _pid and _m.get("peer_name") and _pid not in peer_names:
            peer_names = {**peer_names, _pid: _m["peer_name"]}

    def _touch(key: str, *, kind: str, peer_id: str, direction: str, title: str, status: str, ts: str) -> dict:
        th = threads.get(key)
        if th is None:
            th = {
                "id": key,
                "type": kind,
                "peer_id": peer_id,
                "peer_name": peer_names.get(peer_id) or (peer_id[:12] if peer_id else "?"),
                "direction": direction,
                "title": title,
                "status": status,
                "last_ts": ts,
                "items": [],
            }
            threads[key] = th
        if ts and ts >= (th["last_ts"] or ""):
            th["last_ts"] = ts
            if status:
                th["status"] = status
        return th

    # Audit + événements de tâches : clés par task_id.
    for source in (audit or [], task_events or []):
        for entry in source:
            if not isinstance(entry, dict):
                continue
            kind = history_type_for_event(entry.get("event", ""))
            if not kind:
                continue
            tid = str(entry.get("task_id") or "").strip()
            if not tid:
                continue
            peer_id = str(entry.get("from_instance_id") or "")
            ts = str(entry.get("ts") or "")
            status = str(entry.get("status") or "")
            scope = str(entry.get("scope") or "")
            # `from_instance_id` = l'initiateur de l'échange. S'il vaut notre id,
            # c'est nous qui avons émis (outbound) ; sinon c'est un appel reçu.
            th = _touch(
                f"task:{tid}", kind=kind, peer_id=peer_id,
                direction=_direction(peer_id, own_id),
                title=f"{scope or kind} · {tid}", status=status, ts=ts,
            )
            th["items"].append({
                "ts": ts,
                "event": str(entry.get("event") or ""),
                "status": status,
                "detail": sanitize_detail(entry.get("detail", "")),
            })

    # Connaissances partagées : chaque entrée = un fil « savoir ».
    for rec in (knowledge or []):
        if not isinstance(rec, dict):
            continue
        kid = str(rec.get("id") or rec.get("title") or "").strip()
        if not kid:
            continue
        origin = str(rec.get("origin_instance_id") or "")
        shared_with = str(rec.get("shared_with_peer_id") or "")
        # peer = l'AUTRE bout : si on est l'origine → le destinataire, sinon l'origine.
        # direction : on partage (outbound) si on est l'origine, sinon on reçoit.
        is_origin = origin == own_id
        peer_id = shared_with if is_origin else origin
        ts = str(rec.get("created_at") or "")
        th = _touch(
            f"know:{kid}", kind="knowledge", peer_id=peer_id,
            direction="outbound" if is_origin else "inbound",
            title=str(rec.get("title") or "(savoir)"), status="shared", ts=ts,
        )
        th["items"].append({
            "ts": ts,
            "event": "knowledge_shared",
            "status": "shared",
            "detail": sanitize_detail(rec.get("summary", "")),
        })

    # Missions SORTANTES (tracker côté A) : un fil `mission` par task_id.
    _terminal = {"completed", "failed", "timeout", "cancelled", "interrupted"}
    for m in (missions or []):
        if not isinstance(m, dict):
            continue
        tid = str(m.get("task_id") or "").strip()
        if not tid:
            continue
        peer_id = str(m.get("peer_id") or "")
        status = str(m.get("status") or "queued")
        submitted = str(m.get("submitted_at") or "")
        last = str(m.get("last_poll") or submitted)
        objective = str(m.get("objective") or "")
        th = _touch(
            f"mission:{tid}", kind="mission", peer_id=peer_id, direction="outbound",
            title=f"Mission · {objective or tid}", status=status, ts=last,
        )
        th["items"].append({
            "ts": submitted, "event": "mission_assigned", "status": "queued",
            "detail": sanitize_detail(objective),
        })
        if status in _terminal:
            th["items"].append({
                "ts": last, "event": f"mission_{status}", "status": status,
                "detail": sanitize_detail(m.get("result", "")),
            })

    out = list(threads.values())
    for th in out:
        th["items"].sort(key=lambda i: i.get("ts", ""))
        _assert_no_forbidden(th)
    out.sort(key=lambda t: t.get("last_ts", ""), reverse=True)
    return out[: max(1, limit)]


def history_stats(exchanges: List[dict]) -> Dict[str, int]:
    """Petites stats d'en-tête pour l'UI (à partir des fils déjà construits)."""
    return {
        "total": len(exchanges),
        "completed": sum(1 for e in exchanges if e.get("status") == "completed"),
        "running": sum(1 for e in exchanges if e.get("status") in ("running", "queued")),
        "knowledge": sum(1 for e in exchanges if e.get("type") == "knowledge"),
    }


def _assert_no_forbidden(thread: dict) -> None:
    """Garde-fou défensif : aucune clé interdite ne doit figurer dans un fil."""
    for key in thread.keys():
        if str(key).lower() in _FORBIDDEN_KEYS:
            thread.pop(key, None)
    for item in thread.get("items", []):
        for key in list(item.keys()):
            if str(key).lower() in _FORBIDDEN_KEYS:
                item.pop(key, None)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
