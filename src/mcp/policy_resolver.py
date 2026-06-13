"""
policy_resolver.py — ToolRegistryPolicyResolver (Phase 15 v2).

Implémentation concrète du Protocol PolicyResolver défini en Phase 13.

DOCTRINE Phase 15 :
  - Lecture seule : aucune mutation de ToolRegistry ni Catalog.
  - Aucun câblage runtime.
  - Aucune touche à : tool_registry.py, react.py, sub_agent.py,
    orchestrator.py, server_catalog.py, MCPSandboxRunner, MCPClient,
    approval_queue.py, policy.py, auto_approve.py, runtime_watcher.py.
  - Pas de cache (KISS).
  - Pas de Tool Discovery.
  - Pas de PolicyAttributor.

Source de vérité :
  - server callable ↔ `catalog.is_callable(server_id)` (Phase 14).
    Le resolver ne duplique pas la logique status==ACTIVE.
  - server REMOVED ↔ bloque toujours, indépendamment de require_callable.

⚠️ Phase 15 utilise une regex tool_name plus stricte (lowercase only) que
l'MCPOrchestrator actuel (Phase 13). En pratique les MCP servers exposent
des noms d'outils lowercase ; le resolver impose cette convention. Un
alignement éventuel de l'orchestrator sera fait en Phase ultérieure si
besoin. Phase 15 ne touche pas orchestrator.py.

Audit policy (anti-leak markers attacker-controlled) :
  - server_id invalide → NE LOG NI server_id NI tool_name
  - tool_name invalide → LOG server_id (validé), PAS tool_name
  - binding mismatch   → LOG server_id ET tool_name (tous deux validés)
  - étapes suivantes   → LOG librement server_id et tool_name

Layout disque :
  DATA_DIR/mcp_policy_resolver/audit.jsonl
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Protocol, runtime_checkable

from loguru import logger

from src.mcp.policy import MCPPolicy
from src.mcp.server_catalog import (
    CatalogError,
    MCPServerCatalog,
    ServerStatus,
)
from src.utils.paths import DATA_DIR


# ──────────────────────────────────────────────────────────────────────────────
# Constantes
# ──────────────────────────────────────────────────────────────────────────────

_DEFAULT_DIRNAME = "mcp_policy_resolver"
_AUDIT_FILENAME = "audit.jsonl"

# Validation server_id (cohérent avec Phase 14)
_SERVER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_.\-]{0,63}$")

# Fix AZ (Phase I-8) : segment TOOL en casse libre — la spec MCP ne
# l'impose pas (windows-mcp expose App/Click/PowerShell ; sans ça, les
# tools enregistrés étaient INEXÉCUTABLES : resolve → tool_name_invalid).
# Le segment SERVER reste lowercase strict (slugs dérivés par nous).
_TOOL_NAME_RE_P15 = re.compile(r"^mcp__[a-z0-9_.\-]+__[A-Za-z0-9_.\-]+$")


# ──────────────────────────────────────────────────────────────────────────────
# Protocol attendu du ToolRegistry (duck-typed)
# ──────────────────────────────────────────────────────────────────────────────


@runtime_checkable
class ToolRegistryLike(Protocol):
    """Protocol minimal attendu du ToolRegistry — lecture seule.

    Le ToolRegistry concret expose ces méthodes (Phase 8). Le resolver ne
    les importe pas directement pour faciliter les tests et éviter le
    couplage.
    """

    def is_dynamic_handler(self, name: str) -> bool: ...

    def get_dynamic_handler_policy(
        self, name: str
    ) -> Optional[MCPPolicy]: ...


# ──────────────────────────────────────────────────────────────────────────────
# Helpers internes
# ──────────────────────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_valid_server_id(server_id: Any) -> bool:
    if not isinstance(server_id, str):
        return False
    if not _SERVER_ID_RE.match(server_id):
        return False
    if ".." in server_id or "/" in server_id or "\\" in server_id:
        return False
    # On délègue les noms Windows réservés au Catalog au moment du
    # get_server, mais on les refuse aussi ici pour cohérence forensique.
    # Cohérent avec Phase 14.
    _WINDOWS_RESERVED = frozenset({
        "con", "prn", "aux", "nul",
        "com1", "com2", "com3", "com4", "com5",
        "com6", "com7", "com8", "com9",
        "lpt1", "lpt2", "lpt3", "lpt4", "lpt5",
        "lpt6", "lpt7", "lpt8", "lpt9",
    })
    stem = server_id.split(".", 1)[0]
    if stem in _WINDOWS_RESERVED:
        return False
    return True


def _is_valid_tool_name_p15(tool_name: Any) -> bool:
    """Phase 15 stricte : lowercase only."""
    if not isinstance(tool_name, str):
        return False
    return bool(_TOOL_NAME_RE_P15.match(tool_name))


# ──────────────────────────────────────────────────────────────────────────────
# Resolver
# ──────────────────────────────────────────────────────────────────────────────


class ToolRegistryPolicyResolver:
    """Implémente PolicyResolver Phase 13 en wrappant ToolRegistry + Catalog.

    Lecture seule. Aucune mutation. Pas de cache.

    Args:
        tool_registry: objet exposant le Protocol ToolRegistryLike
        server_catalog: instance MCPServerCatalog Phase 14
        require_callable: si True (défaut), exige `catalog.is_callable(server_id)`
        audit_log_path: path optionnel pour l'audit jsonl
    """

    def __init__(
        self,
        tool_registry: ToolRegistryLike,
        server_catalog: MCPServerCatalog,
        require_callable: bool = True,
        audit_log_path: Optional[Path] = None,
    ):
        if tool_registry is None:
            raise ValueError("tool_registry must not be None")
        if not (
            callable(getattr(tool_registry, "is_dynamic_handler", None))
            and callable(getattr(tool_registry, "get_dynamic_handler_policy", None))
        ):
            raise ValueError(
                "tool_registry must expose is_dynamic_handler and "
                "get_dynamic_handler_policy callables"
            )
        if server_catalog is None:
            raise ValueError("server_catalog must not be None")

        self._registry = tool_registry
        self._catalog = server_catalog
        self._require_callable = bool(require_callable)
        self._audit_log_path = audit_log_path or (
            DATA_DIR / _DEFAULT_DIRNAME / _AUDIT_FILENAME
        )
        self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Properties ────────────────────────────────────────────────────────

    @property
    def require_callable(self) -> bool:
        return self._require_callable

    @property
    def audit_log_path(self) -> Path:
        return self._audit_log_path

    # ── Audit (whitelist stricte) ─────────────────────────────────────────

    def _audit(self, event: str, **fields: Any) -> None:
        """Append-only audit jsonl.

        Champs whitelist : server_id, tool_name, reason, policy, status, ts.
        Les callers sont responsables de respecter la règle anti-leak.
        """
        record = {"ts": _now_iso(), "event": event, **fields}
        try:
            self._audit_log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self._audit_log_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning(f"[mcp.policy_resolver] audit write failed: {e}")

    # ── resolve() ─────────────────────────────────────────────────────────

    def resolve(
        self, server_id: Any, tool_name: Any
    ) -> Optional[MCPPolicy]:
        """Résout la policy pour (server_id, tool_name).

        Returns None si une étape échoue. Aucune exception propagée.

        Audit (whitelist stricte) :
          - server_id invalide → NE LOG NI server_id NI tool_name
          - tool_name invalide → LOG server_id (validé), PAS tool_name
          - binding mismatch   → LOG server_id ET tool_name
          - étapes suivantes   → LOG librement server_id et tool_name
        """
        # ── Étape 1 : server_id validation ────────────────────────────────
        if not _is_valid_server_id(server_id):
            # ⚠️ Anti-leak : ni server_id ni tool_name
            self._audit("resolve_failed", reason="server_id_invalid")
            return None

        # ── Étape 2 : tool_name validation (charset strict, casse libre) ──
        if not _is_valid_tool_name_p15(tool_name):
            # ⚠️ Anti-leak : pas de tool_name
            self._audit(
                "resolve_failed",
                server_id=server_id,
                reason="tool_name_invalid",
            )
            return None

        # ── Étape 3 : binding tool_server ─────────────────────────────────
        expected_prefix = f"mcp__{server_id}__"
        if not tool_name.startswith(expected_prefix):
            self._audit(
                "resolve_failed",
                server_id=server_id,
                tool_name=tool_name,
                reason="tool_server_mismatch",
            )
            return None

        # ── Étape 4 : Catalog get_server ──────────────────────────────────
        try:
            entry = self._catalog.get_server(server_id)
        except CatalogError:
            self._audit(
                "resolve_failed",
                server_id=server_id,
                tool_name=tool_name,
                reason="dependency_error",
            )
            return None
        except Exception:  # noqa: BLE001
            self._audit(
                "resolve_failed",
                server_id=server_id,
                tool_name=tool_name,
                reason="dependency_error",
            )
            return None

        if entry is None:
            self._audit(
                "resolve_failed",
                server_id=server_id,
                tool_name=tool_name,
                reason="server_unknown",
            )
            return None

        # ── Étape 5a : REMOVED bloque toujours ────────────────────────────
        if entry.status == ServerStatus.REMOVED:
            self._audit(
                "resolve_failed",
                server_id=server_id,
                tool_name=tool_name,
                reason="server_removed",
                status=entry.status.value,
            )
            return None

        # ── Étape 5b : require_callable via catalog.is_callable() ─────────
        if self._require_callable:
            try:
                callable_ok = self._catalog.is_callable(server_id)
            except Exception:  # noqa: BLE001
                self._audit(
                    "resolve_failed",
                    server_id=server_id,
                    tool_name=tool_name,
                    reason="dependency_error",
                )
                return None
            if not callable_ok:
                # Source de vérité = catalog.is_callable() ; on log juste le
                # status réel pour observabilité (Catalog Phase 14 garantit
                # qu'il est non-PII).
                self._audit(
                    "resolve_failed",
                    server_id=server_id,
                    tool_name=tool_name,
                    reason="server_not_callable",
                    status=entry.status.value,
                )
                return None

        # ── Étape 6 : ToolRegistry is_dynamic_handler ─────────────────────
        try:
            is_dyn = self._registry.is_dynamic_handler(tool_name)
        except Exception:  # noqa: BLE001
            self._audit(
                "resolve_failed",
                server_id=server_id,
                tool_name=tool_name,
                reason="dependency_error",
            )
            return None
        if not is_dyn:
            self._audit(
                "resolve_failed",
                server_id=server_id,
                tool_name=tool_name,
                reason="tool_not_registered",
            )
            return None

        # ── Étape 7 : get_dynamic_handler_policy ──────────────────────────
        try:
            policy = self._registry.get_dynamic_handler_policy(tool_name)
        except Exception:  # noqa: BLE001
            self._audit(
                "resolve_failed",
                server_id=server_id,
                tool_name=tool_name,
                reason="dependency_error",
            )
            return None

        if policy is None:
            self._audit(
                "resolve_failed",
                server_id=server_id,
                tool_name=tool_name,
                reason="policy_not_attributed",
            )
            return None

        if not isinstance(policy, MCPPolicy):
            self._audit(
                "resolve_failed",
                server_id=server_id,
                tool_name=tool_name,
                reason="policy_invalid",
            )
            return None

        # ── Étape 8 : Succès ─────────────────────────────────────────────
        self._audit(
            "resolve_ok",
            server_id=server_id,
            tool_name=tool_name,
            policy=policy.value,
        )
        return policy
