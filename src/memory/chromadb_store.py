"""
🌟 LUMENA - Mémoire Vectorielle avec ChromaDB

Système de mémoire persistante et sémantique pour LUMENA.
Utilise ChromaDB pour la recherche vectorielle + recherche hybride.

Fonctionnalités:
- Recherche hybride (vecteur + mots-clés BM25)
- Fusion intelligente avec RRF (Reciprocal Rank Fusion)
- Cache d'embeddings intégré
"""

import os
import re
import hashlib
from typing import Dict, Any, List, Optional, Tuple
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass, field
import json
from loguru import logger

# Optional: filelock for thread-safe facts I/O
try:
    from filelock import FileLock
    FILELOCK_AVAILABLE = True
except ImportError:
    FILELOCK_AVAILABLE = False
    logger.debug("filelock non installé - pip install filelock pour accès fichier thread-safe")

# Vérifier ChromaDB
try:
    import chromadb
    from chromadb.config import Settings
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    logger.warning("ChromaDB non installé. Installez avec: pip install chromadb")

# Import BM25 Index
try:
    from .bm25_index import BM25Index
    BM25_AVAILABLE = True
except ImportError:
    BM25_AVAILABLE = False
    logger.debug("BM25Index non disponible")


@dataclass
class Memory:
    """Représente un souvenir."""
    id: str
    content: str
    memory_type: str  # episodic, semantic, procedural
    timestamp: datetime
    metadata: Dict[str, Any] = field(default_factory=dict)
    importance: float = 0.5  # 0.0 à 1.0
    score: float = 0.0  # Score de pertinence (pour la recherche)
    vector_score: float = 0.0  # Score vectoriel
    keyword_score: float = 0.0  # Score mots-clés


