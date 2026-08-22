"""Registry des PREVIEWS LOCALES contrôlées de Lumena.

Une *preview* = un serveur HTTP **loopback** que Lumena a DÉLIBÉRÉMENT lancé pour
vérifier un livrable web (via `serve_website`, ou un `python -m http.server` de
mission). Seuls ces ports loopback **enregistrés** deviennent atteignables par le
SSRF guard (`src/utils/url_safety.assert_url_safe`). Tout le reste — externe, IP
LAN (192.168.x.x…), port loopback non enregistré — **reste bloqué**.

Cf. run todolist 2026-07-02 : l'agent a servi sur `http.server 8081` (hors de
l'allowlist statique) → `localhost:8081` bloqué par le SSRF guard → 20 itérations
de contournement (Edge/Chrome/MCP Windows) sans jamais vérifier le DOM.

Module PUR (stdlib uniquement), thread-safe, SANS dépendance à `src.tools` :
il est importé par `src/utils/url_safety.py` → on évite tout cycle utils→tools.
"""
from __future__ import annotations

import os
import threading
from typing import Dict

# Hôtes loopback autorisés comme cible de preview (jamais l'IP LAN).
_LOOPBACK_HOSTS: frozenset = frozenset({
    "localhost", "localhost.localdomain", "127.0.0.1", "::1",
})

_lock = threading.RLock()
# port -> {"workspace": str, "task_id": str}
_previews: Dict[int, Dict[str, str]] = {}


# ── LOT E (run CéramiShop 2026-07-04) — ports du PLAN DE CONTRÔLE de Lumena ──────
# Une preview de mission ne doit JAMAIS pouvoir enregistrer NI atteindre le port
# de Lumena elle-même (serveur web, IDE bridge, moteur Ollama) : sinon une mission
# ferait pointer `browser_navigate` sur l'UI/l'API de Lumena. Source de vérité
# unique (env-driven → suit la config réelle), consommée par register_preview,
# is_preview_allowed ET le SSRF guard (url_safety.assert_url_safe).
def reserved_lumena_ports() -> frozenset:
    """Ports que Lumena occupe pour son propre contrôle — interdits aux missions."""
    ports = set()
    for env_name, default in (("LUMENA_PORT", 8080), ("LUMENA_IDE_WS_PORT", 8245)):
        try:
            ports.add(int(os.getenv(env_name, str(default))))
        except (TypeError, ValueError):
            ports.add(default)
    ports.add(11434)  # Ollama (moteur LLM local)
    return frozenset(ports)


def _coerce_port(port) -> "int | None":
    try:
        p = int(port)
    except (TypeError, ValueError):
        return None
    return p if 1 <= p <= 65535 else None


def register_preview(port, *, workspace: str = "", task_id: str = "") -> bool:
    """Enregistre un port loopback comme preview contrôlée. Retourne True si OK.

    LOT E : refuse tout port réservé de Lumena — une mission ne peut jamais
    revendiquer le port de contrôle de Lumena comme sa preview."""
    p = _coerce_port(port)
    if p is None or p in reserved_lumena_ports():
        return False
    with _lock:
        _previews[p] = {"workspace": str(workspace or ""), "task_id": str(task_id or "")}
    return True


def unregister_preview(port) -> bool:
    """Retire une preview (à l'arrêt du serveur). True si elle existait."""
    p = _coerce_port(port)
    if p is None:
        return False
    with _lock:
        return _previews.pop(p, None) is not None


def is_preview_allowed(host: str, port) -> bool:
    """True si (host loopback) ET (port enregistré). Jamais l'IP LAN ni l'externe.

    LOT E : défense en profondeur — un port réservé de Lumena n'est jamais
    « allowed », même s'il figurait par erreur dans le registre."""
    if not host:
        return False
    h = str(host).lower().strip().strip("[]")  # ::1 peut arriver entre crochets
    if h not in _LOOPBACK_HOSTS:
        return False
    p = _coerce_port(port)
    if p is None or p in reserved_lumena_ports():
        return False
    with _lock:
        return p in _previews


def list_previews() -> Dict[int, Dict[str, str]]:
    """Copie du registry (port -> métadonnées). Lecture sûre."""
    with _lock:
        return {p: dict(meta) for p, meta in _previews.items()}


def clear_previews() -> None:
    """Vide le registry (tests / reset)."""
    with _lock:
        _previews.clear()
