"""
config_service.py — Phase I-4 : config NON-SECRETS par MCP (JSON disque).

Doctrine :
  - Stocke les champs SENSITIVE et NORMAL (paths, URLs, ports, etc.).
  - Un fichier JSON par server_id : `<config_root>/<server_id>.json`.
  - Écriture atomique (tmp + replace).
  - Filtrage strict des clés par regex.
  - JAMAIS de SECRET ici (les SECRET vont dans MCPCredentialsService).
"""
from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from src.utils.paths import DATA_DIR


_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")
_KEY_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


class MCPConfigError(Exception):
    """Erreur sur le ConfigService MCP."""


def _validate_server_id(server_id: str) -> None:
    if not isinstance(server_id, str) or not _SERVER_ID_RE.match(server_id):
        raise MCPConfigError(f"Invalid server_id: {server_id!r}")


def _validate_key_name(key: str) -> None:
    if not isinstance(key, str) or not _KEY_NAME_RE.match(key):
        raise MCPConfigError(f"Invalid key name: {key!r}")


class MCPConfigService:
    """Stockage JSON par MCP pour les champs NON-secrets."""

    DEFAULT_SUBDIR = "mcp_config"

    def __init__(self, config_root: Optional[Path] = None):
        self._root = Path(config_root) if config_root else (DATA_DIR / self.DEFAULT_SUBDIR)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    # ── File helpers ─────────────────────────────────────────────────────

    def _path(self, server_id: str) -> Path:
        return self._root / f"{server_id}.json"

    def _read(self, server_id: str) -> Dict[str, str]:
        path = self._path(server_id)
        if not path.exists():
            return {}
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            return {}
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return {}
        if not isinstance(data, dict):
            return {}
        # Filtre strict : seuls les couples (str, str) valides survivent.
        return {
            str(k): str(v)
            for k, v in data.items()
            if isinstance(k, str) and isinstance(v, (str, int, float, bool))
        }

    def _write(self, server_id: str, data: Dict[str, str]) -> None:
        path = self._path(server_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        tmp.replace(path)

    # ── API CRUD ─────────────────────────────────────────────────────────

    def set(self, server_id: str, key: str, value: str) -> None:
        """Stocke une valeur de config. value="" supprime la clé."""
        _validate_server_id(server_id)
        _validate_key_name(key)
        if not isinstance(value, str):
            raise MCPConfigError("Value must be a string")
        with self._lock:
            data = self._read(server_id)
            if value == "":
                data.pop(key, None)
            else:
                data[key] = value
            self._write(server_id, data)

    def get(self, server_id: str, key: str) -> Optional[str]:
        _validate_server_id(server_id)
        _validate_key_name(key)
        with self._lock:
            data = self._read(server_id)
        return data.get(key)

    def delete(self, server_id: str, key: str) -> bool:
        _validate_server_id(server_id)
        _validate_key_name(key)
        with self._lock:
            data = self._read(server_id)
            if key not in data:
                return False
            data.pop(key, None)
            self._write(server_id, data)
            return True

    def has(self, server_id: str, key: str) -> bool:
        _validate_server_id(server_id)
        _validate_key_name(key)
        with self._lock:
            data = self._read(server_id)
        return key in data

    def list_keys(self, server_id: str) -> List[str]:
        _validate_server_id(server_id)
        with self._lock:
            data = self._read(server_id)
        return sorted(data.keys())

    def list_items(self, server_id: str) -> Dict[str, str]:
        """Retourne le dict complet (clés ET valeurs).

        ATTENTION : à n'utiliser que pour :
          - export_for_runtime (injection env subprocess)
          - affichage UI des champs NORMAL non sensibles
        Les champs SENSITIVE (webhook URLs, etc.) restent ici mais l'UI
        doit les masquer par défaut.
        """
        _validate_server_id(server_id)
        with self._lock:
            return dict(self._read(server_id))

    # ── Helpers métier ──────────────────────────────────────────────────

    def has_all(self, server_id: str, required_keys: Sequence[str]) -> bool:
        _validate_server_id(server_id)
        if not required_keys:
            return True
        with self._lock:
            data = self._read(server_id)
        return all(k in data for k in required_keys)

    def missing_keys(
        self, server_id: str, required_keys: Sequence[str],
    ) -> List[str]:
        _validate_server_id(server_id)
        if not required_keys:
            return []
        with self._lock:
            data = self._read(server_id)
        return [k for k in required_keys if k not in data]

    def status_map(
        self, server_id: str, required_keys: Sequence[str],
    ) -> Dict[str, str]:
        """{key: 'set'|'missing'}."""
        _validate_server_id(server_id)
        with self._lock:
            data = self._read(server_id)
        return {k: ("set" if k in data else "missing") for k in (required_keys or [])}

    def export_for_runtime(
        self, server_id: str, allowlist: Sequence[str],
    ) -> Dict[str, str]:
        """Snapshot pour injection env subprocess.

        Filtre strict via `allowlist`. Les clés stockées hors allowlist
        sont ignorées (defense-in-depth).
        """
        _validate_server_id(server_id)
        if not allowlist:
            return {}
        allowed = set(allowlist)
        with self._lock:
            data = self._read(server_id)
        return {k: v for k, v in data.items() if k in allowed}

    def clear_all(self, server_id: str) -> int:
        """Vide complètement la config d'un MCP. Retourne nombre supprimé."""
        _validate_server_id(server_id)
        with self._lock:
            data = self._read(server_id)
            count = len(data)
            try:
                self._path(server_id).unlink(missing_ok=True)
            except OSError:
                return 0
        return count


__all__ = ["MCPConfigService", "MCPConfigError"]
