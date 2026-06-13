"""
policy.py — Policy Engine MCP (Phase 9).

Définit les 6 sous-catégories MCP et la politique de blocage Phase 9.

DOCTRINE Phase 9 : aucun handler MCP dynamique n'est callable sans policy
explicite. La politique de blocage Phase 9 est STRICTE :

Autorisé Phase 9 :
  - READ_ONLY
  - EXTERNAL_READ

Bloqué Phase 9 (en attente Phase 10) :
  - LOCAL_WRITE          → nécessite snapshot/rollback (Phase 10)
  - EXTERNAL_WRITE_RECOVERABLE → nécessite watcher + approval policy (Phase 10)
  - EXTERNAL_WRITE_IRREVERSIBLE → nécessite approval queue stricte (Phase 10)
  - SECRETS_AUTH         → nécessite confirmation explicite (Phase 10)

Raison : un MCP dynamique n'est PAS un handler natif Lumena. On ne doit pas
le laisser écrire juste parce qu'il a une policy explicite. Les phases
ultérieures ouvriront chaque catégorie avec ses contrôles dédiés.

Module pur : aucun side effect, aucun import lourd (pas de subprocess, pas
de cryptography). Importable depuis tool_registry sans cycles.
"""
from __future__ import annotations

from enum import Enum
from typing import FrozenSet


class MCPPolicy(Enum):
    """6 sous-catégories MCP (cf REPO/PLAN_MCP_LUMENA.md v4.1 Phase 9)."""

    READ_ONLY = "read_only"
    LOCAL_WRITE = "local_write"
    EXTERNAL_READ = "external_read"
    EXTERNAL_WRITE_RECOVERABLE = "external_write_recoverable"
    EXTERNAL_WRITE_IRREVERSIBLE = "external_write_irreversible"
    SECRETS_AUTH = "secrets_auth"


# Politique de blocage stricte Phase 9
_BLOCKED_POLICIES_PHASE9: FrozenSet[MCPPolicy] = frozenset(
    {
        MCPPolicy.LOCAL_WRITE,
        MCPPolicy.EXTERNAL_WRITE_RECOVERABLE,
        MCPPolicy.EXTERNAL_WRITE_IRREVERSIBLE,
        MCPPolicy.SECRETS_AUTH,
    }
)

_ALLOWED_POLICIES_PHASE9: FrozenSet[MCPPolicy] = frozenset(
    {
        MCPPolicy.READ_ONLY,
        MCPPolicy.EXTERNAL_READ,
    }
)


def is_blocked_phase9(policy: MCPPolicy) -> bool:
    """True si la policy doit être bloquée par défaut en Phase 9.

    Pure function : aucun side effect, déterministe.
    """
    return policy in _BLOCKED_POLICIES_PHASE9


def is_allowed_phase9(policy: MCPPolicy) -> bool:
    """True si la policy est autorisée Phase 9."""
    return policy in _ALLOWED_POLICIES_PHASE9


def blocked_policies_phase9() -> FrozenSet[MCPPolicy]:
    """Retourne le set des policies bloquées Phase 9 (copie défensive d'API)."""
    return _BLOCKED_POLICIES_PHASE9


def allowed_policies_phase9() -> FrozenSet[MCPPolicy]:
    """Retourne le set des policies autorisées Phase 9."""
    return _ALLOWED_POLICIES_PHASE9


# ── Fix T (Phase I-7) : levée contrôlée des policies WRITE ──────────────────
#
# Phase 9 bloquait TOUTES les écritures MCP en attendant l'infra approval.
# Cette levée est gouvernée par un DOUBLE opt-in env (les deux requis) :
#   - LUMENA_MCP_LIVE=1        (mode live MCP global)
#   - LUMENA_MCP_TRUST_LIVE=1  (l'utilisateur fait confiance aux MCPs actifs)
#
# SECRETS_AUTH reste TOUJOURS bloqué — un tool MCP qui manipule des
# credentials ne doit jamais s'exécuter sans approval humain explicite,
# quel que soit le flag.

_NEVER_LIFTABLE: FrozenSet[MCPPolicy] = frozenset({MCPPolicy.SECRETS_AUTH})


def is_write_lift_enabled() -> bool:
    """True si le double opt-in env autorise la levée des policies WRITE."""
    import os

    def _flag(name: str) -> bool:
        return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")

    return _flag("LUMENA_MCP_LIVE") and _flag("LUMENA_MCP_TRUST_LIVE")


def is_blocked_effective(policy: MCPPolicy) -> bool:
    """Blocage effectif d'une policy en tenant compte de la levée Fix T.

    - Policies ALLOWED Phase 9 (READ_ONLY, EXTERNAL_READ) : jamais bloquées.
    - SECRETS_AUTH : toujours bloquée (non levable).
    - Autres WRITE : bloquées sauf si double opt-in env actif.
    """
    if not is_blocked_phase9(policy):
        return False
    if policy in _NEVER_LIFTABLE:
        return True
    return not is_write_lift_enabled()
