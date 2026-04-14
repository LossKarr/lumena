"""
🌟 LUMENA - Code Chunker pour RAG

Découpe le code en chunks sémantiques pour l'indexation vectorielle.
Utilise le AST parser pour découper par fonctions/classes plutôt que par lignes.
"""

from pathlib import Path
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
import hashlib
from loguru import logger

from .ast_parser import get_ast_parser, FileSignatures, CodeSymbol


@dataclass
class CodeChunk:
    """Un chunk de code pour l'indexation."""
    id: str  # Hash unique
    content: str  # Le code source
    file_path: str  # Chemin relatif
    symbol_name: Optional[str]  # Nom de la fonction/classe
    symbol_type: str  # 'function', 'class', 'method', 'module'
    line_start: int
    line_end: int
    language: str
    imports: List[str] = field(default_factory=list)
    parent_class: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "file_path": self.file_path,
            "symbol_name": self.symbol_name,
            "symbol_type": self.symbol_type,
            "line_start": self.line_start,
            "line_end": self.line_end,
            "language": self.language,
            "imports": self.imports,
            "parent_class": self.parent_class,
        }
    
    @property
    def metadata_str(self) -> str:
        """Métadonnées formatées pour le contexte."""
        parts = [f"File: {self.file_path}"]
        if self.symbol_name:
            parts.append(f"{self.symbol_type}: {self.symbol_name}")
        parts.append(f"Lines: {self.line_start}-{self.line_end}")
        return " | ".join(parts)


class CodeChunker:
    """
    🔪 Découpeur de code intelligent
    
    Découpe le code source en chunks sémantiques basés sur la structure AST.
    Chaque fonction/classe devient un chunk autonome avec son contexte.
    """
    
    # Taille max d'un chunk (en caractères)
    MAX_CHUNK_SIZE = 2000
    
    # Taille min pour créer un chunk
    MIN_CHUNK_SIZE = 50
    
    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).resolve()
        self.parser = get_ast_parser()
        self._chunks: List[CodeChunk] = []
    
    def chunk_file(self, file_path: Path) -> List[CodeChunk]:
        """
        Découpe un fichier en chunks sémantiques.
        
        Args:
            file_path: Chemin du fichier
            
        Returns:
            Liste de CodeChunks
        """
        chunks = []
        
        # Parser le fichier
        sig = self.parser.parse_file(file_path)
        if not sig.is_valid:
            return []
        
        # Lire le contenu
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
            lines = content.split('\n')
        except Exception as e:
            logger.warning(f"Erreur lecture {file_path}: {e}")
            return []
        
        rel_path = str(file_path.relative_to(self.project_root))
        
        # Si pas de symboles, créer un chunk pour tout le fichier
        if not sig.symbols:
            if len(content) > self.MIN_CHUNK_SIZE:
                chunk = self._create_chunk(
                    content=content[:self.MAX_CHUNK_SIZE],
                    file_path=rel_path,
                    symbol_name=None,
                    symbol_type="module",
                    line_start=1,
                    line_end=len(lines),
                    language=sig.language,
                    imports=sig.imports
                )
                chunks.append(chunk)
            return chunks
        
        # Créer un chunk pour chaque symbole de haut niveau
        for symbol in sig.symbols:
            # Ignorer les méthodes (incluses dans leur classe)
            if symbol.parent:
                continue
            
            # Extraire le code du symbole
            start_idx = max(0, symbol.line_start - 1)
            end_idx = min(len(lines), symbol.line_end)
            symbol_code = '\n'.join(lines[start_idx:end_idx])
            
            # Tronquer si trop long
            if len(symbol_code) > self.MAX_CHUNK_SIZE:
                symbol_code = symbol_code[:self.MAX_CHUNK_SIZE] + "\n# ... (truncated)"
            
            if len(symbol_code) < self.MIN_CHUNK_SIZE:
                continue
            
            chunk = self._create_chunk(
                content=symbol_code,
                file_path=rel_path,
                symbol_name=symbol.name,
                symbol_type=symbol.type,
                line_start=symbol.line_start,
                line_end=symbol.line_end,
                language=sig.language,
                imports=sig.imports[:5]  # Limiter les imports
            )
            chunks.append(chunk)
        
        return chunks
    
    def _create_chunk(
        self,
        content: str,
        file_path: str,
        symbol_name: Optional[str],
        symbol_type: str,
        line_start: int,
        line_end: int,
        language: str,
        imports: List[str] = None,
        parent_class: str = None
    ) -> CodeChunk:
        """Crée un chunk avec un ID unique."""
        # Générer un ID basé sur le hash du contenu
        hash_input = f"{file_path}:{symbol_name}:{content[:200]}"
        chunk_id = hashlib.md5(hash_input.encode()).hexdigest()[:12]
        
        return CodeChunk(
            id=chunk_id,
            content=content,
            file_path=file_path,
            symbol_name=symbol_name,
            symbol_type=symbol_type,
            line_start=line_start,
            line_end=line_end,
            language=language,
            imports=imports or [],
            parent_class=parent_class
        )
    
    def chunk_project(self, extensions: List[str] = None) -> List[CodeChunk]:
        """
        Découpe tout le projet en chunks.
        
        Args:
            extensions: Extensions à inclure (défaut: .py, .js, .ts)
            
        Returns:
            Liste de tous les chunks
        """
        if extensions is None:
            extensions = ['.py', '.js', '.ts', '.jsx', '.tsx']
        
        all_chunks = []
        
        # Dossiers à ignorer
        ignore_dirs = {
            '__pycache__', 'node_modules', '.git', 'venv', '.venv',
            'dist', 'build', 'Library', 'Packages', 'unity',
            'unsloth_compiled_cache', 'backup_2026-02-02'
        }
        
        for ext in extensions:
            for file_path in self.project_root.rglob(f"*{ext}"):
                # Vérifier si dans un dossier ignoré
                if any(d in file_path.parts for d in ignore_dirs):
                    continue
                
                chunks = self.chunk_file(file_path)
                all_chunks.extend(chunks)
        
        self._chunks = all_chunks
        logger.info(f"📦 {len(all_chunks)} chunks créés depuis le projet")
        return all_chunks
    
    def get_chunks(self) -> List[CodeChunk]:
        """Retourne les chunks créés."""
        return self._chunks
    
    def search_by_name(self, query: str) -> List[CodeChunk]:
        """Recherche des chunks par nom de symbole."""
        query_lower = query.lower()
        return [c for c in self._chunks if c.symbol_name and query_lower in c.symbol_name.lower()]


# Singleton
_chunker: Optional[CodeChunker] = None


def get_code_chunker(project_root: Optional[Path] = None) -> CodeChunker:
    """Retourne l'instance globale du chunker."""
    global _chunker
    if _chunker is None:
        if project_root is None:
            raise ValueError("project_root requis pour le premier appel")
        _chunker = CodeChunker(project_root)
    return _chunker


if __name__ == "__main__":
    from pathlib import Path
    
    project = Path(__file__).parent.parent.parent
    chunker = CodeChunker(project)
    chunks = chunker.chunk_project(['.py'])
    
    print(f"Total chunks: {len(chunks)}")
    for chunk in chunks[:5]:
        print(f"  - {chunk.file_path}: {chunk.symbol_name} ({chunk.symbol_type})")
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
