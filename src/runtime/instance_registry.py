"""Phase 3 — Registre local des instances Lumena (même PC)."""
from __future__ import annotations

import json
import os
import socket
import time
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

REGISTRY_FILENAME = "local_instances.json"
STALE_TTL_SECONDS: float = 120.0

_REGISTRY_LOCK = threading.Lock()

VALID_ROLES = frozenset({"primary", "worker", "standalone"})


@dataclass
class InstanceRecord:
    instance_id: str
    instance_name: str
    pid: int
    port: int
    role: str          # primary | worker | standalone
    data_dir: str
    workspace_dir: str
    started_at: str    # ISO-8601
    last_seen: str     # ISO-8601
    version: str = ""
    capabilities: List[str] = field(default_factory=list)
    host: str = ""


def _pid_alive(pid: int) -> bool:
    """Vérifie si un processus est vivant (cross-platform)."""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION,
                False,
                int(pid),
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def find_free_port(
    start: int = 8080,
    end: int = 9000,
    exclude: Optional[set] = None,
) -> int:
    """Retourne le premier port TCP libre dans [start, end].

    Utilisé quand LUMENA_PORT=auto pour éviter les collisions entre instances.
    """
    excluded = exclude or set()
    for port in range(start, end + 1):
        if port in excluded:
            continue
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError(f"Aucun port libre trouvé dans {start}-{end}")


class InstanceRegistry:
    """Registre JSON local des instances Lumena sur ce PC.

    Thread-safe via _REGISTRY_LOCK + écriture atomique (rename).
    """

    def __init__(self, registry_file: Path):
        self.registry_file = registry_file
        self.registry_file.parent.mkdir(parents=True, exist_ok=True)

    # ── I/O ──────────────────────────────────────────────────────────────────

    def _load(self) -> Dict[str, dict]:
        try:
            if self.registry_file.exists():
                return json.loads(self.registry_file.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save(self, data: Dict[str, dict]) -> None:
        tmp = self.registry_file.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            tmp.replace(self.registry_file)
        except Exception:
            pass

    # ── Public API ────────────────────────────────────────────────────────────

    def register(self, record: InstanceRecord) -> None:
        """Enregistre ou met à jour une instance."""
        with _REGISTRY_LOCK:
            data = self._load()
            data[record.instance_id] = asdict(record)
            self._save(data)

    def update_heartbeat(self, instance_id: str) -> None:
        """Met à jour last_seen et pid pour une instance existante."""
        with _REGISTRY_LOCK:
            data = self._load()
            if instance_id in data:
                data[instance_id]["last_seen"] = datetime.now().isoformat()
                data[instance_id]["pid"] = os.getpid()
                self._save(data)

    def unregister(self, instance_id: str) -> None:
        """Supprime une instance du registre."""
        with _REGISTRY_LOCK:
            data = self._load()
            data.pop(instance_id, None)
            self._save(data)

    def get_all(self) -> List[InstanceRecord]:
        """Retourne toutes les entrées (y compris stale)."""
        with _REGISTRY_LOCK:
            data = self._load()
        return [InstanceRecord(**v) for v in data.values()]

    def get_live(self, ttl_seconds: float = STALE_TTL_SECONDS) -> List[InstanceRecord]:
        """Retourne les instances dont le PID est vivant ET last_seen récent."""
        now = time.time()
        live = []
        for record in self.get_all():
            if not _pid_alive(record.pid):
                continue
            try:
                last = datetime.fromisoformat(record.last_seen).timestamp()
                if now - last > ttl_seconds:
                    continue
            except Exception:
                continue
            live.append(record)
        return live

    def cleanup_stale(self, ttl_seconds: float = STALE_TTL_SECONDS) -> int:
        """Supprime les entrées dont le PID est mort ou last_seen trop ancien.

        Returns:
            Nombre d'entrées supprimées.
        """
        with _REGISTRY_LOCK:
            data = self._load()
            now = time.time()
            to_remove = []
            for iid, rec in data.items():
                pid = rec.get("pid", -1)
                last_seen = rec.get("last_seen", "")
                alive = _pid_alive(pid)
                stale = True
                if last_seen:
                    try:
                        stale = (
                            now - datetime.fromisoformat(last_seen).timestamp()
                            > ttl_seconds
                        )
                    except Exception:
                        stale = True
                if not alive or stale:
                    to_remove.append(iid)
            for iid in to_remove:
                del data[iid]
            self._save(data)
        return len(to_remove)

    def get_used_ports(self, ttl_seconds: float = STALE_TTL_SECONDS) -> set:
        """Retourne les ports déclarés par les instances encore vivantes."""
        return {r.port for r in self.get_live(ttl_seconds)}


def get_registry(data_dir: Optional[Path] = None) -> InstanceRegistry:
    """Retourne un InstanceRegistry pointant sur data/instances/local_instances.json."""
    from src.utils.paths import DATA_DIR
    base = data_dir or DATA_DIR
    return InstanceRegistry(base / "instances" / REGISTRY_FILENAME)


def resolve_startup_port(
    start: int = 8080,
    end: int = 9000,
    data_dir: Optional[Path] = None,
) -> int:
    """Résout le port de démarrage pour LUMENA_PORT=auto.

    Consulte le registre pour exclure les ports déjà utilisés par des instances
    vivantes, puis retourne le premier port TCP libre dans [start, end].
    """
    try:
        used = get_registry(data_dir).get_used_ports()
    except Exception:
        used = set()
    return find_free_port(start, end, exclude=used)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
