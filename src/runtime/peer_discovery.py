"""Phase 5 — Découverte LAN d'instances Lumena.

Feature flag : LUMENA_PEER_DISCOVERY=1 (défaut : 0 — désactivé).

Approche MVP : scan des ports connus + GET /api/instance/hello.
Sépare volontairement 'trouver des machines' (network_scan) de 'trouver des
instances Lumena' (ce module).
"""
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from typing import Dict, List, Optional

import httpx

PEER_DISCOVERY_ENABLED: bool = os.getenv("LUMENA_PEER_DISCOVERY", "0").strip() == "1"


def is_peer_discovery_enabled() -> bool:
    """Découverte LAN active : flag unitaire (boot) OU interrupteur MAÎTRE.

    Utilise la constante boot `PEER_DISCOVERY_ENABLED` (monkeypatchable, et
    cohérente avec « LUMENA_PEER_DISCOVERY nécessite redémarrage ») en OR avec
    le maître `LUMENA_PEER_ENABLED`. NB : la BOUCLE de scan démarre au boot — un
    passage à ON via l'UI prend pleinement effet au reboot.
    """
    # Kill-switch SOFT : le halt veto la découverte (plus de nouveaux scans).
    try:
        from src.runtime.peer_network_autonomy import is_peer_halt_enabled, is_peer_master_enabled
        if is_peer_halt_enabled():
            return False
        if PEER_DISCOVERY_ENABLED:
            return True
        return is_peer_master_enabled()
    except Exception:
        return PEER_DISCOVERY_ENABLED

# Ports Lumena connus à sonder (ordre de priorité)
DEFAULT_LUMENA_PORTS: List[int] = [8080, 8081, 8082, 8083, 8084, 8085, 8086, 8087, 8088, 8089]


def _get_local_network() -> Optional[str]:
    """Détecte le réseau local de cette machine (ex: '192.168.1.0/24')."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        local_ip = s.getsockname()[0]
        s.close()
        net = ipaddress.ip_network(f"{local_ip}/24", strict=False)
        return str(net)
    except Exception:
        return None


_MAX_CONCURRENT_PROBES = 256  # /24 × 10 ports = 2540 sondes → couvrir tout vite


async def _probe_hello(
    host: str,
    port: int,
    own_instance_id: str,
    client: httpx.AsyncClient,
) -> Optional[dict]:
    """Appelle GET /api/instance/hello sur host:port via un client partagé."""
    try:
        url = f"http://{host}:{port}/api/instance/hello"
        r = await client.get(url)
        if r.status_code != 200:
            return None
        data = r.json()
        if data.get("instance_id") == own_instance_id:
            return None
        return {
            "instance_id": data.get("instance_id", ""),
            "instance_name": data.get("instance_name", ""),
            "host": host,
            "port": port,
            "version": data.get("version", ""),
            "role": data.get("role", "standalone"),
            "capabilities": data.get("capabilities", []),
            "requires_pairing": data.get("requires_pairing", True),
            "trust": "unknown",
        }
    except Exception:
        return None


async def scan_lan_for_peers(
    network: Optional[str] = None,
    ports: Optional[List[int]] = None,
    timeout: float = 1.5,
    max_hosts: int = 254,
) -> List[dict]:
    """Scanne le LAN pour trouver des instances Lumena.

    Retourne la liste des instances découvertes (sans modifier le registre).
    Désactivé si LUMENA_PEER_DISCOVERY=0.
    Concurrence limitée à _MAX_CONCURRENT_PROBES via Semaphore.
    Client httpx partagé sur toute la durée du scan.
    """
    if not is_peer_discovery_enabled():
        return []

    from src.utils.paths import INSTANCE_ID as _OWN_ID
    own_id = _OWN_ID

    target_network = network or _get_local_network()
    if not target_network:
        return []

    target_ports = ports or DEFAULT_LUMENA_PORTS

    try:
        net = ipaddress.ip_network(target_network, strict=False)
        if not (net.is_private or net.is_loopback):
            return []
        hosts = [str(h) for h in list(net.hosts())[:max_hosts]]
    except ValueError:
        return []

    semaphore = asyncio.Semaphore(_MAX_CONCURRENT_PROBES)

    async def bounded_probe(h: str, p: int, client: httpx.AsyncClient) -> Optional[dict]:
        async with semaphore:
            return await _probe_hello(h, p, own_id, client)

    _limits = httpx.Limits(max_connections=_MAX_CONCURRENT_PROBES + 32,
                           max_keepalive_connections=0)
    async with httpx.AsyncClient(timeout=timeout, limits=_limits) as client:
        tasks = [bounded_probe(host, port, client) for host in hosts for port in target_ports]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    return [r for r in results if isinstance(r, dict) and r.get("instance_id")]


async def probe_single_peer(host: str, port: int, timeout: float = 3.0) -> Optional[dict]:
    """Sonde une adresse précise pour vérifier la présence d'une instance Lumena."""
    from src.utils.paths import INSTANCE_ID as _OWN_ID
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await _probe_hello(host, port, _OWN_ID, client)


