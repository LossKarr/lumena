"""
🌟 LUMENA - Cache d'Embeddings SQLite

Cache persistant pour les embeddings vectoriels.
Évite de recalculer les embeddings pour les textes déjà vus.

"""

from typing import List, Optional, Callable, Any
from pathlib import Path
import sqlite3
import hashlib
import json
import threading
import time
from loguru import logger


class EmbeddingCache:
    """
    Cache SQLite pour stocker les embeddings calculés.
    
    Évite les appels API répétés pour les mêmes textes.
    Utilise un hash du texte comme clé pour une recherche efficace.
    """
    
    def __init__(self, cache_path: Path):
        """
        Initialise le cache d'embeddings.
        
        Args:
            cache_path: Dossier où stocker la base de données
        """
        cache_path.mkdir(parents=True, exist_ok=True)
        self.db_path = cache_path / "embedding_cache.db"
        self.conn: Optional[sqlite3.Connection] = None
        self._init_db()
    
    def _init_db(self):
        """Crée la table de cache si nécessaire."""
        try:
            self.conn = sqlite3.connect(str(self.db_path))
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    hash TEXT PRIMARY KEY,
                    model TEXT NOT NULL,
                    embedding TEXT NOT NULL,
                    text_preview TEXT,
                    created_at REAL,
                    accessed_at REAL,
                    access_count INTEGER DEFAULT 1
                )
            """)
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_model ON embeddings(model)
            """)
            self.conn.commit()
            
            # Compter les entrées
            count = self.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
            logger.debug(f"Cache d'embeddings initialisé: {count} entrées")
            
        except Exception as e:
            logger.error(f"Erreur initialisation cache embeddings: {e}")
            self.conn = None
    
    def _hash_text(self, text: str, model: str) -> str:
        """Génère un hash unique pour le texte + modèle."""
        content = f"{model}:{text}"
        return hashlib.sha256(content.encode('utf-8')).hexdigest()
    
    def get(self, text: str, model: str) -> Optional[List[float]]:
        """
        Récupère un embedding du cache.
        
        Args:
            text: Texte original
            model: Nom du modèle d'embedding utilisé
            
        Returns:
            L'embedding si trouvé, None sinon
        """
        if not self.conn:
            return None
        
        try:
            hash_key = self._hash_text(text, model)
            cursor = self.conn.execute(
                "SELECT embedding FROM embeddings WHERE hash = ? AND model = ?",
                (hash_key, model)
            )
            row = cursor.fetchone()
            
            if row:
                # Mettre à jour les stats d'accès
                self.conn.execute(
                    "UPDATE embeddings SET accessed_at = ?, access_count = access_count + 1 WHERE hash = ?",
                    (time.time(), hash_key)
                )
                self.conn.commit()
                
                return json.loads(row[0])
            
            return None
            
        except Exception as e:
            logger.error(f"Erreur lecture cache embedding: {e}")
            return None
    
    def set(self, text: str, model: str, embedding: List[float]):
        """
        Sauvegarde un embedding dans le cache.
        
        Args:
            text: Texte original
            model: Nom du modèle
            embedding: Vecteur d'embedding
        """
        if not self.conn:
            return
        
        try:
            hash_key = self._hash_text(text, model)
            now = time.time()
            
            # Prévisualisation du texte (pour debug)
            preview = text[:100] if len(text) > 100 else text
            
            self.conn.execute("""
                INSERT OR REPLACE INTO embeddings 
                (hash, model, embedding, text_preview, created_at, accessed_at, access_count)
                VALUES (?, ?, ?, ?, ?, ?, 1)
            """, (hash_key, model, json.dumps(embedding), preview, now, now))
            self.conn.commit()
            
        except Exception as e:
            logger.error(f"Erreur écriture cache embedding: {e}")
    
    def get_or_compute(
        self, 
        text: str, 
        model: str, 
        compute_fn: Callable[[str], List[float]]
    ) -> List[float]:
        """
        Récupère du cache ou calcule si absent.
        
        C'est la méthode principale à utiliser.
        
        Args:
            text: Texte à embedder
            model: Nom du modèle
            compute_fn: Fonction pour calculer l'embedding si absent
            
        Returns:
            L'embedding (depuis cache ou nouvellement calculé)
        """
        # Essayer le cache d'abord
        cached = self.get(text, model)
        if cached is not None:
            logger.debug(f"Cache hit pour embedding ({len(text)} chars)")
            return cached
        
        # Calculer
        logger.debug(f"Cache miss, calcul embedding ({len(text)} chars)")
        embedding = compute_fn(text)
        
        # Sauvegarder
        self.set(text, model, embedding)
        
        return embedding
    
    async def get_or_compute_async(
        self,
        text: str,
        model: str,
        compute_fn: Callable[[str], Any]  # Coroutine
    ) -> List[float]:
        """Version async de get_or_compute."""
        # Essayer le cache d'abord
        cached = self.get(text, model)
        if cached is not None:
            return cached
        
        # Calculer (async)
        embedding = await compute_fn(text)
        
        # Sauvegarder
        self.set(text, model, embedding)
        
        return embedding
    
    def clear(self):
        """Vide complètement le cache."""
        if not self.conn:
            return
        
        try:
            self.conn.execute("DELETE FROM embeddings")
            self.conn.commit()
            logger.info("Cache d'embeddings vidé")
        except Exception as e:
            logger.error(f"Erreur vidage cache: {e}")
    
    def clear_old(self, max_age_days: int = 30):
        """
        Supprime les entrées plus anciennes que max_age_days.
        
        Args:
            max_age_days: Âge maximum en jours
        """
        if not self.conn:
            return
        
        try:
            cutoff = time.time() - (max_age_days * 24 * 3600)
            cursor = self.conn.execute(
                "DELETE FROM embeddings WHERE accessed_at < ?",
                (cutoff,)
            )
            self.conn.commit()
            logger.info(f"Cache: {cursor.rowcount} entrées anciennes supprimées")
        except Exception as e:
            logger.error(f"Erreur nettoyage cache: {e}")
    
    def get_stats(self) -> dict:
        """Retourne des statistiques sur le cache."""
        if not self.conn:
            return {"error": "Cache non initialisé"}
        
        try:
            total = self.conn.execute("SELECT COUNT(*) FROM embeddings").fetchone()[0]
            models = self.conn.execute(
                "SELECT model, COUNT(*) FROM embeddings GROUP BY model"
            ).fetchall()
            size_bytes = self.db_path.stat().st_size if self.db_path.exists() else 0
            
            return {
                "total_entries": total,
                "by_model": {m[0]: m[1] for m in models},
                "size_mb": round(size_bytes / (1024 * 1024), 2)
            }
        except Exception as e:
            return {"error": str(e)}
    
    def close(self):
        """Ferme la connexion à la base."""
        if self.conn:
            self.conn.close()
            self.conn = None


# Singleton global
_embedding_cache: Optional[EmbeddingCache] = None
_embedding_cache_lock = threading.Lock()


def get_embedding_cache(cache_path: Optional[Path] = None) -> EmbeddingCache:
    """Retourne l'instance globale du cache d'embeddings."""
    global _embedding_cache
    if _embedding_cache is None:
        with _embedding_cache_lock:
            if _embedding_cache is None:
                if cache_path is None:
                    raise RuntimeError("EmbeddingCache: cache_path requis au premier appel")
                _embedding_cache = EmbeddingCache(cache_path)
    return _embedding_cache
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
