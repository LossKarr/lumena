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


_MAX_CONCURRENT_PROBES = 50


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
    if not PEER_DISCOVERY_ENABLED:
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

    async with httpx.AsyncClient(timeout=timeout) as client:
        tasks = [bounded_probe(host, port, client) for host in hosts for port in target_ports]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    return [r for r in results if isinstance(r, dict) and r.get("instance_id")]


async def probe_single_peer(host: str, port: int, timeout: float = 3.0) -> Optional[dict]:
    """Sonde une adresse précise pour vérifier la présence d'une instance Lumena."""
    from src.utils.paths import INSTANCE_ID as _OWN_ID
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await _probe_hello(host, port, _OWN_ID, client)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
