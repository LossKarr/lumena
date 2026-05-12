"""Phase 8.1 — Diagnostic réseau automatique.

Permet à l'instance de savoir si elle est joignable sur le LAN avant
de demander à l'utilisateur de scanner ou jumeler.
"""
from __future__ import annotations

import ipaddress
import os
import platform
import socket
import subprocess
from typing import Any, Dict, List


def get_local_lan_ips() -> List[str]:
    """Retourne toutes les IPv4 privées actives sur cette machine."""
    seen: set[str] = set()
    ips: List[str] = []

    # Méthode 1 : hostname → getaddrinfo (marche bien sur Windows)
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if _is_private_lan(ip) and ip not in seen:
                seen.add(ip)
                ips.append(ip)
    except Exception:
        pass

    # Méthode 2 : UDP trick (source IP vers 8.8.8.8 sans envoyer de paquet)
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if _is_private_lan(ip) and ip not in seen:
            seen.add(ip)
            ips.append(ip)
    except Exception:
        pass

    return ips


def _is_private_lan(ip: str) -> bool:
    """Vrai si IP privée RFC1918 (pas loopback, pas link-local)."""
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_private and not addr.is_loopback and not addr.is_link_local
    except Exception:
        return False


def check_port_listening(port: int) -> Dict[str, Any]:
    """Vérifie si le port est en écoute localement (connexion TCP 127.0.0.1)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            result = s.connect_ex(("127.0.0.1", port))
            return {"listening": result == 0, "port": port}
    except Exception as exc:
        return {"listening": False, "port": port, "error": str(exc)}


def check_bind_host() -> Dict[str, Any]:
    """Retourne l'adresse de bind et si elle est accessible depuis le réseau."""
    host = os.getenv("LUMENA_HOST", "0.0.0.0").strip()
    if host in ("0.0.0.0", ""):
        return {"bind": "0.0.0.0", "network_accessible": True}
    if host in ("::", "0:0:0:0:0:0:0:0"):
        return {"bind": host, "network_accessible": True}
    try:
        addr = ipaddress.ip_address(host)
        if addr.is_loopback:
            return {"bind": host, "network_accessible": False}
    except Exception:
        pass
    return {"bind": host, "network_accessible": True}


def check_firewall_hint(port: int) -> Dict[str, Any]:
    """Heuristique pare-feu Windows via netsh (non-bloquant, 3s timeout)."""
    system = platform.system()
    if system != "Windows":
        return {"platform": system, "firewall_check": "not_applicable"}
    try:
        result = subprocess.run(
            [
                "netsh", "advfirewall", "firewall", "show", "rule",
                f"localport={port}", "protocol=TCP", "dir=in",
            ],
            capture_output=True, text=True, timeout=3,
        )
        no_rule = "No rules match" in result.stdout or result.returncode != 0
        return {
            "platform": "Windows",
            "firewall_rule_found": not no_rule,
            "firewall_check": "rule_exists" if not no_rule else "possible_block",
        }
    except Exception:
        return {"platform": "Windows", "firewall_check": "unknown"}


def get_network_interfaces() -> List[Dict[str, Any]]:
    """Liste les sous-réseaux LAN disponibles pour le scan multi-réseau (Phase 8.10).

    Sans dépendance externe : dérive le /24 depuis chaque IP LAN locale.
    Suffisant pour les réseaux domestiques et de bureau (LAN typique = /24).
    Retourne des entrées dédupliquées par sous-réseau.
    """
    ips = get_local_lan_ips()
    seen: set[str] = set()
    results: List[Dict[str, Any]] = []
    for ip in ips:
        try:
            network = str(ipaddress.IPv4Network(f"{ip}/24", strict=False))
            if network not in seen:
                seen.add(network)
                results.append({
                    "ip": ip,
                    "network": network,
                    "prefix_len": 24,
                    "label": f"{ip}  →  {network}",
                })
        except Exception:
            pass
    return results


def build_network_diagnostic() -> Dict[str, Any]:
    """Construit le diagnostic réseau complet pour cette instance."""
    from src.utils.paths import INSTANCE_ID

    port = int(os.getenv("LUMENA_PORT", "8080"))

    lan_ips = get_local_lan_ips()
    port_check = check_port_listening(port)
    bind_check = check_bind_host()
    fw_check = check_firewall_hint(port)

    issues: List[Dict[str, str]] = []

    if not port_check["listening"]:
        issues.append({
            "code": "port_not_listening",
            "severity": "error",
            "message": f"Lumena n'est pas détectée sur le port {port} en local. "
                       "Le serveur est peut-être en cours de démarrage.",
        })

    if not bind_check["network_accessible"]:
        issues.append({
            "code": "bind_localhost_only",
            "severity": "error",
            "message": f"Lumena écoute uniquement sur {bind_check['bind']} (localhost). "
                       "Les autres machines ne peuvent pas se connecter. "
                       "Changez LUMENA_HOST=0.0.0.0 et redémarrez.",
        })

    if not lan_ips:
        issues.append({
            "code": "no_lan_ip",
            "severity": "warning",
            "message": "Aucune IP LAN privée détectée. "
                       "Êtes-vous connecté à un réseau ?",
        })

    if fw_check.get("firewall_check") == "possible_block":
        issues.append({
            "code": "firewall_possible_block",
            "severity": "warning",
            "message": f"Lumena écoute sur le réseau, mais aucune règle pare-feu "
                       f"entrante n'a été trouvée pour le port {port} (TCP). "
                       "Windows Firewall bloque probablement les connexions entrantes.",
        })

    suggested_actions: List[str] = []
    if any(i["code"] == "bind_localhost_only" for i in issues):
        suggested_actions.append("set_lumena_host_0000")
    if any(i["code"] == "firewall_possible_block" for i in issues):
        suggested_actions.append("open_windows_firewall_port")

    ok = not any(i["severity"] == "error" for i in issues)

    return {
        "ok": ok,
        "instance_id": INSTANCE_ID,
        "host": bind_check["bind"],
        "port": port,
        "lan_ips": lan_ips,
        "listening": port_check["listening"],
        "network_accessible": bind_check["network_accessible"],
        "firewall_check": fw_check.get("firewall_check", "unknown"),
        "issues": issues,
        "suggested_actions": suggested_actions,
    }
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
