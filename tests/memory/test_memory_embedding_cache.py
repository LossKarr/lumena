"""Tests unitaires pour src/memory/embedding_cache.py"""
import json
import pytest
import tempfile
from pathlib import Path

from src.memory.embedding_cache import EmbeddingCache


@pytest.fixture
def cache(tmp_path):
    return EmbeddingCache(tmp_path)


class TestEmbeddingCacheInit:
    def test_db_created(self, tmp_path):
        c = EmbeddingCache(tmp_path)
        assert (tmp_path / "embedding_cache.db").exists()

    def test_conn_available(self, cache):
        assert cache.conn is not None


class TestEmbeddingCacheHashText:
    def test_same_text_same_hash(self, cache):
        h1 = cache._hash_text("hello", "model-a")
        h2 = cache._hash_text("hello", "model-a")
        assert h1 == h2

    def test_different_model_different_hash(self, cache):
        h1 = cache._hash_text("hello", "model-a")
        h2 = cache._hash_text("hello", "model-b")
        assert h1 != h2

    def test_different_text_different_hash(self, cache):
        h1 = cache._hash_text("hello", "model")
        h2 = cache._hash_text("world", "model")
        assert h1 != h2


class TestEmbeddingCacheGetSet:
    def test_miss_returns_none(self, cache):
        result = cache.get("never stored", "model")
        assert result is None

    def test_set_then_get(self, cache):
        embedding = [0.1, 0.2, 0.3]
        cache.set("hello world", "embed-v1", embedding)
        result = cache.get("hello world", "embed-v1")
        assert result == embedding

    def test_set_wrong_model_returns_none(self, cache):
        cache.set("hello", "model-a", [0.5, 0.6])
        result = cache.get("hello", "model-b")
        assert result is None

    def test_overwrite_embedding(self, cache):
        cache.set("text", "model", [1.0])
        cache.set("text", "model", [2.0])
        result = cache.get("text", "model")
        assert result == [2.0]


class TestEmbeddingCacheStats:
    def test_get_stats_structure(self, cache):
        stats = cache.get_stats()
        assert isinstance(stats, dict)
        assert "total_entries" in stats

    def test_stats_after_set(self, cache):
        cache.set("a", "m", [1.0])
        stats = cache.get_stats()
        assert stats["total_entries"] >= 1

    def test_clear_empties_cache(self, cache):
        cache.set("x", "m", [0.9])
        cache.clear()
        # After clear, get should return None
        assert cache.get("x", "m") is None
