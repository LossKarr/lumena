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


def is_peer_network_autonomy_enabled() -> bool:
    return os.getenv("LUMENA_PEER_NETWORK_AUTONOMY", "0").strip().lower() in {"1", "true", "yes", "on"}


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


async def _scan_for_new_peers(timeout: float, max_hosts: int) -> List[dict]:
    if os.getenv("LUMENA_PEER_DISCOVERY", "0").strip() != "1":
        return []

    from src.runtime.network_diagnostics import get_network_interfaces
    from src.runtime.peer_discovery import scan_lan_for_peers

    networks = [i.get("network") for i in get_network_interfaces() if i.get("network")]
    if not networks:
        networks = [None]

    found: List[dict] = []
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
                timeout=12.0,
            )
            found.extend(part)
        except asyncio.TimeoutError:
            continue
        except Exception:
            continue

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
        summary = _build_summary(discovered_count=len(discovered))
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


async def _autonomy_loop() -> None:
    health_interval = _clamp_int(os.getenv("LUMENA_PEER_AUTOHEALTH_INTERVAL_SEC", "60"), 60, 15, 3600)
    scan_interval = _clamp_int(os.getenv("LUMENA_PEER_AUTOSCAN_INTERVAL_SEC", "300"), 300, 60, 86400)
    last_scan = 0.0
    while True:
        now = time.monotonic()
        do_scan = (now - last_scan) >= scan_interval
        await run_peer_network_autonomy_once(scan=do_scan, health=True)
        if do_scan:
            last_scan = now
        await asyncio.sleep(health_interval)


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
