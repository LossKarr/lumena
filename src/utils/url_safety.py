"""
SSRF Guard — Validation centralisée des URLs pour Lumena.

Empêche les requêtes vers des réseaux privés, IPs réservées,
metadata cloud (169.254.169.254), et schemes dangereux.

Extrait de src/tools/playwright_browser.py pour réutilisation
dans web_service.py, web_crawler.py, computer_use.py, etc.
"""

import ipaddress
import socket
from urllib.parse import urlparse

_BLOCKED_SCHEMES = frozenset({"file", "ftp", "gopher", "data", "javascript", "vbscript", ""})
_SSRF_ALLOW_PRIVATE = False  # True uniquement en debug local
# Ports localhost autorisés pour les serveurs web self-hosted (dev previews).
# LOT E (run CéramiShop) : 8080 RETIRÉ — c'est le port de contrôle de Lumena.
# L'agent ne doit jamais atteindre l'UI/l'API de Lumena via son navigateur ; les
# vraies previews (start_preview_server, http.server de mission) montent sur un
# port libre ≥ 8081 qu'elles ENREGISTRENT (is_preview_allowed), pas via 8080.
_SSRF_ALLOWED_LOCAL_PORTS: frozenset = frozenset({3000, 4200, 5000, 8000, 8888, 9000})


def _is_private_host(hostname: str) -> bool:
    """Vérifie si un hostname résout vers une IP privée/réservée."""
    if not hostname:
        return True
    lower = hostname.lower().strip(".")
    if lower in ("localhost", "localhost.localdomain"):
        return True
    try:
        addr_info = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in addr_info:
            ip_str = sockaddr[0]
            ip = ipaddress.ip_address(ip_str)
            if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
                return True
    except (socket.gaierror, ValueError, OSError):
        return False
    return False


def assert_url_safe(url: str) -> None:
    """Lève ValueError si l'URL cible un réseau privé ou un scheme interdit.

    Vérifie le scheme, le hostname et la résolution DNS (anti DNS-rebinding).
    """
    if _SSRF_ALLOW_PRIVATE:
        return
    try:
        parsed = urlparse(url)
    except Exception:
        raise ValueError(f"URL invalide: {url!r}")

    scheme = (parsed.scheme or "").lower()
    if scheme in _BLOCKED_SCHEMES:
        raise ValueError(f"Scheme interdit: {scheme}://")
    if scheme not in ("http", "https"):
        raise ValueError(f"Scheme non supporté: {scheme}://")

    hostname = parsed.hostname or ""
    if not hostname:
        raise ValueError("URL sans hostname")

    # Ports localhost autorisés pour serveurs self-hosted Lumena
    if hostname in ("localhost", "127.0.0.1", "::1"):
        port = parsed.port or (443 if scheme == "https" else 80)
        # LOT E : les ports de CONTRÔLE de Lumena (web 8080, IDE 8245, Ollama) sont
        # interdits AVANT tout — ni l'allowlist ni une preview ne peut les rouvrir.
        from src.utils.local_preview import is_preview_allowed, reserved_lumena_ports
        if port in reserved_lumena_ports():
            raise ValueError(f"Accès réseau privé interdit: port réservé Lumena {port}")
        if port in _SSRF_ALLOWED_LOCAL_PORTS:
            return
        # Preview locale CONTRÔLÉE : port loopback enregistré par Lumena elle-même
        # (serve_website / http.server de mission). Autorise UNIQUEMENT les previews
        # délibérément servies — pas un allow-localhost large. Cf. run todolist.
        if is_preview_allowed(hostname, port):
            return

    # Check IP littérale directe
    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_reserved or ip.is_link_local:
            raise ValueError(f"Accès réseau privé interdit: {hostname}")
    except ValueError as ve:
        if "interdit" in str(ve):
            raise
        if _is_private_host(hostname):
            raise ValueError(f"Hostname résout vers IP privée: {hostname}")
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
