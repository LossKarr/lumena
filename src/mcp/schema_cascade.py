"""
schema_cascade.py — Phase I-3 : cascade officielle Niveau 1 → 4.

Doctrine : un seul point d'entrée pour le reste du code.

  Niveau 1 — curated (KNOWN_MCPS)
  Niveau 2 — package metadata (README parse)
  Niveau 3 — runtime probe (binaire --help)
  Niveau 4 — user snippet (chat fallback)

Le caller choisit quels niveaux activer (par défaut 1+2). Niveau 3 nécessite
un binary path, Niveau 4 nécessite un snippet user — donc déclenchés
explicitement.
"""
from __future__ import annotations

from typing import Optional

from src.mcp.config_schema import MCPConfigSchema
from src.mcp.known_mcps import lookup_known_mcp
from src.mcp.schema_extractor import JSONFetcher, extract_schema_from_package
from src.mcp.schema_from_snippet import schema_from_user_snippet
from src.mcp.schema_prober import ProbeRunner, probe_schema_from_binary


def detect_schema(
    *,
    server_id: str,
    intent: Optional[str] = None,           # pour Niveau 1
    package_spec: Optional[str] = None,     # pour Niveau 2
    binary_path: Optional[str] = None,      # pour Niveau 3
    user_snippet: Optional[str] = None,     # pour Niveau 4
    fetch_json: Optional[JSONFetcher] = None,
    probe_runner: Optional[ProbeRunner] = None,
    timeout_s: float = 8.0,
    enable_levels: tuple = (1, 2, 3, 4),
) -> Optional[MCPConfigSchema]:
    """Cascade 1→4. Retourne le 1er schéma non vide trouvé.

    Args:
        server_id: id catalog (utilisé comme server_id du schéma).
        intent: chaîne de l'user pour matching curated.
        package_spec: 'npm:<x>' ou 'pypi:<x>' pour fetch metadata.
        binary_path: pour probe runtime Niveau 3.
        user_snippet: contenu collé par l'user (Niveau 4).
        fetch_json: callable réseau injecté (testabilité).
        probe_runner: callable subprocess injecté (testabilité).
        timeout_s: timeout HTTP.
        enable_levels: tuple de niveaux à activer (1=curated, 2=package,
            3=probe, 4=user).

    Returns:
        MCPConfigSchema ou None si rien trouvé.
    """
    # Niveau 1 : curated
    if 1 in enable_levels and intent:
        known = lookup_known_mcp(intent)
        if known is not None:
            return known.to_schema()

    # Niveau 2 : README package
    if 2 in enable_levels and package_spec:
        s = extract_schema_from_package(
            server_id=server_id,
            package_spec=package_spec,
            fetch_json=fetch_json,
            timeout_s=timeout_s,
        )
        if s is not None and s.fields:
            return s

    # Niveau 3 : probe runtime
    if 3 in enable_levels and binary_path:
        s = probe_schema_from_binary(
            server_id=server_id,
            binary_path=binary_path,
            runner=probe_runner,
        )
        if s is not None and s.fields:
            return s

    # Niveau 4 : snippet user
    if 4 in enable_levels and user_snippet:
        s = schema_from_user_snippet(
            server_id=server_id,
            snippet=user_snippet,
        )
        if s is not None and s.fields:
            return s

    return None


__all__ = ["detect_schema"]
