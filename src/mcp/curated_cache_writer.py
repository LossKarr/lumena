"""
curated_cache_writer.py — Phase F : cache curated user-specific des MCPs.

Doctrine :
  - Append idempotent sur DATA_DIR/mcp_curated/curated_mcp_catalog.json.
  - Dedup par (package_spec, version) — un meme package+version n'apparait
    qu'une fois.
  - JSON malforme → log warning + skip (jamais raise).
  - Le dossier est cree si absent.
  - Le cache n'est JAMAIS commit (DATA_DIR sous .gitignore).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from src.mcp.server_catalog import ServerEntry


_CURATED_SUBDIR = "mcp_curated"
_CURATED_FILENAME = "curated_mcp_catalog.json"


def _curated_path(data_dir: Path) -> Path:
    return Path(data_dir) / _CURATED_SUBDIR / _CURATED_FILENAME


def _entry_to_payload(entry: ServerEntry) -> Dict[str, Any]:
    """Serialise une entry vers la forme stable du cache curated.

    On garde uniquement le strict necessaire pour re-proposer ce package
    plus tard : package_spec, version, display_name, owner_profile,
    semantic_category, decision_source.
    Pas de notes (potentiellement PII), pas de timestamps (non utiles
    pour la curated list).
    """
    return {
        "package_spec": entry.package_spec,
        "version": entry.version,
        "display_name": entry.display_name,
        "owner_profile": entry.owner_profile,
        "semantic_category": entry.semantic_category,
        "category_decision_source": entry.category_decision_source,
    }


def _payload_key(payload: Dict[str, Any]) -> tuple:
    """Cle d'unicite pour dedup : (package_spec, version)."""
    return (payload.get("package_spec"), payload.get("version"))


def _read_existing(path: Path) -> List[Dict[str, Any]]:
    """Lit le cache existant. Retourne [] si absent ou JSON malforme."""
    if not path.exists():
        return []
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning(f"[mcp.curated_cache] read failed: {e}")
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError) as e:
        logger.warning(f"[mcp.curated_cache] malformed JSON, skipping: {e}")
        return []
    if not isinstance(data, list):
        logger.warning(
            f"[mcp.curated_cache] expected list, got {type(data).__name__}, skipping"
        )
        return []
    # Filtre les entrees non-dict (defensif).
    return [e for e in data if isinstance(e, dict)]


def _atomic_write(path: Path, payload: List[Dict[str, Any]]) -> None:
    """Ecriture atomique : tmp file + rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(path)


def append_curated_entry(
    data_dir: Path,
    server_entry: ServerEntry,
) -> bool:
    """Ajoute idempotemment une entry au cache curated.

    Args:
        data_dir: racine DATA_DIR (le cache va dans `mcp_curated/`).
        server_entry: ServerEntry source (typiquement venant de Phase 14).

    Returns:
        True si une nouvelle entree a ete ajoutee, False si :
          - cache existant contient deja (package_spec, version) identique
          - data_dir invalide
          - ecriture echouee (best-effort)
    """
    if not isinstance(data_dir, Path):
        return False
    if not isinstance(server_entry, ServerEntry):
        return False

    path = _curated_path(data_dir)
    existing = _read_existing(path)
    payload = _entry_to_payload(server_entry)
    new_key = _payload_key(payload)

    for existing_entry in existing:
        if _payload_key(existing_entry) == new_key:
            return False

    existing.append(payload)
    try:
        _atomic_write(path, existing)
    except OSError as e:
        logger.warning(f"[mcp.curated_cache] write failed: {e}")
        return False
    return True


def read_curated_entries(data_dir: Path) -> List[Dict[str, Any]]:
    """Lit toutes les entrees curated (vide si fichier absent/malforme)."""
    if not isinstance(data_dir, Path):
        return []
    return _read_existing(_curated_path(data_dir))
