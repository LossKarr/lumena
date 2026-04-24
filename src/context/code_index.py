"""
🌟 LUMENA - Code Index pour RAG

Index vectoriel du code source utilisant ChromaDB.
Permet la recherche sémantique dans le codebase.
"""

from pathlib import Path
from typing import List, Dict, Optional, Any
from dataclasses import dataclass
from loguru import logger
import json

# Import ChromaDB
try:
    import chromadb
    from chromadb.config import Settings
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False
    logger.warning("ChromaDB non disponible pour l'index code")

from .code_chunker import CodeChunk, CodeChunker, get_code_chunker


@dataclass
class CodeSearchResult:
    """Résultat de recherche dans le code."""
    chunk: CodeChunk
    score: float
    highlight: str  # Extrait pertinent
    
    def format_context(self) -> str:
        """Formate le résultat pour le contexte LLM."""
        header = f"# {self.chunk.file_path}"
        if self.chunk.symbol_name:
            header += f" - {self.chunk.symbol_type} {self.chunk.symbol_name}"
        header += f" (L{self.chunk.line_start}-{self.chunk.line_end})"
        
        return f"{header}\n```{self.chunk.language}\n{self.chunk.content}\n```"


def _workspace_key(project_root: Path) -> str:
    """Hash court (8 chars) du chemin absolu du workspace — safe pour nom de collection Chroma."""
    import hashlib
    return hashlib.sha256(str(project_root.resolve()).encode()).hexdigest()[:8]


