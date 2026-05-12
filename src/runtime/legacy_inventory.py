"""
Phase -1 — Inventaire legacy des données Lumena.

Scanne les répertoires de données existants et produit :
  - data/migration/user_migration_v1.inventory.json  (machine)
  - data/migration/user_migration_v1.report.md       (lisible)
  - data/migration/user_migration_v1.json            (manifeste d'attribution)

Toute donnée existante non attribuée est assignée à owner_user_id=local:owner.
Ce module ne modifie, déplace ni supprime aucun fichier.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

LEGACY_OWNER = "local:owner"

# Répertoires à inventorier (relatifs à data_dir / workspace_dir)
_SCAN_SUBDIRS = [
    "memory",
    "chromadb",
    "vector",
    "training_pool",
    "training_validated",
    "discord_contexts",
    "tg_contexts",
    "wa_contexts",
    "web_contexts",
    "received_documents",
    "browser_profiles",
    "journal",
    "logs",
    "ops",
    "plans",
    "alerts",
    "mail",
    "screenshots",
    "captures",
    "learning",
    "reflection",
    "autonomy",
    "installed_skills",
]

# Fichiers racine à inventorier dans data_dir
_SCAN_ROOT_FILES = [
    "journal.json",
    "MEMORY.md",
    "network_registry.json",
    "emotion_state.json",
    "emotion_history.jsonl",
    "heartbeat_state.json",
    "apis_registry.json",
]


def _count_files(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(1 for _ in path.rglob("*") if _.is_file())


def _dir_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def _fmt_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / 1024 ** 2:.1f} MB"


def run_inventory(
    data_dir: Path,
    workspace_dir: Path,
    *,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Scanne les données legacy et retourne le rapport complet.

    Ne modifie aucun fichier, quel que soit dry_run (le flag est conservé
    pour compatibilité future avec le mode --apply).
    """
    scanned_at = datetime.now().isoformat()
    entries: List[Dict[str, Any]] = []

    # ── Sous-répertoires data_dir ──────────────────────────────────────────
    for subdir in _SCAN_SUBDIRS:
        p = data_dir / subdir
        file_count = _count_files(p)
        size = _dir_size_bytes(p)
        entries.append({
            "path": str(p),
            "type": "directory",
            "exists": p.exists(),
            "file_count": file_count,
            "size_bytes": size,
            "owner_user_id": LEGACY_OWNER,
            "migration_action": "assign_owner",
        })

    # ── Fichiers racine data_dir ───────────────────────────────────────────
    for fname in _SCAN_ROOT_FILES:
        p = data_dir / fname
        entries.append({
            "path": str(p),
            "type": "file",
            "exists": p.exists(),
            "size_bytes": p.stat().st_size if p.exists() else 0,
            "owner_user_id": LEGACY_OWNER,
            "migration_action": "assign_owner",
        })

    # ── workspace_dir ──────────────────────────────────────────────────────
    ws_count = _count_files(workspace_dir)
    ws_size = _dir_size_bytes(workspace_dir)
    entries.append({
        "path": str(workspace_dir),
        "type": "directory",
        "exists": workspace_dir.exists(),
        "file_count": ws_count,
        "size_bytes": ws_size,
        "owner_user_id": LEGACY_OWNER,
        "migration_action": "assign_owner",
    })

    total_files = sum(
        e.get("file_count", 1 if e["type"] == "file" and e["exists"] else 0)
        for e in entries
    )
    total_size = sum(e["size_bytes"] for e in entries)

    return {
        "schema_version": "1.0",
        "scanned_at": scanned_at,
        "dry_run": dry_run,
        "legacy_owner": LEGACY_OWNER,
        "data_dir": str(data_dir),
        "workspace_dir": str(workspace_dir),
        "summary": {
            "total_entries": len(entries),
            "total_files": total_files,
            "total_size_bytes": total_size,
            "total_size_human": _fmt_size(total_size),
        },
        "entries": entries,
    }


def write_inventory(
    inventory: Dict[str, Any],
    migration_dir: Path,
) -> Dict[str, Path]:
    """Écrit les trois artefacts de migration dans migration_dir.

    Retourne un dict avec les chemins écrits.
    """
    migration_dir.mkdir(parents=True, exist_ok=True)

    inventory_path = migration_dir / "user_migration_v1.inventory.json"
    manifest_path = migration_dir / "user_migration_v1.json"
    report_path = migration_dir / "user_migration_v1.report.md"

    # inventory complet
    inventory_path.write_text(
        json.dumps(inventory, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # manifeste allégé (uniquement les attributions)
    manifest = {
        "schema_version": inventory["schema_version"],
        "generated_at": inventory["scanned_at"],
        "legacy_owner": inventory["legacy_owner"],
        "migration_applied": False,
        "assignments": [
            {
                "path": e["path"],
                "type": e["type"],
                "owner_user_id": e["owner_user_id"],
                "action": e["migration_action"],
            }
            for e in inventory["entries"]
            if e["exists"]
        ],
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # rapport lisible
    lines = [
        "# Rapport d'inventaire legacy Lumena — Phase -1",
        "",
        f"- Date : {inventory['scanned_at']}",
        f"- Mode : {'dry-run (aucune modification)' if inventory['dry_run'] else 'lecture seule'}",
        f"- Propriétaire assigné : `{inventory['legacy_owner']}`",
        f"- Répertoire data : `{inventory['data_dir']}`",
        f"- Répertoire workspace : `{inventory['workspace_dir']}`",
        "",
        "## Résumé",
        "",
        f"| Entrées scannées | Fichiers | Taille totale |",
        f"|---|---|---|",
        f"| {inventory['summary']['total_entries']} "
        f"| {inventory['summary']['total_files']} "
        f"| {inventory['summary']['total_size_human']} |",
        "",
        "## Détail",
        "",
        "| Chemin | Type | Existe | Fichiers | Taille | Propriétaire |",
        "|---|---|---|---|---|---|",
    ]
    for e in inventory["entries"]:
        size = _fmt_size(e["size_bytes"])
        fcount = e.get("file_count", "-") if e["type"] == "directory" else "-"
        lines.append(
            f"| `{e['path']}` | {e['type']} | {'oui' if e['exists'] else 'non'} "
            f"| {fcount} | {size} | `{e['owner_user_id']}` |"
        )

    lines += [
        "",
        "## Règle appliquée",
        "",
        "> Toute donnée existante non attribuée devient `owner_user_id=local:owner`.",
        "> Aucun fichier n'a été déplacé, modifié ou supprimé.",
        "> Pour appliquer la migration, utiliser `legacy_migration.apply(manifest_path)`.",
    ]

    report_path.write_text("\n".join(lines), encoding="utf-8")

    return {
        "inventory": inventory_path,
        "manifest": manifest_path,
        "report": report_path,
    }


def run_and_write(
    data_dir: Optional[Path] = None,
    workspace_dir: Optional[Path] = None,
    migration_dir: Optional[Path] = None,
    *,
    dry_run: bool = True,
) -> Dict[str, Any]:
    """Point d'entrée principal. Utilise les chemins Lumena par défaut si non fournis."""
    from src.utils.paths import DATA_DIR, WORKSPACE_DIR

    _data = data_dir or DATA_DIR
    _workspace = workspace_dir or WORKSPACE_DIR
    _migration = migration_dir or (_data / "migration")

    inventory = run_inventory(_data, _workspace, dry_run=dry_run)
    paths = write_inventory(inventory, _migration)
    inventory["_written_to"] = {k: str(v) for k, v in paths.items()}
    return inventory
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
