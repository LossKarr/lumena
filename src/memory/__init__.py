"""
🌟 LUMENA - Module Mémoire

Système de mémoire persistante utilisant ChromaDB et Mem0.
"""

from .chromadb_store import (
    Memory,
    ChromaMemoryStore,
    ChromaDBStore,
    LumenaMemory,
    CHROMADB_AVAILABLE
)

# Store basique (JSON) pour fallback
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, List, Optional
import json
from ..utils.persistence import atomic_write_json


class SimpleMemoryStore:
    """
    Store de mémoire basique pour LUMENA.
    Version simplifiée en JSON (fallback si ChromaDB non disponible).
    """
    
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        
        self.memories_file = data_dir / "memories.json"
        self.facts_file = data_dir / "facts.json"
        
        self.memories: List[Dict[str, Any]] = []
        self.facts: Dict[str, str] = {}
        
        self._load()
    
    def _load(self):
        """Charge les données."""
        if self.memories_file.exists():
            try:
                with open(self.memories_file, "r", encoding="utf-8") as f:
                    self.memories = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.memories = []  # fichier mémoire corrompu
        
        if self.facts_file.exists():
            try:
                with open(self.facts_file, "r", encoding="utf-8") as f:
                    self.facts = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.facts = {}
    
    def _save(self):
        """Sauvegarde les données."""
        atomic_write_json(self.memories_file, self.memories)
        atomic_write_json(self.facts_file, self.facts)
    
    def add_memory(self, content: str, memory_type: str = "episodic") -> None:
        """Ajoute un souvenir."""
        self.memories.append({
            "content": content,
            "type": memory_type,
            "timestamp": datetime.now().isoformat()
        })
        self._save()
    
    def add_fact(self, key: str, value: str) -> None:
        """Ajoute un fait (mémoire sémantique)."""
        self.facts[key] = value
        self._save()
    
    def get_fact(self, key: str) -> Optional[str]:
        """Récupère un fait."""
        return self.facts.get(key)
    
    def search_memories(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Recherche dans les souvenirs (recherche par mot-clé)."""
        results = []
        query_lower = query.lower()
        
        for memory in self.memories:
            if query_lower in memory["content"].lower():
                results.append(memory)
                if len(results) >= limit:
                    break
        
        return results


# Fonction helper pour obtenir le bon store
def get_memory_store(data_dir: Path):
    """Retourne le meilleur store de mémoire disponible."""
    if CHROMADB_AVAILABLE:
        return LumenaMemory(data_dir)
    else:
        return SimpleMemoryStore(data_dir)


__all__ = [
    "Memory",
    "ChromaMemoryStore",
    "ChromaDBStore",
    "LumenaMemory", 
    "SimpleMemoryStore",
    "get_memory_store",
    "CHROMADB_AVAILABLE"
]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