class CodeIndex:
    """
    🔍 Index vectoriel du code source

    Utilise ChromaDB pour indexer et rechercher dans le code.
    Chaque workspace a son propre persist_dir ET sa propre collection Chroma
    → isolation complète, pas de contamination inter-projets.
    """

    def __init__(self, project_root: Path, persist_dir: Optional[Path] = None):
        self.project_root = Path(project_root).resolve()
        _key = _workspace_key(self.project_root)

        # Dossier de persistence : sous-dossier par workspace
        if persist_dir is None:
            from src.utils.paths import CODE_INDEX_DIR
            persist_dir = CODE_INDEX_DIR / _key
        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        # Nom de collection unique par workspace
        self.collection_name = f"lumena_code_{_key}"

        # Initialiser ChromaDB
        if CHROMADB_AVAILABLE:
            self.client = chromadb.PersistentClient(
                path=str(self.persist_dir),
                settings=Settings(anonymized_telemetry=False)
            )
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
            logger.info(f"📚 CodeIndex initialisé ({self.collection.count()} chunks) [ws={_key}]")
        else:
            self.client = None
            self.collection = None
            logger.warning("CodeIndex désactivé (ChromaDB non disponible)")

        # Chunker
        self.chunker = CodeChunker(project_root)
    
    def index_project(self, force_reindex: bool = False) -> int:
        """
        Indexe le projet entier.
        
        Args:
            force_reindex: Si True, réindexe même si déjà fait
            
        Returns:
            Nombre de chunks indexés
        """
        if not CHROMADB_AVAILABLE:
            return 0
        
        # Vérifier si déjà indexé
        existing_count = self.collection.count()
        if existing_count > 0 and not force_reindex:
            logger.info(f"♻️ Index existant ({existing_count} chunks), skip")
            return existing_count
        
        # Nettoyer si réindexation
        if force_reindex and existing_count > 0:
            self.client.delete_collection(self.collection_name)
            self.collection = self.client.create_collection(
                name=self.collection_name,
                metadata={"hnsw:space": "cosine"}
            )
        
        # Chunker le projet (toutes extensions supportées par défaut)
        chunks = self.chunker.chunk_project()
        
        if not chunks:
            logger.warning("Aucun chunk à indexer")
            return 0
        
        # Préparer les données pour ChromaDB
        ids = []
        documents = []
        metadatas = []
        
        for chunk in chunks:
            ids.append(chunk.id)
            
            # Document = contenu enrichi pour l'embedding
            doc = f"{chunk.symbol_type}: {chunk.symbol_name or 'module'}\n"
            doc += f"File: {chunk.file_path}\n"
            doc += chunk.content
            documents.append(doc)
            
            # Métadonnées
            metadatas.append({
                "file_path": chunk.file_path,
                "symbol_name": chunk.symbol_name or "",
                "symbol_type": chunk.symbol_type,
                "line_start": chunk.line_start,
                "line_end": chunk.line_end,
                "language": chunk.language,
            })
        
        # Indexer par batch
        batch_size = 100
        for i in range(0, len(ids), batch_size):
            batch_ids = ids[i:i+batch_size]
            batch_docs = documents[i:i+batch_size]
            batch_meta = metadatas[i:i+batch_size]
            
            self.collection.add(
                ids=batch_ids,
                documents=batch_docs,
                metadatas=batch_meta
            )
        
        logger.info(f"✅ {len(chunks)} chunks indexés")
        return len(chunks)
    
    def search(
        self, 
        query: str, 
        n_results: int = 5,
        filter_type: Optional[str] = None
    ) -> List[CodeSearchResult]:
        """
        Recherche sémantique dans le code.
        
        Args:
            query: Requête de recherche
            n_results: Nombre de résultats
            filter_type: Filtrer par type (function, class, etc.)
            
        Returns:
            Liste de résultats avec scores
        """
        if not CHROMADB_AVAILABLE or self.collection is None:
            return []
        
        # Construire le filtre
        where = None
        if filter_type:
            where = {"symbol_type": filter_type}
        
        # Rechercher
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"]
        )
        
        # Construire les résultats
        search_results = []
        
        if results['ids'] and results['ids'][0]:
            for i, chunk_id in enumerate(results['ids'][0]):
                # Reconstruire le chunk depuis les métadonnées
                meta = results['metadatas'][0][i]
                doc = results['documents'][0][i]
                distance = results['distances'][0][i] if results['distances'] else 1.0
                
                # Extraire le contenu (sans l'en-tête ajouté)
                content_lines = doc.split('\n')[2:]  # Skip les 2 premières lignes
                content = '\n'.join(content_lines)
                
                chunk = CodeChunk(
                    id=chunk_id,
                    content=content,
                    file_path=meta['file_path'],
                    symbol_name=meta['symbol_name'] or None,
                    symbol_type=meta['symbol_type'],
                    line_start=meta['line_start'],
                    line_end=meta['line_end'],
                    language=meta['language'],
                )
                
                # Score = 1 - distance (cosine)
                score = max(0, 1 - distance)
                
                # Highlight (premiers 200 chars)
                highlight = content[:200].replace('\n', ' ')
                
                search_results.append(CodeSearchResult(
                    chunk=chunk,
                    score=score,
                    highlight=highlight
                ))
        
        return search_results
    
    def get_context_for_query(self, query: str, max_tokens: int = 2000) -> str:
        """
        Retourne du contexte code pour une requête.
        
        Args:
            query: Requête utilisateur
            max_tokens: Limite de tokens (approximation)
            
        Returns:
            Contexte formaté pour le LLM
        """
        results = self.search(query, n_results=5)
        
        if not results:
            return ""
        
        context_parts = []
        char_count = 0
        max_chars = max_tokens * 4  # Approximation
        
        for result in results:
            formatted = result.format_context()
            if char_count + len(formatted) > max_chars:
                break
            context_parts.append(formatted)
            char_count += len(formatted)
        
        if context_parts:
            return "## Relevant Code\n\n" + "\n\n---\n\n".join(context_parts)
        return ""
    
    def get_stats(self) -> Dict[str, Any]:
        """Retourne les statistiques de l'index."""
        if not CHROMADB_AVAILABLE or self.collection is None:
            return {"available": False}
        
        return {
            "available": True,
            "total_chunks": self.collection.count(),
            "persist_dir": str(self.persist_dir),
        }


# Cache keyed par workspace — évite la contamination inter-projets
_code_index_cache: dict[str, "CodeIndex"] = {}
import threading as _threading
_code_index_lock = _threading.Lock()


def get_code_index(project_root: Optional[Path] = None) -> "CodeIndex":
    """Retourne l'instance CodeIndex pour un workspace (isolée par projet)."""
    global _code_index_cache
    if project_root is None:
        raise ValueError("project_root requis")
    key = str(Path(project_root).resolve())
    with _code_index_lock:
        if key not in _code_index_cache:
            _code_index_cache[key] = CodeIndex(project_root)
        return _code_index_cache[key]


def clear_code_index_cache(project_root: Optional[Path] = None) -> None:
    """Vide le cache d'un workspace (ou tous si project_root=None)."""
    global _code_index_cache
    with _code_index_lock:
        if project_root is None:
            _code_index_cache.clear()
        else:
            _code_index_cache.pop(str(Path(project_root).resolve()), None)


if __name__ == "__main__":
    project = Path(__file__).parent.parent.parent
    index = CodeIndex(project)
    
    print("Indexation...")
    count = index.index_project(force_reindex=True)
    print(f"Indexé: {count} chunks")
    
    print("\nRecherche 'chat async'...")
    results = index.search("chat async function", n_results=3)
    for r in results:
        print(f"  [{r.score:.2f}] {r.chunk.file_path}: {r.chunk.symbol_name}")
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
