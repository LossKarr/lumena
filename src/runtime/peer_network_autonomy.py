"""Phase 11C - autonomous peer network maintenance.

Small service layer for inter-Lumena networking:
- periodic LAN discovery when enabled;
- periodic health checks for known peers;
- safe registry updates without touching secrets or trust decisions;
- compact status snapshot for chat/UI.

It never auto-trusts a peer. Unknown peers remain unknown until the explicit
pairing flow creates peer tokens.
"""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.paths import DATA_DIR

_PEER_REGISTRY_FILE = DATA_DIR / "peer_registry.json"
_STATE_LOCK = threading.Lock()
_PEER_LOCK = threading.Lock()
_TASK: Optional[asyncio.Task] = None

_STATE: Dict[str, Any] = {
    "enabled": False,
    "running": False,
    "last_run_at": "",
    "last_scan_at": "",
    "last_health_at": "",
    "last_error": "",
    "last_summary": {
        "known": 0,
        "trusted": 0,
        "reachable_trusted": 0,
        "unknown": 0,
        "blocked": 0,
        "discovered": 0,
        "needs_pairing": [],
        "down_trusted": [],
    },
}


_TRUTHY = {"1", "true", "yes", "on"}


def is_peer_master_enabled() -> bool:
    """Interrupteur MAÎTRE du réseau Lumena (`LUMENA_PEER_ENABLED`, défaut 0).

    Quand activé, il allume d'un coup les 4 capacités P2P (découverte, conscience,
    autonomie réseau, collaboration) via un OR-fallback dans chaque garde. Les flags
    unitaires restent souverains : maître OFF → ils commandent ; maître ON → tout on.
    Lu en live (effet immédiat pour chat/collaboration ; découverte/autonomie au reboot).
    """
    return os.getenv("LUMENA_PEER_ENABLED", "0").strip().lower() in _TRUTHY


def is_peer_halt_enabled() -> bool:
    """Kill-switch « panic » SOFT (`LUMENA_PEER_HALT`, défaut 0). Lu en live.

    Quand actif, il VETO toute NOUVELLE activité sortante/entrante : nouvelles
    délégations (in & out), découverte, conscience. C'est un veto absolu qui gagne
    sur le maître ET les flags unitaires.

    IMPORTANT (Lumena 24/7) : il NE coupe PAS les missions EN COURS. Il ne touche
    PAS la boucle d'autonomie (poll/health) → les missions déjà parties continuent
    et leurs résultats reviennent. On gate le FUTUR, jamais le PRÉSENT.
    """
    return os.getenv("LUMENA_PEER_HALT", "0").strip().lower() in _TRUTHY


