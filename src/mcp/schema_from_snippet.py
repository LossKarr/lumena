"""
schema_from_snippet.py — Phase I-3 Niveau 4 : schema depuis snippet user (chat).

Quand les Niveaux 2-3 échouent (README absent, MCP exotique), Lumena demande
à l'utilisateur de coller un snippet de doc/config et on l'analyse.

Formats acceptés (heuristique tolérante) :
  - JSON `claude_desktop_config` ou fragment `env: {...}`
  - Lignes `KEY=value` (style .env)
  - Lignes `export KEY=value`
  - Liste markdown `- KEY : description`
  - Liste de noms majuscules un par ligne

Doctrine : tolérant aux fautes de frappe, déterministe, pas d'I/O.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Set

from src.mcp.config_schema import MCPConfigSchema
from src.mcp.schema_extractor import (
    _build_field,
    _extract_env_vars,
    _is_valid_var_name,
)


# Pattern .env / export simple : KEY=value, KEY= value, etc.
_DOTENV_RE = re.compile(
    r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]{2,62})\s*=", re.MULTILINE,
)
# Liste markdown `- KEY`, `* KEY`, `+ KEY`, `KEY:`
_LIST_RE = re.compile(
    r"^\s*(?:[-*+]\s*)?[`]?([A-Z][A-Z0-9_]{2,62})[`]?(?:\s*[:|]|\s*$)",
    re.MULTILINE,
)


def _extract_env_vars_tolerant(snippet: str) -> Set[str]:
    """Combine plusieurs heuristiques pour un parsing maximal."""
    if not isinstance(snippet, str) or not snippet.strip():
        return set()
    found: Set[str] = set()

    # 1) tente JSON pur (l'user peut coller juste `{ "SLACK_BOT_TOKEN": "..." }`)
    stripped = snippet.strip()
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
            if isinstance(data, dict):
                # Si tout le top-level a l'air d'env vars
                for k in data.keys():
                    if _is_valid_var_name(k):
                        found.add(k)
                # Sinon walk pour 'env' nested
                from src.mcp.schema_extractor import _walk_for_env_dict
                for env_dict in _walk_for_env_dict(data):
                    for k in env_dict:
                        if _is_valid_var_name(k):
                            found.add(k)
        except (ValueError, TypeError):
            pass

    # 2) heuristiques markdown/READMElike (réutilise le parser principal)
    found |= _extract_env_vars(snippet)

    # 3) lignes .env simples
    for m in _DOTENV_RE.finditer(snippet):
        if _is_valid_var_name(m.group(1)):
            found.add(m.group(1))

    # 4) listes markdown
    for m in _LIST_RE.finditer(snippet):
        if _is_valid_var_name(m.group(1)):
            found.add(m.group(1))

    return found


def schema_from_user_snippet(
    *,
    server_id: str,
    snippet: str,
) -> MCPConfigSchema:
    """Construit un MCPConfigSchema depuis un snippet utilisateur libre.

    Args:
        server_id: id du serveur catalog.
        snippet: chaîne brute fournie par l'user (JSON / .env / markdown).

    Returns:
        MCPConfigSchema avec detected_from="user". Si aucun champ trouvé,
        le schéma retourné est valide mais a `fields=()` (l'appelant peut
        décider de redemander).
    """
    if not isinstance(server_id, str) or not server_id:
        server_id = "unknown"
    names = sorted(_extract_env_vars_tolerant(snippet))
    fields = tuple(_build_field(name) for name in names)
    return MCPConfigSchema(
        server_id=server_id,
        fields=fields,
        auth_flows=(),
        detected_from="user",
        detected_at=datetime.now(timezone.utc).isoformat(),
    )


__all__ = ["schema_from_user_snippet"]
