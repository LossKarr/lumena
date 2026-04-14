"""
🌟 LUMENA - AST Parser pour Repo Map

Extrait les signatures de classes et fonctions d'un fichier source.
Utilise le module `ast` natif pour Python (pas de dépendance externe).
Pour d'autres langages, utilise des regex simples.

Peut être amélioré avec tree-sitter pour support multi-langage complet.
"""

import ast
import re
from pathlib import Path
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from loguru import logger


@dataclass
class CodeSymbol:
    """Un symbole de code (fonction, classe, méthode)."""
    name: str
    type: str  # 'function', 'class', 'method', 'async_function'
    signature: str  # La signature complète
    line_start: int
    line_end: int
    docstring: Optional[str] = None
    parent: Optional[str] = None  # Nom de la classe parente si méthode
    
    def to_compact(self) -> str:
        """Retourne une représentation compacte pour le repo map."""
        prefix = f"{self.parent}." if self.parent else ""
        symbol_type = "async " if "async" in self.type else ""
        is_callable = "function" in self.type or self.type == "method"
        return f"{symbol_type}def {prefix}{self.name}(...)" if is_callable else f"class {self.name}"


@dataclass 
class FileSignatures:
    """Signatures extraites d'un fichier."""
    path: str
    language: str
    symbols: List[CodeSymbol] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    error: Optional[str] = None
    
    @property
    def is_valid(self) -> bool:
        return self.error is None
    
    def to_map_entry(self, max_symbols: int = 10) -> str:
        """Formate pour le repo map."""
        if not self.symbols:
            return ""
        
        lines = [f"{self.path}:"]
        for symbol in self.symbols[:max_symbols]:
            indent = "  │ " if not symbol.parent else "  │   "
            lines.append(f"{indent}{symbol.signature}")
        
        if len(self.symbols) > max_symbols:
            lines.append(f"  ⋮... (+{len(self.symbols) - max_symbols} more)")
        
        return "\n".join(lines)