def is_peer_network_autonomy_enabled() -> bool:
    # NOTE : volontairement PAS gardé par le halt — la boucle poll/health doit
    # continuer à récupérer les résultats des missions en cours (drain gracieux).
    return (
        is_peer_master_enabled()
        or os.getenv("LUMENA_PEER_NETWORK_AUTONOMY", "0").strip().lower() in _TRUTHY
    )
    return (
        is_peer_master_enabled()
        or os.getenv("LUMENA_PEER_NETWORK_AUTONOMY", "0").strip().lower() in _TRUTHY
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_peers() -> Dict[str, dict]:
    try:
        if _PEER_REGISTRY_FILE.exists():
            data = json.loads(_PEER_REGISTRY_FILE.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _save_peers(data: Dict[str, dict]) -> None:
    try:
        _PEER_REGISTRY_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PEER_REGISTRY_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(_PEER_REGISTRY_FILE)
    except Exception:
        pass


def _clamp_float(value: Any, default: float, low: float, high: float) -> float:
    try:
        return max(low, min(high, float(value)))
    except (TypeError, ValueError):
        return default


def _clamp_int(value: Any, default: int, low: int, high: int) -> int:
    try:
        return max(low, min(high, int(value)))
    except (TypeError, ValueError):
        return default


def _ports_from_env() -> Optional[List[int]]:
    raw = os.getenv("LUMENA_PEER_AUTOSCAN_PORTS", "").strip()
    if not raw:
        return None
    ports: List[int] = []
    for part in raw.split(","):
        try:
            port = int(part.strip())
        except ValueError:
            continue
        if 1024 <= port <= 65535 and port not in ports:
            ports.append(port)
    return ports[:20] or None


def _safe_peer_summary(peer: dict) -> dict:
    return {
        "instance_id": peer.get("instance_id", ""),
        "instance_name": peer.get("instance_name", ""),
        "host": peer.get("host", ""),
        "port": peer.get("port", 0),
        "trust": peer.get("trust", "unknown"),
        "last_seen": peer.get("last_seen", ""),
        "last_error": peer.get("last_error", ""),
    }


async def _probe_known_peers(timeout: float) -> List[dict]:
    from src.runtime.peer_discovery import probe_single_peer
    from src.runtime.peer_host_validation import validate_peer_host

    with _PEER_LOCK:
        peers = _load_peers()

    results: List[dict] = []

    async def _probe(instance_id: str, peer: dict) -> dict:
        host = str(peer.get("host") or "")
        port = int(peer.get("port") or 8080)
        trust = peer.get("trust", "unknown")
        result = {
            "instance_id": instance_id,
            "trust": trust,
            "reachable": False,
            "latency_ms": None,
            "last_error": "",
        }
        if trust == "blocked":
            result["last_error"] = "blocked"
            return result
        try:
            validate_peer_host(host)
            start = time.monotonic()
            hello = await probe_single_peer(host, port, timeout=timeout)
            result["latency_ms"] = int((time.monotonic() - start) * 1000)
            if hello and hello.get("instance_id") == instance_id:
                result["reachable"] = True
                return result
            result["last_error"] = "down_or_identity_mismatch"
            return result
        except Exception as exc:
            result["last_error"] = f"{type(exc).__name__}: {str(exc)[:120]}"
            return result

    tasks = [_probe(iid, peer) for iid, peer in peers.items()]
    if tasks:
        results = await asyncio.gather(*tasks, return_exceptions=False)

    now = _now_iso()
    with _PEER_LOCK:
        data = _load_peers()
        for item in results:
            iid = item["instance_id"]
            if iid not in data:
                continue
            data[iid]["autonomy_status"] = "reachable" if item["reachable"] else "down"
            data[iid]["last_health_at"] = now
            if item["reachable"]:
                data[iid]["last_seen"] = now
                data[iid]["last_latency_ms"] = item["latency_ms"]
                data[iid].pop("last_error", None)
            else:
                data[iid]["last_error"] = item["last_error"]
        _save_peers(data)

    return results


def _mdns_browse_available() -> bool:
    try:
        from src.runtime.mdns_discovery import is_mdns_available
        return is_mdns_available()
    except Exception:
        return False


async def _scan_for_new_peers(timeout: float, max_hosts: int) -> List[dict]:
    lan_on = is_peer_master_enabled() or os.getenv("LUMENA_PEER_DISCOVERY", "0").strip() == "1"
    mdns_on = _mdns_browse_available()
    if not lan_on and not mdns_on:
        return []

    found: List[dict] = []

    # ── Découverte par scan LAN (port-scan) ──────────────────────────────────
    if lan_on:
        from src.runtime.network_diagnostics import get_network_interfaces
        from src.runtime.peer_discovery import scan_lan_for_peers

        networks = [i.get("network") for i in get_network_interfaces() if i.get("network")]
        if not networks:
            networks = [None]
        ports = _ports_from_env()
        for network in networks[:4]:
            try:
                part = await asyncio.wait_for(
                    scan_lan_for_peers(
                        network=network,
                        ports=ports,
                        timeout=timeout,
                        max_hosts=max_hosts,
                    ),
                    timeout=30.0,  # couvrir un /24 complet (ex: pair en .57)
                )
                found.extend(part)
            except asyncio.TimeoutError:
                continue
            except Exception:
                continue

    # ── Découverte mDNS/Zeroconf (zéro-config) ───────────────────────────────
    if mdns_on:
        try:
            from src.runtime.mdns_discovery import browse_services
            from src.utils.paths import INSTANCE_ID as _own
            loop = asyncio.get_event_loop()
            mdns_found = await loop.run_in_executor(
                None, lambda: browse_services(timeout=3.0, self_instance_id=_own)
            )
            found.extend(mdns_found or [])
        except Exception:
            pass

    unique: Dict[str, dict] = {}
    for peer in found:
        iid = peer.get("instance_id")
        if iid:
            unique[iid] = peer

    now = _now_iso()
    with _PEER_LOCK:
        data = _load_peers()
        for iid, peer in unique.items():
            existing = data.get(iid)
            if not existing:
                data[iid] = {
                    **peer,
                    "trust": "unknown",
                    "discovered_at": now,
                    "last_seen": now,
                    "autonomy_status": "discovered",
                }
                continue
            if existing.get("trust") != "blocked":
                for key in ("instance_name", "host", "port", "version", "role", "capabilities"):
                    if peer.get(key):
                        existing[key] = peer[key]
                existing["last_seen"] = now
                existing["autonomy_status"] = "discovered"
        _save_peers(data)

    return list(unique.values())


def _build_summary(discovered_count: int = 0) -> Dict[str, Any]:
    peers = _load_peers()
    values = list(peers.values())
    trusted = [p for p in values if p.get("trust") == "trusted"]
    unknown = [p for p in values if p.get("trust", "unknown") == "unknown"]
    blocked = [p for p in values if p.get("trust") == "blocked"]
    reachable_trusted = [
        p for p in trusted
        if p.get("autonomy_status") == "reachable" or (p.get("last_seen") and not p.get("last_error"))
    ]
    down_trusted = [
        _safe_peer_summary(p) for p in trusted
        if p.get("autonomy_status") == "down" or p.get("last_error")
    ]
    needs_pairing = [_safe_peer_summary(p) for p in unknown[:10]]
    return {
        "known": len(values),
        "trusted": len(trusted),
        "reachable_trusted": len(reachable_trusted),
        "unknown": len(unknown),
        "blocked": len(blocked),
        "discovered": discovered_count,
        "needs_pairing": needs_pairing,
        "down_trusted": down_trusted[:10],
    }


# ── A1.5 — Auto-jumelage de flotte (confiance uniquement, gated par la clé) ──

_MAX_AUTOPAIR_PER_CYCLE = 5


def is_fleet_autopair_enabled() -> bool:
    """True si une clé de flotte est posée ET l'auto-jumelage non désactivé."""
    try:
        from src.runtime.peer_fleet import is_fleet_pairing_enabled
    except Exception:
        return False
    if not is_fleet_pairing_enabled():
        return False
    return os.getenv("LUMENA_FLEET_AUTOPAIR", "1").strip().lower() not in {"0", "false", "no", "off"}


def _autopair_cooldown() -> int:
    return _clamp_int(os.getenv("LUMENA_FLEET_AUTOPAIR_COOLDOWN_SEC", "600"), 600, 30, 86400)


def _audit_autopair(event: str, instance_id: str, status: str, detail: str = "") -> None:
    try:
        from src.runtime.peer_protocol import write_audit_log
        write_audit_log(event=event, from_instance_id=instance_id, task_id="fleet-autopair",
                        scope="pairing", status=status, detail=detail)
    except Exception:
        pass


def _select_autopair_candidates(peers: Dict[str, dict], now: float, cooldown: int) -> List[tuple]:
    """Pairs à auto-jumeler : unknown + host connu + hors cooldown. Pur (testable)."""
    out: List[tuple] = []
    for iid, peer in peers.items():
        if not isinstance(peer, dict):
            continue
        if peer.get("trust", "unknown") != "unknown":
            continue  # déjà trusted/blocked → on ne touche pas
        host = str(peer.get("host") or "").strip()
        if not host:
            continue
        last_fail = peer.get("fleet_autopair_failed_at")
        if isinstance(last_fail, (int, float)) and now < last_fail + cooldown:
            continue  # en cooldown après un échec récent
        out.append((iid, host, int(peer.get("port") or 8080)))
    return out[:_MAX_AUTOPAIR_PER_CYCLE]


async def _auto_pair_fleet_peers() -> int:
    """Tente l'auto-jumelage des pairs unknown de la flotte. Retourne le nb réussi.

    Sûr : verrouillé par la preuve de clé de flotte (un pair sans la clé échoue
    et passe en cooldown). N'exécute aucun code distant — établit la confiance.
    """
    import time as _time
    if not is_fleet_autopair_enabled():
        return 0
    from src.runtime.peer_discovery import attempt_fleet_pair

    with _PEER_LOCK:
        peers = _load_peers()
    now = _time.time()
    candidates = _select_autopair_candidates(peers, now, _autopair_cooldown())

    paired = 0
    for iid, host, port in candidates:
        try:
            result = await attempt_fleet_pair(host, port)
        except Exception as exc:  # pragma: no cover - best effort
            result = {"ok": False, "error": f"exc:{type(exc).__name__}"}
        if result.get("ok"):
            paired += 1
            _audit_autopair("fleet_autopair_completed", iid, "completed")
        else:
            # Marquer un cooldown sur échec (évite de marteler un pair non-flotte).
            with _PEER_LOCK:
                data = _load_peers()
                if iid in data and data[iid].get("trust", "unknown") == "unknown":
                    data[iid]["fleet_autopair_failed_at"] = now
                    data[iid]["fleet_autopair_last_error"] = str(result.get("error", ""))[:120]
                    _save_peers(data)
            _audit_autopair("fleet_autopair_failed", iid, "error", str(result.get("error", "")))
    return paired


async def run_peer_network_autonomy_once(*, scan: bool = True, health: bool = True) -> Dict[str, Any]:
    """Run one bounded maintenance pass and return a compact summary."""
    timeout = _clamp_float(os.getenv("LUMENA_PEER_AUTONOMY_TIMEOUT", "1.5"), 1.5, 0.2, 5.0)
    max_hosts = _clamp_int(os.getenv("LUMENA_PEER_AUTOSCAN_MAX_HOSTS", "254"), 254, 1, 254)
    discovered: List[dict] = []

    try:
        if health:
            await _probe_known_peers(timeout=timeout)
        if scan:
            discovered = await _scan_for_new_peers(timeout=timeout, max_hosts=max_hosts)
        # A1.5 — auto-jumelage de flotte (après découverte). Confiance uniquement.
        autopaired = 0
        try:
            autopaired = await _auto_pair_fleet_peers()
        except Exception:
            autopaired = 0
        # M3 — suivi des missions sortantes : poll des statuts + notification.
        # Jamais bloquant ni fatal (coupure réseau tolérée → retry au cycle suivant).
        try:
            from src.runtime.peer_mission_tracker import poll_outbound_missions
            await poll_outbound_missions(timeout=timeout)
        except Exception:
            pass
        summary = _build_summary(discovered_count=len(discovered))
        summary["autopaired"] = autopaired
        now = _now_iso()
        with _STATE_LOCK:
            _STATE.update({
                "enabled": is_peer_network_autonomy_enabled(),
                "running": _TASK is not None and not _TASK.done(),
                "last_run_at": now,
                "last_error": "",
                "last_summary": summary,
            })
            if health:
                _STATE["last_health_at"] = now
            if scan:
                _STATE["last_scan_at"] = now
        return get_peer_network_autonomy_status()
    except Exception as exc:
        with _STATE_LOCK:
            _STATE["last_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
        return get_peer_network_autonomy_status()


async def _poll_pending_missions_tick(*, timeout: float = 5.0) -> bool:
    """Tick léger : rapatrie le statut des missions sortantes EN ATTENTE.

    Retourne True si un poll a été lancé (au moins une mission en attente). Extrait
    en fonction pour rester testable. Jamais bloquant ni fatal.
    """
    try:
        from src.runtime.peer_mission_tracker import poll_outbound_missions, list_pending
        if not list_pending():
            return False
        await poll_outbound_missions(timeout=timeout)
        return True
    except Exception:
        return False


async def _autonomy_loop() -> None:
    health_interval = _clamp_int(os.getenv("LUMENA_PEER_AUTOHEALTH_INTERVAL_SEC", "60"), 60, 15, 3600)
    scan_interval = _clamp_int(os.getenv("LUMENA_PEER_AUTOSCAN_INTERVAL_SEC", "300"), 300, 60, 86400)
    # Poll RAPIDE des missions sortantes : tant qu'il y en a en attente, on rapatrie
    # leur statut toutes les ~8 s (au lieu d'attendre le cycle santé 60 s) → le verdict
    # du pair (refused / completed) revient quasi tout de suite à l'émetteur.
    mission_interval = _clamp_int(os.getenv("LUMENA_PEER_MISSION_POLL_SEC", "8"), 8, 3, 120)
    last_scan = 0.0
    last_health = 0.0
    while True:
        now = time.monotonic()
        if (now - last_health) >= health_interval:
            # Cycle complet : santé + scan (+ poll des missions inclus dans `once`).
            do_scan = (now - last_scan) >= scan_interval
            await run_peer_network_autonomy_once(scan=do_scan, health=True)
            last_health = now
            if do_scan:
                last_scan = now
        else:
            # Tick léger entre deux cycles complets : juste le poll des missions.
            await _poll_pending_missions_tick()
        await asyncio.sleep(mission_interval)


def start_peer_network_autonomy() -> Optional[asyncio.Task]:
    """Start the background service if enabled. Idempotent."""
    global _TASK
    with _STATE_LOCK:
        _STATE["enabled"] = is_peer_network_autonomy_enabled()
    if not is_peer_network_autonomy_enabled():
        return None
    if _TASK is not None and not _TASK.done():
        return _TASK
    _TASK = asyncio.create_task(_autonomy_loop())
    with _STATE_LOCK:
        _STATE["running"] = True
    return _TASK


async def stop_peer_network_autonomy() -> None:
    global _TASK
    task = _TASK
    _TASK = None
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    with _STATE_LOCK:
        _STATE["running"] = False


def get_peer_network_autonomy_status() -> Dict[str, Any]:
    with _STATE_LOCK:
        state = json.loads(json.dumps(_STATE))
    state["enabled"] = is_peer_network_autonomy_enabled()
    state["running"] = _TASK is not None and not _TASK.done()
    if not state.get("last_summary"):
        state["last_summary"] = _build_summary()
    return state


def build_peer_network_context() -> str:
    """Short chat-facing status. No secrets, no raw registry dump."""
    if not is_peer_network_autonomy_enabled():
        return ""
    status = get_peer_network_autonomy_status()
    summary = status.get("last_summary") or {}
    lines = [
        "## Autonomie reseau Lumena",
        f"- Etat: {'active' if status.get('running') else 'inactive'}; derniers checks: {status.get('last_run_at') or 'jamais'}.",
        (
            f"- Pairs connus: {summary.get('known', 0)}; trusted joignables: "
            f"{summary.get('reachable_trusted', 0)}/{summary.get('trusted', 0)}; "
            f"a jumeler: {summary.get('unknown', 0)}."
        ),
    ]
    if summary.get("needs_pairing"):
        first = summary["needs_pairing"][0]
        lines.append(
            f"- Pair detecte non jumele: {first.get('instance_name') or first.get('instance_id')} "
            f"({first.get('host')}:{first.get('port')}). Proposer le jumelage par code."
        )
    if summary.get("down_trusted"):
        first = summary["down_trusted"][0]
        lines.append(
            f"- Pair trusted potentiellement down: {first.get('instance_name') or first.get('instance_id')} "
            f"({first.get('host')}:{first.get('port')}). Proposer diagnostic reseau."
        )
    if status.get("last_error"):
        lines.append(f"- Derniere erreur autonomie: {status['last_error']}")
    return "\n".join(lines)