def _own_capabilities() -> List[str]:
    """Capacités déclarées minimales pour le handshake (best-effort, sans secret)."""
    caps = ["chat"]
    extra = os.getenv("LUMENA_EXTRA_CAPABILITIES", "").strip()
    if extra:
        caps.extend(c.strip() for c in extra.split(",") if c.strip())
    return caps


def _own_lan_host() -> str:
    try:
        from src.runtime.network_diagnostics import get_local_lan_ips
        ips = get_local_lan_ips()
        return ips[0] if ips else "0.0.0.0"
    except Exception:
        return "0.0.0.0"


async def attempt_fleet_pair(host: str, port: int, timeout: float = 8.0) -> dict:
    """A1 — Initiateur : auto-jumelage par **preuve de flotte** (sans code humain).

    Exécute le handshake HMAC mutuel avec le pair `host:port`, puis enregistre
    le pair comme `trusted` (côté local) avec des **tokens dérivés** (jamais
    transmis). Retourne un dict `{ok, ...}`. Réutilisable par l'autonomie (C1).

    Sûr par construction : si la clé de flotte est absente ou la preuve du pair
    invalide (pas la même flotte), aucune confiance n'est établie.
    """
    from datetime import datetime, timezone

    from src.runtime.peer_fleet import (
        is_fleet_pairing_enabled, get_fleet_key, generate_nonce,
        compute_proof, verify_proof, derive_peer_token,
    )
    from src.runtime.peer_host_validation import validate_peer_host
    from src.runtime.peer_tokens import hash_peer_token
    from src.runtime.peer_network_autonomy import _load_peers, _save_peers, _PEER_LOCK
    from src.utils.paths import INSTANCE_ID as own_id, INSTANCE_NAME as own_name

    if not is_fleet_pairing_enabled():
        return {"ok": False, "error": "fleet_key_absent"}
    try:
        validate_peer_host(host)
    except ValueError as exc:
        return {"ok": False, "error": f"ssrf:{exc}"}

    # Exclusion par IP propre : ne JAMAIS se jumeler avec soi-même (robuste même
    # si l'instance_id est instable). Évite les doublons "self-pairing".
    try:
        from src.runtime.network_diagnostics import get_local_lan_ips
        if host.strip() in set(get_local_lan_ips() or []):
            return {"ok": False, "error": "self_ip"}
    except Exception:
        pass

    nonce_init = generate_nonce()
    init_payload = {
        "from_instance_id": own_id,
        "from_instance_name": own_name,
        "from_host": _own_lan_host(),
        "from_port": int(os.getenv("LUMENA_PORT", "8080")),
        "from_capabilities": _own_capabilities(),
        "nonce_init": nonce_init,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            r1 = await client.post(f"http://{host}:{port}/api/peer/fleet-pair-init", json=init_payload)
            if r1.status_code != 200:
                return {"ok": False, "error": f"init_http_{r1.status_code}"}
            d1 = r1.json()
            peer_id = str(d1.get("instance_id", ""))
            nonce_resp = str(d1.get("nonce_resp", ""))
            proof_b = str(d1.get("proof_b", ""))
            if not peer_id or peer_id == own_id:
                return {"ok": False, "error": "self_or_no_peer"}
            # Vérifier la preuve de B (prouveur=peer_id, autre=nous)
            if not verify_proof(proof_b, peer_id, own_id, nonce_init, nonce_resp):
                return {"ok": False, "error": "peer_proof_invalid"}
            # Notre preuve (prouveur=nous)
            proof_a = compute_proof(own_id, peer_id, nonce_init, nonce_resp)
            r2 = await client.post(
                f"http://{host}:{port}/api/peer/fleet-pair-confirm",
                json={
                    "from_instance_id": own_id,
                    "nonce_init": nonce_init,
                    "nonce_resp": nonce_resp,
                    "proof_a": proof_a,
                },
            )
            if r2.status_code != 200:
                return {"ok": False, "error": f"confirm_http_{r2.status_code}"}
            d2 = r2.json()
    except Exception as exc:
        return {"ok": False, "error": f"net:{type(exc).__name__}"}

    # Enregistrer B comme trusted (côté A), tokens dérivés (jamais transmis).
    fk = get_fleet_key()
    now = datetime.now(timezone.utc).isoformat()
    peer_name = str(d2.get("instance_name") or d1.get("instance_name") or peer_id[:12])
    with _PEER_LOCK:
        data = _load_peers()
        data[peer_id] = {
            **data.get(peer_id, {}),
            "instance_id": peer_id,
            "instance_name": peer_name,
            "host": host,
            "port": port,
            "capabilities": d2.get("capabilities") or d1.get("capabilities") or [],
            "trust": "trusted",
            "pairing_method": "fleet",
            "paired_at": now,
            "last_seen": now,
            # hash du token que B présentera (B→A) → valide les appels entrants de B
            "peer_token_hash": hash_peer_token(derive_peer_token(peer_id, own_id, fleet_key=fk)),
            # token que nous présentons à B (A→B)
            "peer_token_outbound": derive_peer_token(own_id, peer_id, fleet_key=fk),
            "allowed_scopes": ["chat"],
        }
        _save_peers(data)

    return {"ok": True, "instance_id": peer_id, "instance_name": peer_name, "host": host, "port": port}
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
