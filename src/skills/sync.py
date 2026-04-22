"""
Idempotent synchronization of skills-main into Lumena local skills.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from ..utils.persistence import atomic_write_json, safe_read_json


ORIGIN_MARKER_FILENAME = ".lumena_origin.json"


@dataclass
class SyncResult:
    synced_at: str
    source_path: str
    destination_path: str
    skills: List[str]
    updated: List[str]
    skipped: List[str]
    conflicts: List[str]
    errors: List[str]

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["status"] = "success" if not self.errors else "partial"
        payload["updated_count"] = len(self.updated)
        payload["skipped_count"] = len(self.skipped)
        payload["conflicts_count"] = len(self.conflicts)
        payload["errors_count"] = len(self.errors)
        return payload


def _default_source_path() -> Path:
    lumena_root = Path(__file__).parent.parent.parent
    return (lumena_root.parent / "skills-main" / "skills").resolve()


def _default_destination_path() -> Path:
    lumena_root = Path(__file__).parent.parent.parent
    return (lumena_root / "skills").resolve()


def _default_manifest_path() -> Path:
    from src.utils.paths import SKILLS_MANIFEST_JSON
    return SKILLS_MANIFEST_JSON


def _read_origin_marker(skill_dir: Path) -> Dict[str, Any]:
    marker_path = skill_dir / ORIGIN_MARKER_FILENAME
    return safe_read_json(marker_path, default={})


def _write_origin_marker(skill_dir: Path, source_skill_dir: Path) -> None:
    marker_path = skill_dir / ORIGIN_MARKER_FILENAME
    payload = {
        "origin": "skills-main",
        "source_path": str(source_skill_dir.resolve()),
        "synced_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(marker_path, payload)


def _is_upstream_managed(existing_dir: Path, source_skill_dir: Path) -> bool:
    marker = _read_origin_marker(existing_dir)
    if not marker:
        return False
    if marker.get("origin") != "skills-main":
        return False
    marker_source = str(marker.get("source_path", "")).strip()
    if not marker_source:
        return True
    return marker_source == str(source_skill_dir.resolve())


def sync_skills_main(
    source_path: Optional[Path] = None,
    destination_path: Optional[Path] = None,
    manifest_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """
    Sync skills from skills-main/skills to lumena/skills.

    Collision policy:
    - If destination skill exists and has upstream marker => update allowed.
    - If destination skill exists without marker => skip and register conflict.
    """

    source = Path(source_path).resolve() if source_path else _default_source_path()
    destination = Path(destination_path).resolve() if destination_path else _default_destination_path()
    manifest_file = Path(manifest_path).resolve() if manifest_path else _default_manifest_path()

    result = SyncResult(
        synced_at=datetime.now(timezone.utc).isoformat(),
        source_path=str(source),
        destination_path=str(destination),
        skills=[],
        updated=[],
        skipped=[],
        conflicts=[],
        errors=[],
    )

    if not source.exists() or not source.is_dir():
        result.errors.append(f"source_not_found:{source}")
        payload = result.to_dict()
        _write_manifest(manifest_file, payload)
        logger.error("Skills sync failed: source not found {}", source)
        return payload

    destination.mkdir(parents=True, exist_ok=True)
    source_skill_dirs = sorted([p for p in source.iterdir() if p.is_dir()], key=lambda p: p.name.lower())
    result.skills = [p.name for p in source_skill_dirs]

    for source_skill_dir in source_skill_dirs:
        skill_name = source_skill_dir.name
        target_skill_dir = destination / skill_name

        try:
            if target_skill_dir.exists():
                if _is_upstream_managed(target_skill_dir, source_skill_dir):
                    shutil.rmtree(target_skill_dir)
                else:
                    result.conflicts.append(skill_name)
                    result.skipped.append(skill_name)
                    logger.warning("Skills sync conflict on '{}': local skill preserved", skill_name)
                    continue

            shutil.copytree(source_skill_dir, target_skill_dir)
            _write_origin_marker(target_skill_dir, source_skill_dir)
            result.updated.append(skill_name)
            logger.info("Skill synced: {}", skill_name)
        except Exception as e:
            result.errors.append(f"{skill_name}:{e}")
            logger.error("Skill sync error on '{}': {}", skill_name, e)

    payload = result.to_dict()
    _write_manifest(manifest_file, payload)
    logger.info(
        "Skills sync completed: updated={} skipped={} conflicts={} errors={}",
        payload["updated_count"],
        payload["skipped_count"],
        payload["conflicts_count"],
        payload["errors_count"],
    )
    return payload


def _write_manifest(manifest_file: Path, payload: Dict[str, Any]) -> None:
    manifest_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(manifest_file, payload)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
