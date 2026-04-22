"""
🌟 LUMENA - Repo Map (Carte du Projet)

Génère une carte structurelle compacte du repository pour donner
à Lumena une conscience globale du projet en ~1000 tokens.

Carte structurelle compacte du repository.
"""

import os
from pathlib import Path
from typing import List, Dict, Optional, Set, Tuple
from dataclasses import dataclass, field
from collections import defaultdict
from loguru import logger

from .ast_parser import ASTParser, FileSignatures, get_ast_parser


@dataclass
class RepoStats:
    """Statistiques du repository."""
    total_files: int = 0
    total_symbols: int = 0
    languages: Dict[str, int] = field(default_factory=dict)
    largest_files: List[Tuple[str, int]] = field(default_factory=list)


class RepoMap:
    """
    🗺️ Carte du Repository
    
    Génère une vue compacte et hiérarchique du projet pour le LLM.
    Permet de connaître la structure sans charger tous les fichiers.
    """
    
    # Extensions à ignorer
    IGNORE_EXTENSIONS = {
        '.pyc', '.pyo', '.pyd', '.so', '.dll', '.exe',
        '.jpg', '.jpeg', '.png', '.gif', '.ico', '.svg', '.webp',
        '.mp3', '.mp4', '.wav', '.avi', '.mov',
        '.zip', '.tar', '.gz', '.rar', '.7z',
        '.pdf', '.doc', '.docx', '.xls', '.xlsx',
        '.db', '.sqlite', '.pkl', '.pickle',
        '.lock', '.log', '.tmp', '.cache',
        '.woff', '.woff2', '.ttf', '.eot',
    }
    
    # Dossiers à ignorer
    IGNORE_DIRS = {
        '__pycache__', 'node_modules', '.git', '.svn', '.hg',
        'venv', '.venv', 'env', '.env', 'virtualenv',
        'dist', 'build', 'target', 'out', 'bin',
        '.idea', '.vscode', '.vs',
        '.pytest_cache', '.mypy_cache', '.tox',
        'coverage', 'htmlcov', '.coverage',
        'eggs', '*.egg-info', '.eggs',
        'Library', 'Packages', 'Logs',  # Unity
        'unsloth_compiled_cache', 'unity',  # Specifique Lumena
    }
    
    # Fichiers importants à prioriser
    IMPORTANT_FILES = {
        'main.py', 'app.py', 'index.py', '__init__.py',
        'core.py', 'config.py', 'settings.py',
        'package.json', 'pyproject.toml', 'setup.py',
        'README.md', 'Makefile', 'Dockerfile',
    }
    
    def __init__(
        self, 
        project_path: Path,
        max_tokens: int = 1500,
        max_files: int = 50,
        max_symbols_per_file: int = 10
    ):
        """
        Initialise le RepoMap.
        
        Args:
            project_path: Chemin racine du projet
            max_tokens: Nombre max de tokens pour la carte
            max_files: Nombre max de fichiers à inclure
            max_symbols_per_file: Symboles max par fichier
        """
        self.project_path = Path(project_path).resolve()
        self.max_tokens = max_tokens
        self.max_files = max_files
        self.max_symbols_per_file = max_symbols_per_file
        
        self.parser = get_ast_parser()
        self._file_signatures: Dict[str, FileSignatures] = {}
        self._importance_scores: Dict[str, float] = {}
        self._stats = RepoStats()
        
        logger.debug(f"🗺️ RepoMap initialisé pour: {self.project_path}")
    
    def build(self) -> None:
        """
        Construit la carte du repository.
        Scanne tous les fichiers et extrait les signatures.
        """
        logger.info(f"🔍 Construction du RepoMap pour {self.project_path}")
        
        # Scanner les fichiers
        files = self._scan_files()
        self._stats.total_files = len(files)
        
        logger.debug(f"  Fichiers trouvés: {len(files)}")
        
        # Parser chaque fichier
        for file_path in files:
            rel_path = file_path.relative_to(self.project_path)
            sig = self.parser.parse_file(file_path)
            
            if sig.is_valid and sig.symbols:
                self._file_signatures[str(rel_path)] = sig
                self._stats.total_symbols += len(sig.symbols)
                
                # Comptage par langage
                lang = sig.language
                self._stats.languages[lang] = self._stats.languages.get(lang, 0) + 1
        
        # Calculer les scores d'importance
        self._calculate_importance()
        
        logger.info(f"✅ RepoMap construit: {len(self._file_signatures)} fichiers, {self._stats.total_symbols} symboles")
    
    def _scan_files(self) -> List[Path]:
        """Scanne les fichiers du projet."""
        files = []
        
        for root, dirs, filenames in os.walk(self.project_path):
            # Filtrer les dossiers à ignorer
            dirs[:] = [d for d in dirs if d not in self.IGNORE_DIRS 
                       and not d.startswith('.')]
            
            for filename in filenames:
                file_path = Path(root) / filename
                
                # Vérifier l'extension
                if file_path.suffix.lower() in self.IGNORE_EXTENSIONS:
                    continue
                
                # Vérifier si c'est un fichier de code supporté
                if file_path.suffix.lower() in ASTParser.SUPPORTED_EXTENSIONS:
                    files.append(file_path)
        
        return files
    
    def _calculate_importance(self) -> None:
        """
        Calcule un score d'importance pour chaque fichier.
        
        Facteurs:
        - Nom de fichier important (main.py, core.py, etc.)
        - Nombre de symboles
        - Profondeur dans l'arborescence (moins profond = plus important)
        - Référencé par d'autres fichiers
        """
        for rel_path, sig in self._file_signatures.items():
            score = 0.0
            path = Path(rel_path)
            
            # Fichier important par nom
            if path.name in self.IMPORTANT_FILES:
                score += 10.0
            
            # __init__.py à la racine très important
            if path.name == '__init__.py' and len(path.parts) <= 2:
                score += 5.0
            
            # Nombre de symboles (plus = plus important, plafonné)
            score += min(len(sig.symbols), 20) * 0.5
            
            # Profondeur (moins profond = plus important)
            depth = len(path.parts)
            score += max(0, 5 - depth)
            
            # Présence de classes (souvent plus importantes)
            classes = sum(1 for s in sig.symbols if s.type == 'class')
            score += classes * 2
            
            self._importance_scores[rel_path] = score
    
    def get_top_files(self, n: int = None) -> List[Tuple[str, FileSignatures]]:
        """Retourne les n fichiers les plus importants."""
        n = n or self.max_files
        
        sorted_files = sorted(
            self._file_signatures.items(),
            key=lambda x: self._importance_scores.get(x[0], 0),
            reverse=True
        )
        
        return sorted_files[:n]
    
    def get_compact_map(self, max_tokens: int = None) -> str:
        """
        Génère une carte compacte pour le prompt LLM.
        
        Returns:
            String formaté avec la structure du projet
        """
        max_tokens = max_tokens or self.max_tokens
        
        if not self._file_signatures:
            self.build()
        
        lines = []
        lines.append("# 🗺️ Project Map")
        lines.append("")
        
        # Stats résumées
        lang_str = ", ".join(f"{k}:{v}" for k, v in sorted(
            self._stats.languages.items(), 
            key=lambda x: x[1], 
            reverse=True
        )[:3])
        lines.append(f"Files: {self._stats.total_files} | Symbols: {self._stats.total_symbols} | {lang_str}")
        lines.append("")
        
        # Fichiers par importance
        top_files = self.get_top_files()
        
        current_dir = None
        char_count = sum(len(l) for l in lines)
        
        for rel_path, sig in top_files:
            path = Path(rel_path)
            
            # Nouvelle section de dossier
            if len(path.parts) > 1:
                dir_path = str(path.parent)
                if dir_path != current_dir:
                    current_dir = dir_path
                    lines.append(f"\n## {dir_path}/")
            elif current_dir is not None:
                current_dir = None
                lines.append(f"\n## ./")
            
            # Fichier et ses symboles
            file_line = f"### {path.name}"
            lines.append(file_line)
            
            # Symboles (limités)
            shown_symbols = 0
            for symbol in sig.symbols[:self.max_symbols_per_file]:
                symbol_line = f"  - {symbol.signature}"
                
                # Vérifier la limite de tokens (approximation: 4 chars = 1 token)
                char_count += len(symbol_line) + 1
                if char_count / 4 > max_tokens:
                    lines.append(f"  ... (truncated)")
                    break
                    
                lines.append(symbol_line)
                shown_symbols += 1
            
            if len(sig.symbols) > shown_symbols:
                lines.append(f"  ... (+{len(sig.symbols) - shown_symbols} more)")
            
            # Vérifier limite globale
            if char_count / 4 > max_tokens * 0.9:
                remaining = len(top_files) - top_files.index((rel_path, sig)) - 1
                if remaining > 0:
                    lines.append(f"\n... (+{remaining} more files)")
                break
        
        return "\n".join(lines)
    
    def get_file_context(self, file_path: str) -> Optional[str]:
        """
        Retourne le contexte d'un fichier spécifique.
        
        Args:
            file_path: Chemin relatif du fichier
        
        Returns:
            Contexte formaté ou None si non trouvé
        """
        sig = self._file_signatures.get(file_path)
        if not sig:
            return None
        
        return sig.to_map_entry(self.max_symbols_per_file)
    
    def search_symbol(self, query: str) -> List[Tuple[str, str]]:
        """
        Recherche un symbole par nom.
        
        Args:
            query: Nom ou partie du nom à chercher
        
        Returns:
            Liste de (fichier, signature) correspondants
        """
        query_lower = query.lower()
        results = []
        
        for rel_path, sig in self._file_signatures.items():
            for symbol in sig.symbols:
                if query_lower in symbol.name.lower():
                    results.append((rel_path, symbol.signature))
        
        return results[:20]  # Limiter les résultats
    
    def get_stats(self) -> RepoStats:
        """Retourne les statistiques du repo."""
        return self._stats
    
    def refresh(self) -> None:
        """Reconstruit la carte (après modifications)."""
        self._file_signatures.clear()
        self._importance_scores.clear()
        self.parser.clear_cache()
        self.build()


# Singleton
_repo_map: Optional[RepoMap] = None


def get_repo_map(project_path: Optional[Path] = None) -> RepoMap:
    """
    Retourne l'instance globale du RepoMap.
    
    Args:
        project_path: Chemin du projet (requis au premier appel)
    
    Returns:
        Instance RepoMap
    """
    global _repo_map
    
    if _repo_map is None:
        if project_path is None:
            raise ValueError("project_path requis pour le premier appel")
        _repo_map = RepoMap(project_path)
    
    return _repo_map


# Test si exécuté directement
if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        project = Path(sys.argv[1])
    else:
        project = Path(__file__).parent.parent.parent  # lumena/
    
    print(f"Building RepoMap for: {project}")
    
    repo_map = RepoMap(project, max_tokens=2000, max_files=30)
    repo_map.build()
    
    print("\n" + "="*60)
    print(repo_map.get_compact_map())
    print("="*60)
    
    stats = repo_map.get_stats()
    print(f"\nStats: {stats.total_files} files, {stats.total_symbols} symbols")
    print(f"Languages: {stats.languages}")
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
