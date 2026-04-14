from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.memory.chromadb_store import CHROMADB_AVAILABLE, ChromaMemoryStore
from src.memory.migration import migrate_legacy_vector_to_canonical


@pytest.mark.skipif(not CHROMADB_AVAILABLE, reason="ChromaDB not available")
def test_memory_migration_imports_legacy_only_and_is_idempotent(tmp_path: Path):
    data_dir = tmp_path / "data"
    legacy_dir = data_dir / "vector"
    canonical_vector_dir = data_dir / "memory" / "vector"

    legacy_store = ChromaMemoryStore(legacy_dir)
    canonical_store = ChromaMemoryStore(canonical_vector_dir)

    assert legacy_store.collection is not None
    assert canonical_store.collection is not None

    # Duplicate across legacy/canonical.
    assert legacy_store.add("Bonjour Alice", memory_type="episodic", importance=0.5)
    assert canonical_store.add("Bonjour Alice", memory_type="episodic", importance=0.5)

    # Unique legacy memory expected to be migrated.
    assert legacy_store.add("Souvenir uniquement legacy", memory_type="semantic", importance=0.7)

    canonical_before = canonical_store.count()
    result_first = migrate_legacy_vector_to_canonical(data_dir)

    assert result_first["status"] == "success"
    assert result_first["legacy_count"] >= 2
    assert result_first["inserted"] == 1
    assert result_first["canonical_before"] == canonical_before

    canonical_after_store = ChromaMemoryStore(canonical_vector_dir)
    canonical_after = canonical_after_store.count()
    assert canonical_after == canonical_before + 1
    assert result_first["canonical_after"] == canonical_after

    all_meta = canonical_after_store.collection.get(include=["metadatas"]).get("metadatas", [])
    migrated_rows = [m for m in all_meta if isinstance(m, dict) and m.get("migrated_from") == "data/vector"]
    assert len(migrated_rows) >= 1

    result_second = migrate_legacy_vector_to_canonical(data_dir)
    assert result_second["status"] == "skipped"
    assert result_second["reason"] == "already_migrated"
    assert result_second["canonical_after"] == canonical_after


def test_memory_migration_missing_legacy_or_chromadb_returns_skip(tmp_path: Path):
    data_dir = tmp_path / "data"
    result = migrate_legacy_vector_to_canonical(data_dir)

    if CHROMADB_AVAILABLE:
        assert result["status"] == "skipped"
        assert result["reason"] == "legacy_db_missing"
    else:
        assert result["status"] == "skipped"
        assert result["reason"] == "chromadb_unavailable"
