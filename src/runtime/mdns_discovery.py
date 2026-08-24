"""Phase 8.11 — Découverte mDNS/Zeroconf optionnelle.

Feature flag : LUMENA_MDNS_DISCOVERY=1  (défaut : 0 = désactivé).
Dépendance : python-zeroconf — optionnelle.
Si absente ou flag=0, toutes les fonctions retournent des valeurs vides/None
et Lumena continue de fonctionner normalement.

Règles de sécurité :
- TXT records autorisés : instance_id, instance_name, role, version, caps_hash, port.
- Aucun secret (token, hash de token, clé) ne sort dans les TXT records.
- Les instances découvertes reçoivent trust="unknown" — le pairing reste obligatoire.
- Auto-exclusion : on ignore notre propre annonce lors du browse.
"""
from __future__ import annotations

import hashlib
import os
import socket
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

SERVICE_TYPE = "_lumena._tcp.local."
SERVICE_NAME_PREFIX = "lumena-"

# Seuls ces champs sont autorisés dans les TXT records (pas de secrets)
_TXT_ALLOWED = frozenset({"instance_id", "instance_name", "role", "version", "caps_hash", "port"})


def is_mdns_available() -> bool:
    """True si python-zeroconf est installé ET le feature flag activé."""
    if os.getenv("LUMENA_MDNS_DISCOVERY", "0").strip() != "1":
        return False
    try:
        import zeroconf  # noqa: F401
        return True
    except ImportError:
        return False


def is_mdns_enabled() -> bool:
    """True si le feature flag est activé (indépendamment de la lib)."""
    return os.getenv("LUMENA_MDNS_DISCOVERY", "0").strip() == "1"


def _caps_hash(caps: List[str]) -> str:
    """Hash compact des capabilities — non-secret, reproductible quel que soit l'ordre."""
    payload = ",".join(sorted(caps))
    return hashlib.sha256(payload.encode()).hexdigest()[:8]


def build_txt_records(
    instance_id: str,
    instance_name: str,
    role: str,
    version: str,
    capabilities: List[str],
    port: int,
) -> Dict[str, bytes]:
    """Construit les TXT records mDNS — aucun secret n'est inclus."""
    return {
        "instance_id": instance_id.encode(),
        "instance_name": instance_name.encode(),
        "role": role.encode(),
        "version": version.encode(),
        "caps_hash": _caps_hash(capabilities).encode(),
        "port": str(port).encode(),
    }


def parse_service_info(
    info: Any,
    self_instance_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Parse un ServiceInfo zeroconf en entrée pair.

    Retourne None si l'entrée doit être ignorée (auto-exclusion ou invalide).
    Cette fonction est pure et testable sans dépendance zeroconf.
    """
    try:
        props = {
            (k.decode() if isinstance(k, bytes) else k): (
                v.decode() if isinstance(v, bytes) else str(v)
            )
            for k, v in (getattr(info, "properties", None) or {}).items()
        }
        iid = props.get("instance_id", "")
        if self_instance_id and iid == self_instance_id:
            return None  # auto-exclusion

        # Filtrage strict : seulement les champs TXT autorisés
        safe = {k: v for k, v in props.items() if k in _TXT_ALLOWED}

        host = ""
        addresses = getattr(info, "addresses", None)
        if addresses:
            try:
                host = socket.inet_ntoa(addresses[0])
            except Exception:
                pass

        return {
            "instance_id": iid or str(info),
            "instance_name": safe.get("instance_name", ""),
            "host": host,
            "port": getattr(info, "port", 8080),
            "role": safe.get("role", "unknown"),
            "version": safe.get("version", ""),
            "caps_hash": safe.get("caps_hash", ""),
            "trust": "unknown",
            "source": "mdns",
        }
    except Exception:
        return None


def _resolve_local_ip() -> str:
    """Détermine l'IP locale sortante (sans envoyer de paquet)."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def advertise_service(
    instance_id: str,
    instance_name: str,
    role: str,
    version: str,
    capabilities: List[str],
    port: int,
    host_ip: Optional[str] = None,
) -> Optional[Tuple[Any, Any]]:
    """Annonce le service _lumena._tcp.local. sur le LAN.

    Retourne (Zeroconf, ServiceInfo) pour pouvoir l'arrêter via stop_service(),
    ou None si mDNS est indisponible/désactivé.
    """
    if not is_mdns_available():
        return None
    try:
        from zeroconf import ServiceInfo, Zeroconf

        ip = host_ip or _resolve_local_ip()
        txt = build_txt_records(instance_id, instance_name, role, version, capabilities, port)
        service_name = f"{SERVICE_NAME_PREFIX}{instance_id}.{SERVICE_TYPE}"

        # Nom d'hôte mDNS sûr : seuls [a-z0-9-] (les UUID/IDs avec d'autres
        # caractères font échouer register_service sur les zeroconf récents).
        safe_host = "".join(c if (c.isalnum() or c == "-") else "-" for c in str(instance_id).lower())[:40] or "lumena"
        info = ServiceInfo(
            SERVICE_TYPE,
            service_name,
            addresses=[socket.inet_aton(ip)],
            port=port,
            properties=txt,
            server=f"{safe_host}.local.",
        )
        zc = Zeroconf()
        # allow_name_change : évite NonUniqueNameException si déjà annoncé.
        zc.register_service(info, allow_name_change=True)
        return (zc, info)
    except Exception as exc:
        try:
            from loguru import logger as _lg
            _lg.warning("[mDNS] advertise_service échoué ({}): {}", type(exc).__name__, str(exc)[:200])
        except Exception:
            pass
        return None


