"""
🧪 Tests: Memory System
Tests unitaires pour le système de mémoire LUMENA
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))


class TestMemoryImport:
    """Tests d'import du système de mémoire."""
    
    def test_import_chromadb_store(self):
        """Vérifie l'import de ChromaDB store."""
        from src.memory.chromadb_store import LumenaMemory
        assert LumenaMemory is not None
    
    def test_import_bm25_index(self):
        """Vérifie l'import de l'index BM25."""
        from src.memory.bm25_index import BM25Index
        assert BM25Index is not None


class TestBM25Index:
    """Tests de l'index BM25."""
    
    def test_create_index(self):
        """Vérifie la création d'un index."""
        from src.memory.bm25_index import BM25Index
        index = BM25Index()
        assert index is not None
    
    def test_add_document(self):
        """Test l'ajout d'un document."""
        from src.memory.bm25_index import BM25Index
        index = BM25Index()
        index.add("doc1", "Ceci est un test de document")
        assert index.doc_count > 0
    
    def test_search_document(self):
        """Test la recherche dans l'index."""
        from src.memory.bm25_index import BM25Index
        index = BM25Index()
        index.add("doc1", "Python est un langage de programmation")
        index.add("doc2", "JavaScript est aussi populaire")
        index.rebuild()
        
        results = index.search("Python programmation", top_k=2)
        assert len(results) > 0
        assert results[0][0] == "doc1"  # Le premier résultat doit être doc1


class TestMemoryIntegration:
    """Tests d'intégration de la mémoire."""
    
    def test_memory_initialization(self):
        """Vérifie l'initialisation de la mémoire."""
        from src.memory.chromadb_store import LumenaMemory
        memory = LumenaMemory()
        assert memory is not None
    
    def test_memory_stats(self):
        """Vérifie les stats de la mémoire."""
        from src.memory.chromadb_store import LumenaMemory
        memory = LumenaMemory()
        stats = memory.get_stats()
        assert "total_memories" in stats