class ASTParser:
    """
    🔍 Parser AST multi-langage
    
    Extrait les signatures de code sans exécuter le code.
    Utilise ast pour Python, regex pour les autres langages.
    """
    
    SUPPORTED_EXTENSIONS = {
        '.py': 'python',
        '.js': 'javascript', 
        '.ts': 'typescript',
        '.jsx': 'javascript',
        '.tsx': 'typescript',
        '.go': 'go',
        '.rs': 'rust',
        '.java': 'java',
        '.cpp': 'cpp',
        '.c': 'c',
        '.h': 'c',
        '.hpp': 'cpp',
    }
    
    def __init__(self):
        self._cache: Dict[str, FileSignatures] = {}
    
    def parse_file(self, file_path: Path) -> FileSignatures:
        """
        Parse un fichier et extrait ses signatures.
        
        Args:
            file_path: Chemin vers le fichier
            
        Returns:
            FileSignatures avec les symboles extraits
        """
        path_str = str(file_path)
        
        # Vérifier le cache
        if path_str in self._cache:
            return self._cache[path_str]
        
        # Déterminer le langage
        ext = file_path.suffix.lower()
        language = self.SUPPORTED_EXTENSIONS.get(ext, 'unknown')
        
        if language == 'unknown':
            return FileSignatures(path=path_str, language=language, 
                                  error=f"Langage non supporté: {ext}")
        
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            return FileSignatures(path=path_str, language=language, 
                                  error=f"Erreur lecture: {e}")
        
        # Parser selon le langage
        if language == 'python':
            result = self._parse_python(content, path_str)
        else:
            result = self._parse_generic(content, path_str, language)
        
        # Mettre en cache
        self._cache[path_str] = result
        return result
    
    def _parse_python(self, content: str, path: str) -> FileSignatures:
        """Parse un fichier Python avec le module ast."""
        try:
            tree = ast.parse(content)
        except SyntaxError as e:
            return FileSignatures(path=path, language='python', 
                                  error=f"Erreur syntaxe: {e}")
        
        symbols = []
        imports = []
        
        for node in ast.walk(tree):
            # Imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ''
                for alias in node.names:
                    imports.append(f"{module}.{alias.name}")
        
        # Parcourir l'arbre pour les définitions de haut niveau
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                symbols.append(self._python_function_to_symbol(node))
            elif isinstance(node, ast.AsyncFunctionDef):
                symbols.append(self._python_function_to_symbol(node, is_async=True))
            elif isinstance(node, ast.ClassDef):
                class_symbol = self._python_class_to_symbol(node)
                symbols.append(class_symbol)
                
                # Ajouter les méthodes de la classe
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        method = self._python_function_to_symbol(
                            item, 
                            parent=node.name,
                            is_async=isinstance(item, ast.AsyncFunctionDef)
                        )
                        symbols.append(method)
        
        return FileSignatures(
            path=path,
            language='python',
            symbols=symbols,
            imports=imports[:20]  # Limiter les imports
        )
    
    def _python_function_to_symbol(
        self, 
        node: ast.FunctionDef, 
        parent: Optional[str] = None,
        is_async: bool = False
    ) -> CodeSymbol:
        """Convertit un noeud fonction Python en CodeSymbol."""
        # Construire la signature
        args = []
        for arg in node.args.args:
            arg_str = arg.arg
            if arg.annotation:
                arg_str += f": {ast.unparse(arg.annotation)}"
            args.append(arg_str)
        
        # Arguments avec valeurs par défaut
        defaults_offset = len(node.args.args) - len(node.args.defaults)
        for i, default in enumerate(node.args.defaults):
            idx = defaults_offset + i
            if idx < len(args):
                args[idx] += f" = ..."
        
        # *args et **kwargs
        if node.args.vararg:
            args.append(f"*{node.args.vararg.arg}")
        if node.args.kwarg:
            args.append(f"**{node.args.kwarg.arg}")
        
        # Return type
        return_str = ""
        if node.returns:
            return_str = f" -> {ast.unparse(node.returns)}"
        
        # Signature compacte
        args_str = ", ".join(args[:4])  # Max 4 args affichés
        if len(args) > 4:
            args_str += ", ..."
        
        async_prefix = "async " if is_async else ""
        signature = f"{async_prefix}def {node.name}({args_str}){return_str}"
        
        # Docstring
        docstring = ast.get_docstring(node)
        if docstring and len(docstring) > 100:
            docstring = docstring[:100] + "..."
        
        return CodeSymbol(
            name=node.name,
            type="async_function" if is_async else ("method" if parent else "function"),
            signature=signature,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            docstring=docstring,
            parent=parent
        )
    
    def _python_class_to_symbol(self, node: ast.ClassDef) -> CodeSymbol:
        """Convertit un noeud classe Python en CodeSymbol."""
        # Bases
        bases = []
        for base in node.bases[:3]:
            try:
                bases.append(ast.unparse(base))
            except Exception:
                bases.append("?")
        
        bases_str = f"({', '.join(bases)})" if bases else ""
        signature = f"class {node.name}{bases_str}"
        
        # Docstring
        docstring = ast.get_docstring(node)
        if docstring and len(docstring) > 100:
            docstring = docstring[:100] + "..."
        
        return CodeSymbol(
            name=node.name,
            type="class",
            signature=signature,
            line_start=node.lineno,
            line_end=node.end_lineno or node.lineno,
            docstring=docstring
        )
    
    def _parse_generic(self, content: str, path: str, language: str) -> FileSignatures:
        """Parse générique avec regex pour les autres langages."""
        symbols = []
        
        # Patterns par langage
        patterns = {
            'javascript': [
                (r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\([^)]*\)', 'function'),
                (r'(?:export\s+)?class\s+(\w+)', 'class'),
                (r'const\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*=>', 'function'),
            ],
            'typescript': [
                (r'(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*[<(]', 'function'),
                (r'(?:export\s+)?(?:abstract\s+)?class\s+(\w+)', 'class'),
                (r'(?:export\s+)?interface\s+(\w+)', 'interface'),
                (r'(?:export\s+)?type\s+(\w+)', 'type'),
            ],
            'go': [
                (r'func\s+(?:\([^)]+\)\s+)?(\w+)\s*\(', 'function'),
                (r'type\s+(\w+)\s+struct', 'struct'),
                (r'type\s+(\w+)\s+interface', 'interface'),
            ],
            'rust': [
                (r'(?:pub\s+)?fn\s+(\w+)', 'function'),
                (r'(?:pub\s+)?struct\s+(\w+)', 'struct'),
                (r'(?:pub\s+)?enum\s+(\w+)', 'enum'),
                (r'(?:pub\s+)?trait\s+(\w+)', 'trait'),
                (r'impl\s+(?:<[^>]+>\s+)?(\w+)', 'impl'),
            ],
            'java': [
                (r'(?:public|private|protected)?\s*(?:static\s+)?(?:class|interface)\s+(\w+)', 'class'),
                (r'(?:public|private|protected)?\s*(?:static\s+)?\w+\s+(\w+)\s*\([^)]*\)\s*(?:throws|{)', 'method'),
            ],
            'cpp': [
                (r'class\s+(\w+)', 'class'),
                (r'struct\s+(\w+)', 'struct'),
                (r'(?:\w+\s+)+(\w+)\s*\([^)]*\)\s*(?:const\s*)?(?:override\s*)?(?:=\s*0\s*)?[{;]', 'function'),
            ],
            'c': [
                (r'struct\s+(\w+)', 'struct'),
                (r'typedef\s+struct[^{]*{\s*[^}]*}\s*(\w+)', 'typedef'),
                (r'(?:\w+\s+)+\*?(\w+)\s*\([^)]*\)\s*{', 'function'),
            ],
        }
        
        lang_patterns = patterns.get(language, [])
        lines = content.split('\n')
        
        for pattern, symbol_type in lang_patterns:
            for match in re.finditer(pattern, content, re.MULTILINE):
                name = match.group(1)
                # Trouver le numéro de ligne
                line_num = content[:match.start()].count('\n') + 1
                
                symbols.append(CodeSymbol(
                    name=name,
                    type=symbol_type,
                    signature=f"{symbol_type} {name}",
                    line_start=line_num,
                    line_end=line_num
                ))
        
        # Dédupliquer par nom
        seen = set()
        unique_symbols = []
        for s in symbols:
            if s.name not in seen:
                seen.add(s.name)
                unique_symbols.append(s)
        
        return FileSignatures(
            path=path,
            language=language,
            symbols=unique_symbols
        )
    
    def clear_cache(self):
        """Vide le cache."""
        self._cache.clear()
    
    def invalidate(self, file_path: Path):
        """Invalide le cache pour un fichier spécifique."""
        path_str = str(file_path)
        if path_str in self._cache:
            del self._cache[path_str]