def browse_services(
    timeout: float = 5.0,
    self_instance_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Découvre les services _lumena._tcp.local. sur le LAN.

    - Exclut self_instance_id (auto-exclusion via parse_service_info).
    - Retourne uniquement les champs TXT autorisés + host + port.
    - trust="unknown", source="mdns" — le pairing reste obligatoire.
    - Retourne [] si mDNS est indisponible/désactivé.
    """
    if not is_mdns_available():
        return []
    try:
        from zeroconf import ServiceBrowser, Zeroconf

        found: List[Dict[str, Any]] = []
        lock = threading.Lock()

        # Duck-typed listener — pas d'héritage de ServiceListener pour rester mockable
        class _Listener:
            def add_service(self, zc: Any, type_: str, name: str) -> None:
                try:
                    info = zc.get_service_info(type_, name)
                    if info is None:
                        return
                    entry = parse_service_info(info, self_instance_id)
                    if entry is None:
                        return
                    with lock:
                        if not any(e["instance_id"] == entry["instance_id"] for e in found):
                            found.append(entry)
                except Exception:
                    pass

            def remove_service(self, zc: Any, type_: str, name: str) -> None:
                pass

            def update_service(self, zc: Any, type_: str, name: str) -> None:
                pass

        zc = Zeroconf()
        listener = _Listener()
        ServiceBrowser(zc, SERVICE_TYPE, listener)
        time.sleep(timeout)
        zc.close()
        return found
    except Exception:
        return []


def stop_service(handle: Optional[Tuple[Any, Any]]) -> None:
    """Arrête le service annoncé. handle = valeur retournée par advertise_service()."""
    if handle is None:
        return
    try:
        zc, info = handle
        zc.unregister_service(info)
        zc.close()
    except Exception:
        pass


# ── A1.5 — Annonce mDNS automatique au boot (singleton géré ici) ─────────────

_ADVERTISE_HANDLE: Optional[Tuple[Any, Any]] = None


def start_mdns_advertise_from_env() -> bool:
    """Annonce cette instance sur le LAN (`_lumena._tcp.local`) à partir de l'env.

    Idempotent. Retourne True si l'annonce est active. No-op si mDNS indisponible
    (flag off ou zeroconf absent) → Lumena fonctionne normalement.
    """
    global _ADVERTISE_HANDLE
    if not is_mdns_available() or _ADVERTISE_HANDLE is not None:
        return _ADVERTISE_HANDLE is not None
    try:
        from src import __version__
        from src.utils.paths import INSTANCE_ID, INSTANCE_NAME
        role = os.getenv("LUMENA_INSTANCE_ROLE", "standalone")
        version = os.getenv("LUMENA_VERSION", "") or __version__
        port = int(os.getenv("LUMENA_PORT", "8080"))
        caps = ["chat"]
        extra = os.getenv("LUMENA_EXTRA_CAPABILITIES", "").strip()
        if extra:
            caps.extend(c.strip() for c in extra.split(",") if c.strip())
        handle = advertise_service(INSTANCE_ID, INSTANCE_NAME, role, version, caps, port)
        if handle is not None:
            _ADVERTISE_HANDLE = handle
            return True
    except Exception:
        pass
    return False


def stop_mdns_advertise() -> None:
    """Arrête l'annonce mDNS de cette instance (singleton)."""
    global _ADVERTISE_HANDLE
    if _ADVERTISE_HANDLE is not None:
        stop_service(_ADVERTISE_HANDLE)
        _ADVERTISE_HANDLE = None
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
