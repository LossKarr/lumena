"""
🧪 Test de déduplication mémoire par hash (Phase 5.2)

Vérifie que la déduplication utilise SHA256 et non hash().
"""

import pytest


class TestMemoryDedupHash:
    """Tests de déduplication mémoire."""
    
    def test_chromadb_uses_sha256(self):
        """Vérifie que ChromaDB utilise SHA256 pour la déduplication."""
        import inspect
        from src.memory.chromadb_store import ChromaDBStore
        
        # Vérifier le code source
        source = inspect.getsource(ChromaDBStore._compute_hash)
        
        assert "sha256" in source.lower(), "SHA256 non utilisé pour le hash"
        assert "hashlib" in source, "hashlib non importé"
    
    def test_hash_is_deterministic(self):
        """Vérifie que le hash est déterministe."""
        from src.memory.chromadb_store import ChromaDBStore
        
        store = ChromaDBStore.__new__(ChromaDBStore)
        
        text = "Ceci est un test de déduplication"
        
        hash1 = store._compute_hash(text)
        hash2 = store._compute_hash(text)
        
        assert hash1 == hash2, "Hash non déterministe"
    
    def test_different_texts_have_different_hashes(self):
        """Vérifie que des textes différents ont des hashes différents."""
        from src.memory.chromadb_store import ChromaDBStore
        
        store = ChromaDBStore.__new__(ChromaDBStore)
        
        text1 = "Premier texte"
        text2 = "Deuxième texte"
        
        hash1 = store._compute_hash(text1)
        hash2 = store._compute_hash(text2)
        
        assert hash1 != hash2, "Textes différents mais même hash"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
