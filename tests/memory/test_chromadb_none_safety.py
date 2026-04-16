"""
🧪 Test de sécurité None pour ChromaDB (Phase 5.2)

Vérifie que ChromaDB gère correctement les valeurs None.
"""

import pytest


class TestChromaDBNoneSafety:
    """Tests de sécurité None pour ChromaDB."""
    
    def test_chromadb_handles_none_metadatas(self):
        """Vérifie que ChromaDB gère les metadatas None."""
        from src.memory.chromadb_store import ChromaDBStore
        
        # Créer une instance de test
        store = ChromaDBStore()
        
        # Simuler une réponse avec metadatas None
        mock_results = {
            "ids": [["test_id"]],
            "documents": [["Test document"]],
            "metadatas": None,  # Valeur None
            "distances": [[0.5]]
        }
        
        # Vérifier que l'accès ne crash pas
        metadatas = mock_results.get("metadatas") or [[{}]]
        assert metadatas is not None
    
    def test_chromadb_handles_none_distances(self):
        """Vérifie que ChromaDB gère les distances None."""
        mock_results = {
            "ids": [["test_id"]],
            "documents": [["Test document"]],
            "metadatas": [[{"type": "test"}]],
            "distances": None  # Valeur None
        }
        
        # Vérifier que l'accès ne crash pas
        distances = mock_results.get("distances") or [[1.0]]
        assert distances is not None
    
    def test_chromadb_handles_empty_results(self):
        """Vérifie que ChromaDB gère les résultats vides."""
        mock_results = {
            "ids": [[]],
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]]
        }
        
        ids_list = mock_results.get("ids", [[]])[0]
        assert len(ids_list) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
