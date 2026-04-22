"""Memory migration helpers for Lumena canonical vector store."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger
from ..utils.persistence import atomic_write_json

from .chromadb_store import CHROMADB_AVAILABLE, ChromaMemoryStore


MIGRATION_MARKER = ".legacy_vector_migration.json"


def _normalize_content(content: str) -> str:
    text = content or ""
    text = re.sub(r"\s+", " ", text).strip().lower()
    return text


def _content_hash(content: str) -> str:
    normalized = _normalize_content(content)
    return hashlib.md5(normalized.encode("utf-8")).hexdigest()


def _read_marker(marker_path: Path) -> Dict[str, Any]:
    if not marker_path.exists():
        return {}
    try:
        return json.loads(marker_path.read_text(encoding="utf-8"))
    except Exception:
        return {}  # marker illisible


def _write_marker(marker_path: Path, payload: Dict[str, Any]) -> None:
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(marker_path, payload)


def _legacy_fingerprint(legacy_db_file: Path, legacy_count: int) -> Dict[str, Any]:
    stat = legacy_db_file.stat() if legacy_db_file.exists() else None
    return {
        "legacy_count": int(legacy_count),
        "db_size": int(stat.st_size) if stat else 0,
        "db_mtime": float(stat.st_mtime) if stat else 0.0,
        "db_mtime_ns": int(stat.st_mtime_ns) if stat else 0,
    }


def _safe_float(value: Any, default: float = 0.5) -> float:
    try:
        return float(value)
    except Exception:
        return default  # conversion float échouée


def _matches_legacy_fingerprint(
    marker_fingerprint: Any,
    legacy_stat: Any,
    legacy_count: Optional[int] = None,
) -> bool:
    if not isinstance(marker_fingerprint, dict) or legacy_stat is None:
        return False

    try:
        marker_size = int(marker_fingerprint.get("db_size", -1))
    except Exception:
        return False  # comparaison taille échouée
    if marker_size != int(legacy_stat.st_size):
        return False

    if legacy_count is not None and "legacy_count" in marker_fingerprint:
        try:
            if int(marker_fingerprint.get("legacy_count")) != int(legacy_count):
                return False
        except Exception:
            return False  # comparaison count échouée

    marker_mtime_ns = marker_fingerprint.get("db_mtime_ns")
    if marker_mtime_ns is not None:
        try:
            return int(marker_mtime_ns) == int(legacy_stat.st_mtime_ns)
        except Exception:
            return False  # comparaison mtime_ns échouée

    # Backward compatibility for older markers storing float mtime only.
    marker_mtime = marker_fingerprint.get("db_mtime")
    if marker_mtime is None:
        return False
    try:
        return abs(float(marker_mtime) - float(legacy_stat.st_mtime)) <= 1e-3
    except Exception:
        return False  # comparaison mtime échouée


def _load_all(store: ChromaMemoryStore) -> Dict[str, List[Any]]:
    if not store.collection:
        return {"ids": [], "documents": [], "metadatas": []}
    try:
        data = store.collection.get(include=["documents", "metadatas"])
    except Exception:
        return {"ids": [], "documents": [], "metadatas": []}  # collection get échouée
    return {
        "ids": list(data.get("ids") or []),
        "documents": list(data.get("documents") or []),
        "metadatas": list(data.get("metadatas") or []),
    }


def migrate_legacy_vector_to_canonical(data_dir: Path) -> Dict[str, Any]:
    """Migrate data/vector memories into data/memory/vector (idempotent)."""
    result: Dict[str, Any] = {
        "status": "skipped",
        "reason": None,
        "legacy_count": 0,
        "canonical_before": 0,
        "canonical_after": 0,
        "inserted": 0,
        "skipped": 0,
        "marker_path": str(data_dir / "memory" / MIGRATION_MARKER),
    }

    if not CHROMADB_AVAILABLE:
        result["reason"] = "chromadb_unavailable"
        return result

    legacy_vector_dir = data_dir / "vector"
    canonical_memory_dir = data_dir / "memory"
    canonical_vector_dir = canonical_memory_dir / "vector"
    marker_path = canonical_memory_dir / MIGRATION_MARKER

    legacy_db_file = legacy_vector_dir / "chromadb" / "chroma.sqlite3"
    canonical_db_file = canonical_vector_dir / "chromadb" / "chroma.sqlite3"
    if not legacy_db_file.exists():
        result["reason"] = "legacy_db_missing"
        return result

    # Fast path: if marker and DB fingerprint are unchanged, skip migration without
    # instantiating Chroma stores (avoids noisy/expensive double init at startup).
    marker = _read_marker(marker_path)
    legacy_stat = legacy_db_file.stat()
    if (
        marker.get("status") == "success"
        and canonical_db_file.exists()
        and _matches_legacy_fingerprint(marker.get("legacy_fingerprint"), legacy_stat)
    ):
        result["status"] = "skipped"
        result["reason"] = "already_migrated"
        result["legacy_count"] = int(marker.get("legacy_count", 0))
        result["canonical_before"] = int(marker.get("canonical_after", marker.get("canonical_before", 0)))
        result["canonical_after"] = int(marker.get("canonical_after", marker.get("canonical_before", 0)))
        return result

    legacy_store = ChromaMemoryStore(legacy_vector_dir)
    canonical_store = ChromaMemoryStore(canonical_vector_dir)
    if not legacy_store.collection or not canonical_store.collection:
        result["status"] = "error"
        result["reason"] = "store_init_failed"
        return result

    legacy_count = legacy_store.count()
    canonical_before = canonical_store.count()
    result["legacy_count"] = int(legacy_count)
    result["canonical_before"] = int(canonical_before)

    fingerprint = _legacy_fingerprint(legacy_db_file, legacy_count)
    marker = _read_marker(marker_path)
    if marker.get("status") == "success" and _matches_legacy_fingerprint(
        marker.get("legacy_fingerprint"),
        legacy_stat,
        legacy_count=legacy_count,
    ):
        result["status"] = "skipped"
        result["reason"] = "already_migrated"
        result["canonical_after"] = canonical_before
        return result

    legacy_data = _load_all(legacy_store)
    canonical_data = _load_all(canonical_store)

    canonical_hashes = {
        _content_hash(doc)
        for doc in canonical_data.get("documents", [])
        if isinstance(doc, str) and doc.strip()
    }

    inserted = 0
    skipped = 0
    now_iso = datetime.now().isoformat()
    ids = legacy_data.get("ids", [])
    documents = legacy_data.get("documents", [])
    metadatas = legacy_data.get("metadatas", [])

    for idx, content in enumerate(documents):
        if not isinstance(content, str) or not content.strip():
            skipped += 1
            continue

        doc_hash = _content_hash(content)
        if doc_hash in canonical_hashes:
            skipped += 1
            continue

        metadata = {}
        if idx < len(metadatas) and isinstance(metadatas[idx], dict):
            metadata.update(metadatas[idx])

        legacy_id = ids[idx] if idx < len(ids) else None
        metadata.update(
            {
                "migrated_from": "data/vector",
                "migrated_at": now_iso,
                "legacy_memory_id": legacy_id,
            }
        )

        memory_type = str(metadata.get("type") or metadata.get("memory_type") or "episodic")
        importance = _safe_float(metadata.get("importance"), default=0.5)
        created_id = canonical_store.add(
            content=content,
            memory_type=memory_type,
            importance=importance,
            metadata=metadata,
        )
        if created_id:
            inserted += 1
            canonical_hashes.add(doc_hash)
        else:
            skipped += 1

    canonical_after = canonical_store.count()
    result.update(
        {
            "status": "success",
            "legacy_count": int(legacy_count),
            "canonical_before": int(canonical_before),
            "canonical_after": int(canonical_after),
            "inserted": int(inserted),
            "skipped": int(skipped),
            "legacy_fingerprint": fingerprint,
            "migrated_at": datetime.now().isoformat(),
        }
    )

    _write_marker(
        marker_path,
        {
            "status": "success",
            "legacy_fingerprint": fingerprint,
            "legacy_count": int(legacy_count),
            "canonical_before": int(canonical_before),
            "canonical_after": int(canonical_after),
            "inserted": int(inserted),
            "skipped": int(skipped),
            "migrated_at": datetime.now().isoformat(),
        },
    )

    logger.success(
        "Memory migration complete: legacy={} canonical_before={} inserted={} skipped={} canonical_after={}",
        legacy_count,
        canonical_before,
        inserted,
        skipped,
        canonical_after,
    )
    return result
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