class ChromaMemoryStore:
    """
    Store de mémoire vectorielle avec ChromaDB.
    
    Permet de stocker et rechercher des souvenirs de manière sémantique.
    """
    
    COLLECTION_NAME = "lumena_memories"
    
    def __init__(self, data_dir: Path, user_id: str = "local:owner"):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.user_id = user_id

        self.client = None
        self.collection = None
        
        # Index BM25 pour recherche mots-clés
        self.bm25_index = None
        if BM25_AVAILABLE:
            self.bm25_index = BM25Index(data_dir / "bm25_cache")
        
        if CHROMADB_AVAILABLE:
            self._init_chromadb()
            # Synchroniser l'index BM25 avec les documents existants
            if self.bm25_index:
                self._sync_bm25_index()

    @staticmethod
    def _matches_where(meta: Dict[str, Any], where: Dict[str, Any]) -> bool:
        """Vérifie qu'une entrée metadata satisfait un filtre where style ChromaDB.

        Gère les structures simples {"key": value} et composées {"$and": [...]}.
        """
        if not where:
            return True
        if "$and" in where:
            return all(ChromaMemoryStore._matches_where_single(meta, cond) for cond in where["$and"])
        return ChromaMemoryStore._matches_where_single(meta, where)

    @staticmethod
    def _matches_where_single(meta: Dict[str, Any], condition: Dict[str, Any]) -> bool:
        for key, val in condition.items():
            if key.startswith("$"):
                continue
            actual = meta.get(key)
            if isinstance(val, dict):
                for op, operand in val.items():
                    if op == "$gte" and not (actual is not None and actual >= operand):
                        return False
                    elif op == "$lte" and not (actual is not None and actual <= operand):
                        return False
                    elif op == "$eq" and actual != operand:
                        return False
                    elif op == "$ne" and actual == operand:
                        return False
            else:
                if actual != val:
                    return False
        return True

    @staticmethod
    def _compute_hash(text: str) -> str:
        """Hash déterministe pour déduplication (compat legacy tests)."""
        normalized = (text or "").strip().lower()
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]

    def _build_where(
        self,
        *,
        memory_type: Optional[str] = None,
        min_importance: float = 0.0,
    ) -> Dict[str, Any]:
        """Construit le filtre ChromaDB en incluant toujours user_id."""
        conditions: List[Dict[str, Any]] = [{"user_id": self.user_id}]
        if memory_type:
            conditions.append({"type": memory_type})
        if min_importance > 0:
            conditions.append({"importance": {"$gte": min_importance}})
        if len(conditions) == 1:
            return conditions[0]
        return {"$and": conditions}
    
    def _init_chromadb(self):
        """Initialise ChromaDB."""
        try:
            # Configuration persistante
            self.client = chromadb.PersistentClient(
                path=str(self.data_dir / "chromadb")
            )
            
            # Créer ou récupérer la collection
            self.collection = self.client.get_or_create_collection(
                name=self.COLLECTION_NAME,
                metadata={"description": "Mémoires de LUMENA"}
            )
            
            logger.info(f"ChromaDB initialisé avec {self.collection.count()} mémoires")
            
        except Exception as e:
            logger.error(f"Erreur initialisation ChromaDB: {e}")
            self.client = None
            self.collection = None
    
    def add(
        self, 
        content: str, 
        memory_type: str = "episodic",
        importance: float = 0.5,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[str]:
        """
        Ajoute un souvenir.
        
        Args:
            content: Contenu du souvenir
            memory_type: Type (episodic, semantic, procedural)
            importance: Importance (0.0-1.0)
            metadata: Métadonnées additionnelles
            
        Returns:
            ID du souvenir créé
        """
        if not self.collection:
            logger.warning("ChromaDB non disponible")
            return None
        
        try:
            # Générer un ID unique
            memory_id = f"mem_{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
            
            # Préparer les métadonnées
            meta = metadata or {}
            meta.update({
                "type": memory_type,
                "importance": importance,
                "timestamp": datetime.now().isoformat(),
                "user_id": self.user_id,
            })
            
            # Ajouter à ChromaDB
            self.collection.add(
                ids=[memory_id],
                documents=[content],
                metadatas=[meta]
            )
            
            # Ajouter à l'index BM25
            if self.bm25_index:
                self.bm25_index.add_document(memory_id, content)
            
            logger.debug(f"Mémoire ajoutée: {memory_id}")
            return memory_id
            
        except Exception as e:
            logger.error(f"Erreur ajout mémoire: {e}")
            return None
    
    def batch_add(
        self,
        items: List[Dict[str, Any]],
        deduplicate: bool = True
    ) -> List[str]:
        """
        Ajoute plusieurs souvenirs en lot (batch embeddings).
        
        Args:
            items: Liste de dicts avec 'content', 'type', 'importance', 'metadata'
            deduplicate: Si True, évite les doublons proches
            
        Returns:
            Liste des IDs créés
        """
        if not self.collection:
            logger.warning("ChromaDB non disponible")
            return []
        
        created_ids = []
        
        try:
            for item in items:
                content = item.get("content", "")
                
                # Vérifier les doublons si demandé
                if deduplicate and self._is_duplicate(content):
                    logger.debug(f"Doublon ignoré: {content[:50]}...")
                    continue
                
                memory_id = self.add(
                    content=content,
                    memory_type=item.get("type", "episodic"),
                    importance=item.get("importance", 0.5),
                    metadata=item.get("metadata")
                )
                
                if memory_id:
                    created_ids.append(memory_id)
            
            logger.info(f"📦 Batch: {len(created_ids)}/{len(items)} mémoires ajoutées")
            return created_ids
            
        except Exception as e:
            logger.error(f"Erreur batch_add: {e}")
            return created_ids
    
    def _is_duplicate(self, content: str, threshold: float = 0.95) -> bool:
        """
        Vérifie si un contenu est un doublon via similarité vectorielle.
        
        Args:
            content: Contenu à vérifier
            threshold: Seuil de similarité (0.95 = très similaire)
            
        Returns:
            True si doublon détecté
        """
        if not self.collection or self.collection.count() == 0:
            return False

        try:
            results = self.collection.query(
                query_texts=[content],
                n_results=1,
                where=self._build_where(),
            )
            
            if results and results.get("distances"):
                distances = results["distances"][0]
                if distances:
                    # ChromaDB retourne des distances, pas des similarités
                    # Distance proche de 0 = très similaire
                    similarity = 1 - distances[0]
                    return similarity >= threshold
            
            return False
            
        except Exception:
            return False  # collection non accessible
    
    def deduplicate(self) -> int:
        """
        Supprime les mémoires dupliquées.
        
        Returns:
            Nombre de doublons supprimés
        """
        if not self.collection:
            return 0
        
        try:
            # Récupérer toutes les mémoires de cet utilisateur
            all_data = self.collection.get(where=self._build_where())
            if not all_data or not all_data.get("ids"):
                return 0
            
            ids = all_data["ids"]
            documents = all_data.get("documents", [])
            
            seen_hashes = set()
            to_delete = []
            
            for i, doc in enumerate(documents):
                # Phase 3.5: Hash SHA256 pour détection fiable (au lieu de hash())
                doc_hash = self._compute_hash(doc or "")
                
                if doc_hash in seen_hashes:
                    to_delete.append(ids[i])
                else:
                    seen_hashes.add(doc_hash)
            
            # Supprimer les doublons
            if to_delete:
                self.collection.delete(ids=to_delete)
                logger.info(f"🗑️ {len(to_delete)} doublons supprimés")
            
            return len(to_delete)
            
        except Exception as e:
            logger.error(f"Erreur deduplicate: {e}")
            return 0
    
    def search(
        self, 
        query: str, 
        limit: int = 5,
        memory_type: Optional[str] = None,
        min_importance: float = 0.0,
        hybrid: bool = True,
        vector_weight: float = 0.7,
        keyword_weight: float = 0.3
    ) -> List[Memory]:
        """
        Recherche des souvenirs similaires avec recherche HYBRIDE.
        
        Combine recherche vectorielle (sémantique) et mots-clés (exacte)
        pour des résultats plus précis.
        
        Args:
            query: Requête de recherche
            limit: Nombre maximum de résultats
            memory_type: Filtrer par type
            min_importance: Importance minimale
            hybrid: Utiliser la recherche hybride (défaut: True)
            vector_weight: Poids de la recherche vectorielle (0.0-1.0)
            keyword_weight: Poids de la recherche par mots-clés (0.0-1.0)
            
        Returns:
            Liste de souvenirs pertinents triés par score
        """
        if not self.collection:
            return []
        
        try:
            where = self._build_where(
                memory_type=memory_type,
                min_importance=min_importance,
            )

            # Recherche vectorielle (sémantique)
            vector_results = self._search_vector(query, limit * 2, where)
            
            if hybrid:
                # Recherche par mots-clés
                keyword_results = self._search_keywords(query, limit * 2, where)
                
                # Fusion hybride
                memories = self._merge_hybrid_results(
                    vector_results, 
                    keyword_results,
                    vector_weight,
                    keyword_weight
                )
            else:
                memories = vector_results
            
            # Trier par score et limiter
            memories.sort(key=lambda m: m.score, reverse=True)
            return memories[:limit]
            
        except Exception as e:
            logger.error(f"Erreur recherche mémoire: {e}")
            return []
    
    def _search_vector(self, query: str, limit: int, where: Optional[Dict] = None) -> List[Memory]:
        """Recherche vectorielle pure (sémantique)."""
        if not self.collection:
            return []
        
        try:
            results = self.collection.query(
                query_texts=[query],
                n_results=limit,
                where=where,
                include=["documents", "metadatas", "distances"]
            )
            
            memories = []
            ids_list = results.get("ids", [[]])[0] if results.get("ids") else []
            documents_list = results.get("documents", [[]])[0] if results.get("documents") else []
            metadatas_list = results.get("metadatas", [[]])[0] if results.get("metadatas") else []
            distances_list = results.get("distances", [[]])[0] if results.get("distances") else []
            
            for i, mem_id in enumerate(ids_list):
                # Phase 3.6: None-safety pour tous les accès
                meta = metadatas_list[i] if i < len(metadatas_list) else {}
                content = documents_list[i] if i < len(documents_list) else ""
                distance = distances_list[i] if i < len(distances_list) else 1.0
                
                # Convertir distance en score (plus petit = meilleur)
                vector_score = 1.0 / (1.0 + distance)  # Normaliser entre 0 et 1
                
                memories.append(Memory(
                    id=mem_id,
                    content=content,
                    memory_type=meta.get("type", "unknown") if meta else "unknown",
                    timestamp=datetime.fromisoformat(meta.get("timestamp", datetime.now().isoformat()) if meta else datetime.now().isoformat()),
                    metadata=meta or {},
                    importance=meta.get("importance", 0.5) if meta else 0.5,
                    score=vector_score,
                    vector_score=vector_score,
                    keyword_score=0.0
                ))
            
            return memories
        except Exception as e:
            logger.error(f"Erreur recherche vectorielle: {e}")
            return []
    
    def _sync_bm25_index(self):
        """Synchronise l'index BM25 avec les documents ChromaDB existants."""
        if not self.bm25_index or not self.collection:
            return
        
        try:
            # Récupérer tous les documents
            all_docs = self.collection.get(include=["documents"])
            
            if all_docs["ids"]:
                documents = [
                    (doc_id, content) 
                    for doc_id, content in zip(all_docs["ids"], all_docs["documents"])
                ]
                self.bm25_index.rebuild(documents)
                logger.info(f"Index BM25 synchronisé: {len(documents)} documents")
        except Exception as e:
            logger.error(f"Erreur synchronisation BM25: {e}")
    
    def _search_keywords(self, query: str, limit: int, where: Optional[Dict] = None) -> List[Memory]:
        """
        Recherche par mots-clés avec BM25.
        
        Utilise l'algorithme BM25 pour une recherche plus pertinente
        que la simple correspondance de sous-chaînes.
        """
        # Si BM25 disponible, l'utiliser en priorité
        if self.bm25_index and self.bm25_index.count() > 0:
            return self._search_bm25(query, limit, where)
        
        # Fallback: recherche basique
        return self._search_keywords_fallback(query, limit, where)
    
    def _search_bm25(self, query: str, limit: int, where: Optional[Dict] = None) -> List[Memory]:
        """Recherche avec l'index BM25."""
        if not self.bm25_index or not self.collection:
            return []
        
        try:
            # Recherche BM25
            bm25_results = self.bm25_index.search(query, limit * 2)
            
            if not bm25_results:
                return []
            
            # Récupérer les métadonnées depuis ChromaDB
            doc_ids = [doc_id for doc_id, _ in bm25_results]
            scores_by_id = {doc_id: score for doc_id, score in bm25_results}
            
            # Normaliser les scores BM25 (entre 0 et 1)
            max_score = max(scores_by_id.values()) if scores_by_id else 1.0
            
            # Récupérer les documents correspondants
            docs = self.collection.get(
                ids=doc_ids,
                include=["documents", "metadatas"]
            )
            
            memories = []
            for i, mem_id in enumerate(docs["ids"]):
                meta = docs["metadatas"][i] if docs["metadatas"] else {}

                # Filtre where complet (user_id + type + importance + $and)
                if where and not self._matches_where(meta, where):
                    continue
                
                # Score normalisé
                raw_score = scores_by_id.get(mem_id, 0)
                keyword_score = raw_score / max_score if max_score > 0 else 0
                
                memories.append(Memory(
                    id=mem_id,
                    content=docs["documents"][i],
                    memory_type=meta.get("type", "unknown"),
                    timestamp=datetime.fromisoformat(meta.get("timestamp", datetime.now().isoformat())),
                    metadata=meta,
                    importance=meta.get("importance", 0.5),
                    score=keyword_score,
                    vector_score=0.0,
                    keyword_score=keyword_score
                ))
            
            # Trier par score BM25
            memories.sort(key=lambda m: m.keyword_score, reverse=True)
            return memories[:limit]
            
        except Exception as e:
            logger.error(f"Erreur recherche BM25: {e}")
            return []
    
    def _search_keywords_fallback(self, query: str, limit: int, where: Optional[Dict] = None) -> List[Memory]:
        """
        Recherche par mots-clés basique (fallback si BM25 non disponible).
        """
        if not self.collection:
            return []
        
        try:
            # Extraire les mots-clés de la requête
            keywords = self._extract_keywords(query)
            if not keywords:
                return []
            
            # Récupérer tous les documents (avec filtre si spécifié)
            all_docs = self.collection.get(where=where, include=["documents", "metadatas"])
            
            memories = []
            if all_docs["ids"]:
                for i, mem_id in enumerate(all_docs["ids"]):
                    content = all_docs["documents"][i].lower()
                    meta = all_docs["metadatas"][i] if all_docs["metadatas"] else {}
                    
                    # Calculer le score de correspondance mots-clés
                    matches = sum(1 for kw in keywords if kw.lower() in content)
                    if matches > 0:
                        keyword_score = matches / len(keywords)
                        
                        memories.append(Memory(
                            id=mem_id,
                            content=all_docs["documents"][i],
                            memory_type=meta.get("type", "unknown"),
                            timestamp=datetime.fromisoformat(meta.get("timestamp", datetime.now().isoformat())),
                            metadata=meta,
                            importance=meta.get("importance", 0.5),
                            score=keyword_score,
                            vector_score=0.0,
                            keyword_score=keyword_score
                        ))
            
            # Trier par score et limiter
            memories.sort(key=lambda m: m.keyword_score, reverse=True)
            return memories[:limit]
            
        except Exception as e:
            logger.error(f"Erreur recherche mots-clés: {e}")
            return []
    
    def _extract_keywords(self, text: str) -> List[str]:
        """Extrait les mots-clés d'un texte."""
        # Garder uniquement les mots alphanumériques de plus de 2 caractères
        words = re.findall(r'[A-Za-zÀ-ÿ0-9_]+', text)
        return [w for w in words if len(w) > 2]
    
    def _merge_hybrid_results(
        self,
        vector_results: List[Memory],
        keyword_results: List[Memory],
        vector_weight: float,
        keyword_weight: float
    ) -> List[Memory]:
        """
        Fusionne les résultats vectoriels et mots-clés.
        
        Combine les scores vectoriels et mots-clés avec pondération.
        """
        # Index par ID
        by_id: Dict[str, Memory] = {}
        
        # Ajouter les résultats vectoriels
        for mem in vector_results:
            by_id[mem.id] = Memory(
                id=mem.id,
                content=mem.content,
                memory_type=mem.memory_type,
                timestamp=mem.timestamp,
                metadata=mem.metadata,
                importance=mem.importance,
                score=0.0,
                vector_score=mem.vector_score,
                keyword_score=0.0
            )
        
        # Ajouter/fusionner les résultats mots-clés
        for mem in keyword_results:
            if mem.id in by_id:
                by_id[mem.id].keyword_score = mem.keyword_score
            else:
                by_id[mem.id] = Memory(
                    id=mem.id,
                    content=mem.content,
                    memory_type=mem.memory_type,
                    timestamp=mem.timestamp,
                    metadata=mem.metadata,
                    importance=mem.importance,
                    score=0.0,
                    vector_score=0.0,
                    keyword_score=mem.keyword_score
                )
        
        # Calculer le score final combiné
        for mem in by_id.values():
            mem.score = (vector_weight * mem.vector_score) + (keyword_weight * mem.keyword_score)
        
        return list(by_id.values())
    
    def rerank(
        self,
        query: str,
        memories: List[Memory],
        top_k: int = 5
    ) -> List[Memory]:
        """
        PHASE 6: Reranking des resultats de recherche.
        
        Ameliore la precision en re-scorant les resultats avec des
        heuristiques plus fines (sans modele ML externe).
        
        Args:
            query: Requete originale
            memories: Resultats a reranker
            top_k: Nombre de resultats a retourner
            
        Returns:
            Liste rerankee par pertinence
        """
        if not memories:
            return []
        
        query_lower = query.lower()
        query_words = set(self._extract_keywords(query))
        
        for mem in memories:
            boost = 0.0
            content_lower = mem.content.lower()
            
            # Boost: correspondance exacte de sous-phrase
            if query_lower in content_lower:
                boost += 0.3
            
            # Boost: nombre de mots-cles communs
            content_words = set(self._extract_keywords(mem.content))
            common_words = query_words & content_words
            if query_words:
                word_overlap = len(common_words) / len(query_words)
                boost += word_overlap * 0.2
            
            # Boost: recence (memoires recentes plus pertinentes)
            age_hours = (datetime.now() - mem.timestamp).total_seconds() / 3600
            if age_hours < 24:
                boost += 0.1
            elif age_hours < 168:  # 1 semaine
                boost += 0.05
            
            # Boost: importance
            boost += mem.importance * 0.1
            
            # Appliquer le boost
            mem.score = min(1.0, mem.score + boost)
        
        # Trier par score final
        memories.sort(key=lambda m: m.score, reverse=True)
        return memories[:top_k]
    
    def get_recent(self, limit: int = 10) -> List[Memory]:
        """Récupère les souvenirs récents."""
        if not self.collection:
            return []
        
        try:
            # Récupérer les mémoires de cet utilisateur, triées par timestamp
            results = self.collection.get(where=self._build_where(), limit=limit)
            
            memories = []
            if results["ids"]:
                for i, mem_id in enumerate(results["ids"]):
                    meta = results["metadatas"][i] if results["metadatas"] else {}
                    memories.append(Memory(
                        id=mem_id,
                        content=results["documents"][i],
                        memory_type=meta.get("type", "unknown"),
                        timestamp=datetime.fromisoformat(meta.get("timestamp", datetime.now().isoformat())),
                        metadata=meta,
                        importance=meta.get("importance", 0.5)
                    ))
            
            # Trier par date décroissante
            memories.sort(key=lambda m: m.timestamp, reverse=True)
            return memories[:limit]
            
        except Exception as e:
            logger.error(f"Erreur récupération mémoires: {e}")
            return []
    
    def delete(self, memory_id: str) -> bool:
        """Supprime un souvenir."""
        if not self.collection:
            return False
        
        try:
            self.collection.delete(ids=[memory_id])
            logger.debug(f"Mémoire supprimée: {memory_id}")
            return True
        except Exception as e:
            logger.error(f"Erreur suppression mémoire: {e}")
            return False
    
    def count(self) -> int:
        """Retourne le nombre de souvenirs."""
        if not self.collection:
            return 0
        return self.collection.count()
    
    def clear(self) -> bool:
        """Efface toutes les mémoires."""
        if not self.client:
            return False
        
        try:
            self.client.delete_collection(self.COLLECTION_NAME)
            self.collection = self.client.create_collection(
                name=self.COLLECTION_NAME,
                metadata={"description": "Mémoires de LUMENA"}
            )
            logger.info("Toutes les mémoires effacées")
            return True
        except Exception as e:
            logger.error(f"Erreur effacement mémoires: {e}")
            return False


class LumenaMemory:
    """
    Système de mémoire complet pour LUMENA.
    
    Combine mémoire vectorielle (ChromaDB) et mémoire factuelle (JSON).
    """
    
    def __init__(self, data_dir: Optional[Path] = None, user_id: str = "local:owner"):
        if data_dir is None:
            from src.utils.paths import MEMORY_DIR
            data_dir = MEMORY_DIR
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.user_id = user_id

        # Mémoire vectorielle (pour les souvenirs)
        self.vector_store = ChromaMemoryStore(data_dir / "vector", user_id=user_id)
        
        # Mémoire factuelle (pour les faits sur l'utilisateur)
        self.facts_file = data_dir / "facts.json"
        self.facts: Dict[str, str] = self._load_facts()
        
        # Mémoire de conversation
        self.conversation_log = data_dir / "conversations.json"
        
        logger.info("Système de mémoire LUMENA initialisé")
    def _load_facts(self) -> Dict[str, str]:
        """Charge les faits depuis le fichier (thread-safe avec filelock)."""
        if self.facts_file.exists():
            try:
                # Phase 2.5: Utiliser filelock si disponible
                if FILELOCK_AVAILABLE:
                    lock_file = str(self.facts_file) + ".lock"
                    with FileLock(lock_file, timeout=5):
                        with open(self.facts_file, "r", encoding="utf-8") as f:
                            return json.load(f)
                else:
                    with open(self.facts_file, "r", encoding="utf-8") as f:
                        return json.load(f)
            except Exception as e:
                logger.debug(f"Erreur chargement faits: {e}")
        return {}
    
    def _save_facts(self):
        """Sauvegarde les faits (thread-safe avec filelock, écriture atomique)."""
        try:
            import tempfile
            tmp_fd = None
            tmp_path = None
            # Écriture atomique: écrire dans un fichier temporaire puis renommer
            if FILELOCK_AVAILABLE:
                lock_file = str(self.facts_file) + ".lock"
                with FileLock(lock_file, timeout=5):
                    tmp_fd, tmp_path = tempfile.mkstemp(
                        dir=str(self.facts_file.parent),
                        suffix=".tmp",
                        prefix="facts_",
                    )
                    with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                        json.dump(self.facts, f, ensure_ascii=False, indent=2)
                    tmp_fd = None  # déjà fermé par os.fdopen
                    os.replace(tmp_path, str(self.facts_file))
            else:
                tmp_fd, tmp_path = tempfile.mkstemp(
                    dir=str(self.facts_file.parent),
                    suffix=".tmp",
                    prefix="facts_",
                )
                with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                    json.dump(self.facts, f, ensure_ascii=False, indent=2)
                tmp_fd = None
                os.replace(tmp_path, str(self.facts_file))
        except Exception as e:
            logger.error(f"Erreur sauvegarde faits: {e}")
            # Nettoyage du fichier temporaire en cas d'erreur
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass  # tmp file cleanup best-effort
    
    def remember(
        self, 
        content: str, 
        memory_type: str = "episodic",
        importance: float = 0.5
    ) -> Optional[str]:
        """
        Se souvient de quelque chose.
        
        Args:
            content: Ce dont se souvenir
            memory_type: Type de mémoire
            importance: Importance (0.0-1.0)
        """
        return self.vector_store.add(content, memory_type, importance)
    
    def recall(self, query: str, limit: int = 5) -> List[Memory]:
        """
        Se rappelle de souvenirs pertinents.
        
        Args:
            query: Ce qu'on cherche à se rappeler
            limit: Nombre de souvenirs à retourner
        """
        return self.vector_store.search(query, limit)
    
    def learn_fact(self, key: str, value: str):
        """
        Apprend un fait sur l'utilisateur.
        
        Args:
            key: Clé du fait (ex: "prénom", "travail", "passion")
            value: Valeur (ex: "Alice", "développeur", "jeux vidéo")
        """
        self.facts[key] = value
        self._save_facts()
        
        # Aussi stocker dans la mémoire vectorielle
        self.remember(
            f"L'utilisateur {key}: {value}",
            memory_type="semantic",
            importance=0.8
        )
        
        logger.info(f"Fait appris: {key} = {value}")
    
    def get_fact(self, key: str) -> Optional[str]:
        """Récupère un fait."""
        return self.facts.get(key)
    
    def get_all_facts(self) -> Dict[str, str]:
        """Récupère tous les faits."""
        return dict(self.facts)
    
    def get_context_for_prompt(self, query: str, max_memories: int = 3) -> str:
        """
        Génère un contexte mémoire pour le prompt LLM.
        
        Recherche hybride + inclusion des détails complets.
        
        Args:
            query: La question/message actuel
            max_memories: Nombre max de souvenirs à inclure
            
        Returns:
            Texte à ajouter au prompt système
        """
        parts = []
        
        # === RÈGLES STRICTES basées sur les faits ===
        if self.facts:
            rules = []
            
            # Règle de vouvoiement/tutoiement
            formality = self.facts.get("formality")
            if formality == "vouvoiement":
                rules.append("🔒 RÈGLE ABSOLUE: Tu dois TOUJOURS vouvoyer l'utilisateur. Utilise 'vous', 'votre', 'vos', JAMAIS 'tu', 'ton', 'ta', 'tes'.")
            elif formality == "tutoiement":
                rules.append("🔒 RÈGLE: Tu peux tutoyer l'utilisateur (tu, ton, ta, tes).")
            
            # Nom de l'utilisateur
            user_name = self.facts.get("user_name")
            if user_name:
                rules.append(f"🔒 RÈGLE: L'utilisateur s'appelle {user_name}. Utilise son prénom naturellement.")
            
            # Relation avec l'utilisateur
            relationship = self.facts.get("relationship")
            if relationship:
                rules.append(f"🔒 RÈGLE: Ta relation avec l'utilisateur: {relationship}.")
            
            if rules:
                rules_text = "\n".join(rules)
                parts.append(f"## 🔒 RÈGLES DE MÉMOIRE (À RESPECTER ABSOLUMENT)\n\n{rules_text}")
            
            # Autres faits informatifs (portfolio, anniversaire, etc.)
            info_facts = {k: v for k, v in self.facts.items() 
                         if k not in ["formality", "user_name", "relationship"]}
            if info_facts:
                facts_text = "\n".join([f"- {k}: {v}" for k, v in info_facts.items()])
                parts.append(f"## Informations sur l'utilisateur:\n{facts_text}")
        
        # === RECHERCHE MÉMOIRE AMÉLIORÉE ===
        # Détecter si la question porte sur des sujets mémorisés
        query_lower = query.lower()
        personal_keywords = ["portfolio", "portefolio", "site", "moi", "mon", "me", "toi", "lumena", "lumi", "utilisateur", "owner"]
        is_personal_question = any(kw in query_lower for kw in personal_keywords)
        
        # Augmenter le nombre de résultats pour les questions personnelles
        search_limit = max_memories * 2 if is_personal_question else max_memories
        
        # Souvenirs pertinents avec recherche hybride
        memories = self.recall(query, search_limit)
        
        # Filtrage intelligent par score de pertinence :
        # Ne garder que les mémoires vraiment pertinentes au lieu d'injecter du bruit.
        # Seuil adaptatif : plus strict pour les questions génériques, plus souple pour le personnel.
        _min_score = 0.25 if is_personal_question else 0.35
        memories = [m for m in memories if m.score >= _min_score or m.importance >= 0.9]
        
        if memories:
            # M-1: Formater les souvenirs avec contexte temporel relatif
            def _relative_time(content: str) -> str:
                """Extrait et reformate la date ISO en temps relatif lisible."""
                try:
                    import re as _re
                    from datetime import datetime as _dt
                    # Chercher [YYYY-MM-DD HH:MM] au début du contenu
                    m = _re.match(r"^\[(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}[:\d]*)\]", content)
                    if not m:
                        return content
                    date_str = m.group(1).replace("T", " ")[:16]
                    then = _dt.strptime(date_str, "%Y-%m-%d %H:%M")
                    delta = _dt.now() - then
                    days = delta.days
                    if days == 0:
                        hours = delta.seconds // 3600
                        if hours == 0:
                            label = "il y a moins d'une heure"
                        elif hours == 1:
                            label = "il y a 1 heure"
                        else:
                            label = f"il y a {hours}h"
                    elif days == 1:
                        label = "hier"
                    elif days < 7:
                        label = f"il y a {days} jours"
                    elif days < 14:
                        label = "la semaine dernière"
                    elif days < 30:
                        label = f"il y a {days // 7} semaines"
                    elif days < 60:
                        label = "le mois dernier"
                    elif days < 365:
                        label = f"il y a {days // 30} mois"
                    else:
                        label = then.strftime("le %d/%m/%Y")
                    # Remplacer la date brute par le label relatif
                    return content.replace(m.group(0), f"[{label}]", 1)
                except Exception:
                    return content  # Fallback silencieux

            # Pour les questions personnelles, inclure plus de détails
            if is_personal_question:
                memories_parts = []
                for m in memories[:max_memories]:
                    content_raw = m.content[:800] if m.importance >= 0.8 else m.content[:300]
                    content = _relative_time(content_raw)
                    memories_parts.append(f"- {content}")
                memories_text = "\n".join(memories_parts)
                parts.append(f"## 🧠 Souvenirs pertinents (À UTILISER POUR RÉPONDRE):\n{memories_text}")
            else:
                memories_text = "\n".join([f"- {_relative_time(m.content[:200])}" for m in memories])
                parts.append(f"## Souvenirs pertinents:\n{memories_text}")
        
        return "\n\n".join(parts) if parts else ""
    
    def get_stats(self) -> Dict[str, Any]:
        """Retourne des statistiques sur la mémoire."""
        return {
            "total_memories": self.vector_store.count(),
            "total_facts": len(self.facts),
            "facts": list(self.facts.keys())
        }


class ChromaDBStore(ChromaMemoryStore):
    """
    Alias compatibilité legacy.

    Certains tests/imports historiques attendent `ChromaDBStore`.
    """

    def __init__(self, data_dir: Optional[Path] = None, user_id: str = "local:owner"):
        if data_dir is None:
            from src.utils.paths import VECTOR_DIR
            data_dir = VECTOR_DIR
        super().__init__(data_dir, user_id=user_id)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