# Singleton
_parser: Optional[ASTParser] = None


def get_ast_parser() -> ASTParser:
    """Retourne l'instance globale du parser AST."""
    global _parser
    if _parser is None:
        _parser = ASTParser()
    return _parser


def get_import_graph(file_path: str, project_root: str | None = None) -> list[str]:
    """
    Retourne les chemins des imports locaux (au projet) d'un fichier Python.
    Parser dédié via ast.parse() — ne passe pas par _parse_python (cap [:20]).
    """
    if project_root is None:
        project_root = str(Path(__file__).parent.parent.parent)
    root = Path(project_root)
    fpath = Path(file_path)
    if not fpath.is_absolute():
        fpath = root / fpath
    if not fpath.exists() or fpath.suffix != ".py":
        return []
    try:
        tree = ast.parse(fpath.read_text(encoding="utf-8"))
    except Exception:
        return []
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.append(node.module)
    # Résoudre vers des fichiers locaux
    seen: set[str] = set()
    result: list[str] = []
    for mod in modules:
        # src.tools.apply_patch → src/tools/apply_patch.py
        rel = mod.replace(".", "/")
        candidates = [root / f"{rel}.py", root / rel / "__init__.py"]
        for c in candidates:
            if c.exists() and str(c) not in seen:
                seen.add(str(c))
                # Retourner chemin relatif au projet
                try:
                    result.append(str(c.relative_to(root)).replace("\\", "/"))
                except ValueError:
                    result.append(str(c))
    return result


# Test rapide si exécuté directement
if __name__ == "__main__":
    import sys
    
    parser = get_ast_parser()
    
    if len(sys.argv) > 1:
        file_path = Path(sys.argv[1])
    else:
        # Tester sur ce fichier
        file_path = Path(__file__)
    
    print(f"Parsing: {file_path}")
    result = parser.parse_file(file_path)
    
    if result.error:
        print(f"Error: {result.error}")
    else:
        print(f"Language: {result.language}")
        print(f"Symbols ({len(result.symbols)}):")
        for s in result.symbols:
            print(f"  - {s.signature} (L{s.line_start}-{s.line_end})")
        print(f"\nRepo Map Entry:")
        print(result.to_map_entry())
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
