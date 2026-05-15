"""
STATUS: pending-integration — non canonique en production. Branchement prévu sur view_outline (P8).
🌲 LUMENA - Multi-Language Code Parser (Phase 4: Performance)

Parse la structure de fichiers dans plusieurs langages.
Fallback gracieux si tree-sitter n'est pas installé: utilise AST Python + regex.
"""

import re
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger("lumena.tree_sitter_parser")


@dataclass
class CodeSymbol:
    """Un symbole dans le code (classe, fonction, etc.)."""
    name: str
    kind: str  # "class", "function", "method", "variable", "import"
    line_start: int
    line_end: int
    signature: str = ""
    children: List["CodeSymbol"] = field(default_factory=list)


class MultiLanguageParser:
    """
    🚀 Parser multi-langages avec fallback.
    
    Langages supportés:
    - Python (.py) - AST natif
    - JavaScript (.js) - Regex patterns
    - TypeScript (.ts) - Regex patterns
    - Rust (.rs) - Regex patterns
    - Go (.go) - Regex patterns
    """
    
    # Regex patterns par langage
    PATTERNS = {
        "javascript": {
            "function": r"(?:async\s+)?function\s+(\w+)\s*\([^)]*\)",
            "arrow": r"(?:const|let|var)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>",
            "class": r"class\s+(\w+)(?:\s+extends\s+\w+)?",
            "method": r"(?:async\s+)?(\w+)\s*\([^)]*\)\s*{",
        },
        "typescript": {
            "function": r"(?:export\s+)?(?:async\s+)?function\s+(\w+)(?:<[^>]+>)?\s*\([^)]*\)",
            "arrow": r"(?:export\s+)?(?:const|let|var)\s+(\w+)(?::\s*[^=]+)?\s*=\s*(?:async\s+)?\([^)]*\)\s*=>",
            "class": r"(?:export\s+)?(?:abstract\s+)?class\s+(\w+)(?:<[^>]+>)?(?:\s+extends\s+\w+)?(?:\s+implements\s+\w+)?",
            "interface": r"(?:export\s+)?interface\s+(\w+)(?:<[^>]+>)?",
            "type": r"(?:export\s+)?type\s+(\w+)(?:<[^>]+>)?\s*=",
        },
        "rust": {
            "function": r"(?:pub\s+)?(?:async\s+)?fn\s+(\w+)(?:<[^>]+>)?\s*\([^)]*\)",
            "struct": r"(?:pub\s+)?struct\s+(\w+)(?:<[^>]+>)?",
            "enum": r"(?:pub\s+)?enum\s+(\w+)(?:<[^>]+>)?",
            "impl": r"impl(?:<[^>]+>)?\s+(\w+)",
            "trait": r"(?:pub\s+)?trait\s+(\w+)(?:<[^>]+>)?",
        },
        "go": {
            "function": r"func\s+(\w+)\s*\([^)]*\)",
            "method": r"func\s+\([^)]+\)\s+(\w+)\s*\([^)]*\)",
            "struct": r"type\s+(\w+)\s+struct",
            "interface": r"type\s+(\w+)\s+interface",
        }
    }
    
    EXTENSIONS = {
        ".py": "python",
        ".js": "javascript",
        ".jsx": "javascript",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".rs": "rust",
        ".go": "go",
    }
    
    def __init__(self):
        self._python_ast_available = True
        try:
            import ast
        except ImportError:
            self._python_ast_available = False
    
    def parse_file(self, file_path: str) -> List[CodeSymbol]:
        """
        Parse un fichier et retourne ses symboles.
        
        Args:
            file_path: Chemin du fichier
            
        Returns:
            Liste de CodeSymbol
        """
        path = Path(file_path)
        if not path.exists():
            return []
        
        ext = path.suffix.lower()
        lang = self.EXTENSIONS.get(ext)
        
        if not lang:
            return []
        
        content = path.read_text(encoding='utf-8', errors='ignore')
        
        if lang == "python":
            return self._parse_python(content)
        else:
            return self._parse_regex(content, lang)
    
    def _parse_python(self, content: str) -> List[CodeSymbol]:
        """Parse Python avec AST natif."""
        import ast
        
        symbols = []
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            logger.warning(f"Erreur syntaxe Python: {e}")
            return []
        
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                methods = []
                for item in node.body:
                    if isinstance(item, ast.FunctionDef):
                        methods.append(CodeSymbol(
                            name=item.name,
                            kind="method",
                            line_start=item.lineno,
                            line_end=item.end_lineno or item.lineno,
                            signature=self._get_python_signature(item)
                        ))
                
                symbols.append(CodeSymbol(
                    name=node.name,
                    kind="class",
                    line_start=node.lineno,
                    line_end=node.end_lineno or node.lineno,
                    signature=f"class {node.name}",
                    children=methods
                ))
            
            elif isinstance(node, ast.FunctionDef):
                symbols.append(CodeSymbol(
                    name=node.name,
                    kind="function",
                    line_start=node.lineno,
                    line_end=node.end_lineno or node.lineno,
                    signature=self._get_python_signature(node)
                ))
            
            elif isinstance(node, ast.AsyncFunctionDef):
                symbols.append(CodeSymbol(
                    name=node.name,
                    kind="function",
                    line_start=node.lineno,
                    line_end=node.end_lineno or node.lineno,
                    signature=f"async def {node.name}(...)"
                ))
        
        return symbols
    
    def _get_python_signature(self, node) -> str:
        """Génère la signature d'une fonction Python."""
        import ast
        
        args = []
        for arg in node.args.args:
            args.append(arg.arg)
        
        prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
        return f"{prefix}def {node.name}({', '.join(args)})"
    
    def _parse_regex(self, content: str, lang: str) -> List[CodeSymbol]:
        """Parse avec regex pour les autres langages."""
        patterns = self.PATTERNS.get(lang, {})
        symbols = []
        lines = content.split('\n')
        
        for kind, pattern in patterns.items():
            for i, line in enumerate(lines, 1):
                match = re.search(pattern, line)
                if match:
                    name = match.group(1)
                    # Estimer la fin (simpliste: cherche la prochaine accolade fermante au même niveau)
                    end_line = self._find_block_end(lines, i - 1)
                    
                    symbols.append(CodeSymbol(
                        name=name,
                        kind=kind,
                        line_start=i,
                        line_end=end_line,
                        signature=line.strip()[:100]
                    ))
        
        # Trier par ligne
        symbols.sort(key=lambda s: s.line_start)
        return symbols
    
    def _find_block_end(self, lines: List[str], start_idx: int) -> int:
        """Trouve la fin d'un bloc (simpliste)."""
        brace_count = 0
        started = False
        
        for i in range(start_idx, min(start_idx + 200, len(lines))):
            line = lines[i]
            brace_count += line.count('{') - line.count('}')
            
            if '{' in line:
                started = True
            
            if started and brace_count <= 0:
                return i + 1
        
        return min(start_idx + 50, len(lines))
    
    def format_outline(self, symbols: List[CodeSymbol], indent: int = 0) -> str:
        """Formate les symboles en outline lisible."""
        lines = []
        prefix = "  " * indent
        
        for sym in symbols:
            icon = {
                "class": "📦",
                "function": "🔧",
                "method": "  🔹",
                "struct": "🏗️",
                "interface": "📋",
                "enum": "📊",
                "trait": "🎭",
                "type": "📝",
            }.get(sym.kind, "•")
            
            lines.append(f"{prefix}{icon} {sym.name} (L{sym.line_start}-{sym.line_end})")
            
            if sym.children:
                for child in sym.children:
                    child_icon = "  🔹" if child.kind == "method" else "  •"
                    lines.append(f"{prefix}  {child_icon} {child.name}()")
        
        return "\n".join(lines)


# Singleton
_parser: Optional[MultiLanguageParser] = None


def get_parser() -> MultiLanguageParser:
    """Retourne l'instance du parser."""
    global _parser
    if _parser is None:
        _parser = MultiLanguageParser()
    return _parser


def parse_file_outline(file_path: str) -> str:
    """
    Fonction helper pour parser un fichier et retourner l'outline.
    
    Utilisée par le handler view_outline dans react.py.
    """
    parser = get_parser()
    symbols = parser.parse_file(file_path)
    
    if not symbols:
        return f"Aucun symbole trouvé dans {Path(file_path).name}"
    
    return parser.format_outline(symbols)
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
