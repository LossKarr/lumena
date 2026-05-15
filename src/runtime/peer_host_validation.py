"""Anti-SSRF host validation pour les pairs Lumena.

Module partagé entre web/routes/peers.py (Phase 8) et
src/reasoning/handlers/peer_delegation.py (Phase 10 Lot B).

Whitelist explicite : RFC1918 uniquement (10/8, 172.16/12, 192.168/16).
Refuse : loopback, link-local, CGNAT, multicast, IPs publiques, noms de domaine.
Soulève ValueError (pas HTTPException) pour rester indépendant de FastAPI.
"""
from __future__ import annotations

import ipaddress

_RFC1918 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def validate_peer_host(host: str) -> None:
    """Vérifie que le host est une IP privée strictement RFC1918.

    Raises ValueError si invalide (loopback, link-local, publique, domaine, vide…).
    """
    if not host or not host.strip():
        raise ValueError(
            "Host vide — seules les IPs privées RFC1918 sont acceptées."
        )
    try:
        ip = ipaddress.ip_address(host.strip())
    except ValueError:
        raise ValueError(
            f"Host {host!r} invalide — seules les IPs privées RFC1918 sont acceptées "
            "(pas de noms de domaine, pas de résolution DNS)."
        )
    if not any(ip in net for net in _RFC1918):
        raise ValueError(
            f"IP {host!r} non autorisée — uniquement les plages RFC1918 strictes "
            "(10.0.0.0/8, 172.16.0.0/12, 192.168.0.0/16)."
        )
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
