"""
🌟 LUMENA - Module Context (Conscience Projet)

Fournit à Lumena une conscience automatique du projet :
- Repo Map : carte structurelle du repo
- AST Parser : parsing intelligent du code
- Code Chunker : découpage sémantique pour RAG
- Code Index : recherche vectorielle dans le code
"""

from pathlib import Path
from typing import Optional

# Exports principaux
from .ast_parser import (
    ASTParser,
    CodeSymbol,
    FileSignatures,
    get_ast_parser,
)

from .repo_map import (
    RepoMap,
    RepoStats,
    get_repo_map,
)

from .code_chunker import (
    CodeChunk,
    CodeChunker,
    get_code_chunker,
)

from .code_index import (
    CodeIndex,
    CodeSearchResult,
    get_code_index,
)

__all__ = [
    # AST Parser
    "ASTParser",
    "CodeSymbol", 
    "FileSignatures",
    "get_ast_parser",
    # Repo Map
    "RepoMap",
    "RepoStats",
    "get_repo_map",
    # Code Chunker
    "CodeChunk",
    "CodeChunker",
    "get_code_chunker",
    # Code Index
    "CodeIndex",
    "CodeSearchResult",
    "get_code_index",
]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
