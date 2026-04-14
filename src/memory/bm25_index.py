"""
🌟 LUMENA - Index BM25 pour Recherche Mots-Clés

Utilise l'algorithme BM25 (Best Matching 25) pour une recherche
par mots-clés efficace et pertinente.

Inspiré de Moltbot batch-* et manager-search.ts
"""

from typing import List, Tuple, Optional, Dict
from pathlib import Path
import json
import re
import threading
from loguru import logger

try:
    from rank_bm25 import BM25Okapi
    BM25_AVAILABLE = True
except ImportError:
    BM25_AVAILABLE = False
    logger.warning("rank-bm25 non installé. Installez avec: pip install rank-bm25")


class BM25Index:
    """
    Index BM25 pour recherche par mots-clés efficace.
    
    BM25 est l'algorithme standard utilisé par les moteurs de recherche
    pour le ranking basé sur les termes. Il tient compte de :
    - La fréquence des termes dans le document (TF)
    - La fréquence inverse dans le corpus (IDF)
    - La longueur du document (normalisation)
    """
    
    def __init__(self, cache_path: Optional[Path] = None):
        """
        Initialise l'index BM25.
        
        Args:
            cache_path: Chemin pour sauvegarder/charger l'index
        """
        self.cache_path = cache_path
        self.documents: List[str] = []  # Contenus tokenisés
        self.doc_ids: List[str] = []    # IDs des documents
        self.raw_contents: Dict[str, str] = {}  # ID -> contenu original
        self.bm25: Optional[BM25Okapi] = None
        self._dirty = False  # True si l'index doit être reconstruit
        
        if cache_path:
            self._try_load()
    
    def _tokenize(self, text: str) -> List[str]:
        """
        Tokenise un texte pour BM25.
        
        Extraction des mots, normalisation, suppression des mots vides.
        """
        # Normalisation: minuscules
        text = text.lower()
        
        # Extraction des mots (lettres, chiffres, underscores)
        tokens = re.findall(r'[a-zà-ÿ0-9_]+', text)
        
        # Filtrage des mots trop courts et des mots vides
        stopwords = {
            'le', 'la', 'les', 'un', 'une', 'des', 'de', 'du', 'et', 'ou',
            'que', 'qui', 'quoi', 'est', 'sont', 'a', 'à', 'au', 'aux',
            'pour', 'par', 'sur', 'dans', 'avec', 'ce', 'cette', 'ces',
            'the', 'a', 'an', 'and', 'or', 'is', 'are', 'was', 'were',
            'in', 'on', 'at', 'to', 'for', 'of', 'with', 'by'
        }
        
        return [t for t in tokens if len(t) > 2 and t not in stopwords]
    
    def add_document(self, doc_id: str, content: str):
        """
        Ajoute un document à l'index.
        
        L'index sera reconstruit à la prochaine recherche.
        
        Args:
            doc_id: Identifiant unique du document
            content: Contenu textuel du document
        """
        if doc_id in self.raw_contents:
            # Document déjà présent, mettre à jour
            idx = self.doc_ids.index(doc_id)
            self.documents[idx] = self._tokenize(content)
        else:
            # Nouveau document
            self.doc_ids.append(doc_id)
            self.documents.append(self._tokenize(content))
        
        self.raw_contents[doc_id] = content
        self._dirty = True

    # Compat legacy tests
    def add(self, doc_id: str, content: str):
        """Alias legacy pour add_document()."""
        self.add_document(doc_id, content)

    @property
    def doc_count(self) -> int:
        """Nombre de documents indexés (alias legacy)."""
        return len(self.doc_ids)
    
    def remove_document(self, doc_id: str) -> bool:
        """
        Supprime un document de l'index.
        
        Args:
            doc_id: ID du document à supprimer
            
        Returns:
            True si supprimé, False si non trouvé
        """
        if doc_id not in self.raw_contents:
            return False
        
        idx = self.doc_ids.index(doc_id)
        del self.doc_ids[idx]
        del self.documents[idx]
        del self.raw_contents[doc_id]
        self._dirty = True
        return True
    
    def rebuild(self, documents: Optional[List[Tuple[str, str]]] = None):
        """
        Reconstruit l'index BM25.
        
        Args:
            documents: Liste optionnelle de (doc_id, content) pour remplacer
        """
        if documents is not None:
            self.doc_ids = []
            self.documents = []
            self.raw_contents = {}
            for doc_id, content in documents:
                self.doc_ids.append(doc_id)
                self.documents.append(self._tokenize(content))
                self.raw_contents[doc_id] = content
        
        if not self.documents:
            self.bm25 = None
            return
        
        if BM25_AVAILABLE:
            self.bm25 = BM25Okapi(self.documents)
            self._dirty = False
            logger.debug(f"Index BM25 reconstruit avec {len(self.documents)} documents")
        else:
            logger.warning("BM25 non disponible, recherche mots-clés limitée")
    
    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """
        Recherche les documents les plus pertinents pour une requête.
        
        Args:
            query: Requête de recherche
            top_k: Nombre maximum de résultats
            
        Returns:
            Liste de (doc_id, score) triée par pertinence décroissante
        """
        if not BM25_AVAILABLE or not self.documents:
            return []
        
        # Reconstruire si nécessaire
        if self._dirty or self.bm25 is None:
            self.rebuild()
        
        if self.bm25 is None:
            return []
        
        # Tokeniser la requête
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []
        
        # Calculer les scores BM25
        scores = self.bm25.get_scores(query_tokens)
        
        # Trier par score décroissant
        # Compat: sur petits corpus BM25 peut produire des scores <= 0.
        scored_docs = [(self.doc_ids[i], float(scores[i])) for i in range(len(scores))]
        scored_docs.sort(key=lambda x: x[1], reverse=True)

        return scored_docs[:top_k]
    
    def get_content(self, doc_id: str) -> Optional[str]:
        """Récupère le contenu original d'un document."""
        return self.raw_contents.get(doc_id)
    
    def save(self) -> bool:
        """
        Sauvegarde l'index sur disque.
        
        Returns:
            True si succès
        """
        if not self.cache_path:
            return False
        
        try:
            self.cache_path.mkdir(parents=True, exist_ok=True)
            cache_file = self.cache_path / "bm25_index.json"

            data = {
                'doc_ids': self.doc_ids,
                'documents': self.documents,
                'raw_contents': self.raw_contents
            }

            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False)
            
            logger.debug(f"Index BM25 sauvegardé: {len(self.doc_ids)} documents")
            return True
            
        except Exception as e:
            logger.error(f"Erreur sauvegarde index BM25: {e}")
            return False
    
    def _try_load(self) -> bool:
        """Tente de charger l'index depuis le disque."""
        if not self.cache_path:
            return False
        
        cache_file = self.cache_path / "bm25_index.json"
        # Migration: convertir l'ancien .pkl s'il existe
        old_pkl = self.cache_path / "bm25_index.pkl"
        if old_pkl.exists() and not cache_file.exists():
            try:
                import pickle as _pkl
                with open(old_pkl, 'rb') as f:
                    _old = _pkl.load(f)
                with open(cache_file, 'w', encoding='utf-8') as f:
                    json.dump(_old, f, ensure_ascii=False)
                old_pkl.unlink()
            except Exception:
                pass
        if not cache_file.exists():
            return False

        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self.doc_ids = data.get('doc_ids', [])
            self.documents = data.get('documents', [])
            self.raw_contents = data.get('raw_contents', {})
            self._dirty = True  # Reconstruire le BM25 à la prochaine recherche
            
            logger.debug(f"Index BM25 chargé: {len(self.doc_ids)} documents")
            return True
            
        except Exception as e:
            logger.warning(f"Erreur chargement index BM25: {e}")
            return False
    
    def count(self) -> int:
        """Retourne le nombre de documents indexés."""
        return len(self.doc_ids)
    
    def clear(self):
        """Vide l'index."""
        self.doc_ids = []
        self.documents = []
        self.raw_contents = {}
        self.bm25 = None
        self._dirty = False


# Singleton global
_bm25_index: Optional[BM25Index] = None
_bm25_lock = threading.Lock()


def get_bm25_index(cache_path: Optional[Path] = None) -> BM25Index:
    """Retourne l'instance globale de l'index BM25."""
    global _bm25_index
    if _bm25_index is None:
        with _bm25_lock:
            if _bm25_index is None:
                _bm25_index = BM25Index(cache_path)
    return _bm25_index
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
