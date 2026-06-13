"""
credentials_service.py — Phase I-4 : coffre-fort SECRETS par MCP.

Doctrine :
  - Wrap au-dessus de SecretsService (chiffrement Fernet déjà existant).
  - Scope d'isolation strict : `mcp_credentials.<server_id>`.
  - Stocke UNIQUEMENT les valeurs des champs `sensitivity == SECRET`.
  - Les valeurs ne sont JAMAIS exposées par `list_keys()` / `status_map()`.
  - `export_for_runtime(server_id, allowlist)` filtre strict pour spawn.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence

from src.services.secrets_service import SecretsService, SecretsServiceError


# ──────────────────────────────────────────────────────────────────────────────
# Validations
# ──────────────────────────────────────────────────────────────────────────────


_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")
_KEY_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")


class MCPCredentialsError(Exception):
    """Erreur sur le coffre credentials MCP."""


def _validate_server_id(server_id: str) -> None:
    if not isinstance(server_id, str) or not _SERVER_ID_RE.match(server_id):
        raise MCPCredentialsError(f"Invalid server_id: {server_id!r}")


def _validate_key_name(key: str) -> None:
    if not isinstance(key, str) or not _KEY_NAME_RE.match(key):
        raise MCPCredentialsError(f"Invalid key name: {key!r}")


def _scope(server_id: str) -> str:
    return f"mcp_credentials.{server_id}"


# ──────────────────────────────────────────────────────────────────────────────
# Service
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CredentialStatus:
    """Statut d'une clé requise (pas la valeur)."""
    key: str
    is_set: bool


class MCPCredentialsService:
    """Coffre SECRETS par server_id MCP, chiffré at-rest via SecretsService."""

    SCOPE_PREFIX = "mcp_credentials"

    def __init__(self, secrets_service: SecretsService):
        if not isinstance(secrets_service, SecretsService):
            raise MCPCredentialsError("secrets_service must be a SecretsService")
        self._secrets = secrets_service

    # ── API CRUD ─────────────────────────────────────────────────────────

    def set(self, server_id: str, key: str, value: str) -> None:
        """Stocke un secret. value="" supprime (politique SecretsService)."""
        _validate_server_id(server_id)
        _validate_key_name(key)
        if not isinstance(value, str):
            raise MCPCredentialsError("Value must be a string")
        try:
            self._secrets.set(_scope(server_id), key, value)
        except SecretsServiceError as e:
            raise MCPCredentialsError(str(e)) from e

    def get(self, server_id: str, key: str) -> Optional[str]:
        """Retourne la valeur (ou None si absente)."""
        _validate_server_id(server_id)
        _validate_key_name(key)
        return self._secrets.get(_scope(server_id), key)

    def delete(self, server_id: str, key: str) -> bool:
        """Supprime explicitement. Retourne True si la clé existait."""
        _validate_server_id(server_id)
        _validate_key_name(key)
        return self._secrets.delete(_scope(server_id), key)

    def has(self, server_id: str, key: str) -> bool:
        """True si la clé est définie ET non vide."""
        _validate_server_id(server_id)
        _validate_key_name(key)
        return self._secrets.has(_scope(server_id), key)

    def list_keys(self, server_id: str) -> List[str]:
        """Liste les NOMS des clés stockées (JAMAIS les valeurs)."""
        _validate_server_id(server_id)
        return self._secrets.list_keys(_scope(server_id))

    # ── Helpers métier (pour UI / activation) ───────────────────────────

    def has_all(self, server_id: str, required_keys: Sequence[str]) -> bool:
        """True ssi TOUTES les clés requises sont définies pour ce MCP."""
        _validate_server_id(server_id)
        if not required_keys:
            return True
        existing = set(self._secrets.list_keys(_scope(server_id)))
        return all(k in existing for k in required_keys)

    def missing_keys(
        self, server_id: str, required_keys: Sequence[str],
    ) -> List[str]:
        """Liste des clés requises qui ne sont PAS encore définies."""
        _validate_server_id(server_id)
        if not required_keys:
            return []
        existing = set(self._secrets.list_keys(_scope(server_id)))
        return [k for k in required_keys if k not in existing]

    def status_map(
        self, server_id: str, required_keys: Sequence[str],
    ) -> Dict[str, str]:
        """Mapping {key: 'set'|'missing'} pour affichage UI.

        Ne renvoie JAMAIS les valeurs. Le client UI ne peut donc pas lire
        les secrets via cette méthode même par erreur.
        """
        _validate_server_id(server_id)
        existing = set(self._secrets.list_keys(_scope(server_id)))
        return {
            k: ("set" if k in existing else "missing")
            for k in (required_keys or [])
        }

    def export_for_runtime(
        self, server_id: str, allowlist: Sequence[str],
    ) -> Dict[str, str]:
        """Snapshot des valeurs pour injection au spawn du sandbox.

        Filtre strict via `allowlist` (defense-in-depth) : seules les clés
        listées sont remontées, même si d'autres existent dans le scope.

        Args:
            server_id: id catalog.
            allowlist: liste des clés autorisées (généralement
                `MCPConfigSchema.secret_field_names()` + secrets globaux MCP).

        Returns:
            dict {key: value} pour les clés présentes ET dans l'allowlist.
        """
        _validate_server_id(server_id)
        if not allowlist:
            return {}
        allowed = set(allowlist)
        out: Dict[str, str] = {}
        for k in self._secrets.list_keys(_scope(server_id)):
            if k not in allowed:
                continue
            v = self._secrets.get(_scope(server_id), k)
            if isinstance(v, str) and v:
                out[k] = v
        return out

    def clear_all(self, server_id: str) -> int:
        """Vide tous les secrets d'un MCP (utilisé lors de remove_server).

        Returns: nombre de clés supprimées.
        """
        _validate_server_id(server_id)
        keys = self._secrets.list_keys(_scope(server_id))
        deleted = 0
        for k in keys:
            if self._secrets.delete(_scope(server_id), k):
                deleted += 1
        return deleted


__all__ = [
    "MCPCredentialsService",
    "MCPCredentialsError",
    "CredentialStatus",
]
