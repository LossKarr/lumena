"""
🔍 LUMENA — Code Validator Engine

Moteur de validation inter-fichiers post-génération.
Remplace le manque de LSP par une analyse statique
qui détecte les incohérences avant écriture sur disque.

Analyse supportée :
  - HTML : liens <script src>, <link href>, <a href> → fichiers existants
  - CSS  : variables var(--x) vs :root { --x } — cohérence palette
  - JS   : import/require → fichiers existants, fonctions appelées vs définies
  - Python : import → fichiers existants, ast.parse() syntaxe
  - Inter-fichiers : références croisées (HTML→CSS→JS graph)

Retourne une liste de ValidationIssue avec severité + suggestion de fix.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, Dict, List, Optional, Set, Tuple

from loguru import logger


# ═══════════════════════════════════════════════════════════════
# DATA TYPES
# ═══════════════════════════════════════════════════════════════

class Severity(Enum):
    ERROR = "error"       # Cassera à l'exécution
    WARNING = "warning"   # Probablement cassé mais pas certain
    INFO = "info"         # Amélioration possible


@dataclass
class ValidationIssue:
    file_path: str
    line: int              # 0 = fichier entier
    severity: Severity
    code: str              # ex: "HTML_MISSING_SCRIPT", "CSS_UNDEFINED_VAR"
    message: str
    suggestion: str = ""   # Fix suggéré (texte humain ou code)

    def __str__(self) -> str:
        loc = f"{self.file_path}:{self.line}" if self.line else self.file_path
        return f"[{self.severity.value.upper()}] {loc} — {self.code}: {self.message}"


@dataclass
class ValidationReport:
    issues: List[ValidationIssue] = field(default_factory=list)
    files_checked: int = 0
    duration_ms: float = 0.0

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.ERROR]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == Severity.WARNING]

    @property
    def is_clean(self) -> bool:
        return len(self.errors) == 0

    def summary(self) -> str:
        e, w = len(self.errors), len(self.warnings)
        status = "✅ CLEAN" if self.is_clean else f"❌ {e} erreur(s)"
        return (
            f"{status} | {w} warning(s) | "
            f"{self.files_checked} fichiers vérifiés ({self.duration_ms:.0f}ms)"
        )

    def format_for_llm(self, max_issues: int = 20) -> str:
        """Format compact pour injection dans un prompt LLM de correction."""
        if self.is_clean:
            return "✅ Aucun problème détecté."
        lines = [f"🔍 VALIDATION: {self.summary()}\n"]
        for issue in self.issues[:max_issues]:
            prefix = "❌" if issue.severity == Severity.ERROR else "⚠️"
            lines.append(f"{prefix} {issue}")
            if issue.suggestion:
                lines.append(f"   💡 {issue.suggestion}")
        if len(self.issues) > max_issues:
            lines.append(f"\n... et {len(self.issues) - max_issues} autres problèmes")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════════
# VALIDATEURS PAR LANGAGE
# ═══════════════════════════════════════════════════════════════

def _validate_html(
    file_path: str,
    content: str,
    all_files: Dict[str, str],
) -> List[ValidationIssue]:
    """Valide un fichier HTML : refs vers scripts, styles, liens internes."""
    issues: List[ValidationIssue] = []
    file_dir = str(PurePosixPath(file_path).parent)

    # ── Scripts src ──
    for m in re.finditer(r'<script[^>]+src=["\']([^"\']+)["\']', content):
        src = m.group(1)
        if src.startswith(("http://", "https://", "//", "data:")):
            continue
        resolved = _resolve_relative_path(file_dir, src)
        if resolved not in all_files:
            line = content[:m.start()].count("\n") + 1
            issues.append(ValidationIssue(
                file_path=file_path, line=line, severity=Severity.ERROR,
                code="HTML_MISSING_SCRIPT",
                message=f'<script src="{src}"> → fichier inexistant: {resolved}',
                suggestion=f"Créer le fichier '{resolved}' ou corriger le chemin",
            ))

    # ── CSS link href ──
    for m in re.finditer(r'<link[^>]+href=["\']([^"\']+)["\']', content):
        href = m.group(1)
        if href.startswith(("http://", "https://", "//", "data:", "#")):
            continue
        # Ignore les fonts externes et CDN
        if "fonts.googleapis" in href or "cdnjs" in href or "cdn" in href:
            continue
        resolved = _resolve_relative_path(file_dir, href)
        if resolved not in all_files:
            line = content[:m.start()].count("\n") + 1
            issues.append(ValidationIssue(
                file_path=file_path, line=line, severity=Severity.ERROR,
                code="HTML_MISSING_STYLE",
                message=f'<link href="{href}"> → fichier inexistant: {resolved}',
                suggestion=f"Créer le fichier '{resolved}' ou corriger le chemin",
            ))

    # ── Images src ──
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', content):
        src = m.group(1)
        if src.startswith(("http://", "https://", "//", "data:", "blob:")):
            continue
        resolved = _resolve_relative_path(file_dir, src)
        if resolved not in all_files:
            line = content[:m.start()].count("\n") + 1
            issues.append(ValidationIssue(
                file_path=file_path, line=line, severity=Severity.WARNING,
                code="HTML_MISSING_IMAGE",
                message=f'<img src="{src}"> → fichier inexistant: {resolved}',
                suggestion=f"Ajouter un placeholder ou corriger le chemin",
            ))

    # ── IDs dupliqués ──
    ids_found: Dict[str, int] = {}
    for m in re.finditer(r'\bid=["\']([^"\']+)["\']', content):
        id_val = m.group(1)
        line = content[:m.start()].count("\n") + 1
        if id_val in ids_found:
            issues.append(ValidationIssue(
                file_path=file_path, line=line, severity=Severity.WARNING,
                code="HTML_DUPLICATE_ID",
                message=f'id="{id_val}" déjà déclaré ligne {ids_found[id_val]}',
                suggestion=f"Renommer l'un des deux IDs",
            ))
        else:
            ids_found[id_val] = line

    # ── data-page → section#page-xxx ──
    data_pages = set(m.group(1) for m in re.finditer(r'data-page=["\']([^"\']+)["\']', content))
    section_ids = set(m.group(1) for m in re.finditer(r'<section[^>]+id=["\']([^"\']+)["\']', content))
    for dp in data_pages:
        if dp not in section_ids:
            issues.append(ValidationIssue(
                file_path=file_path, line=0, severity=Severity.ERROR,
                code="HTML_MISSING_SECTION",
                message=f'data-page="{dp}" → aucune <section id="{dp}"> trouvée',
                suggestion=f'Ajouter <section id="{dp}" class="page">...</section>',
            ))

    return issues


def _validate_css(
    file_path: str,
    content: str,
    all_files: Dict[str, str],
) -> List[ValidationIssue]:
    """Valide un fichier CSS : variables :root vs var() usage."""
    issues: List[ValidationIssue] = []

    # ── Collecter les variables CSS définies dans :root ──
    defined_vars: Set[str] = set()
    # Chercher dans TOUS les fichiers CSS du projet (les vars peuvent être dans un autre fichier)
    for fp, fc in all_files.items():
        if fp.endswith(".css"):
            for m in re.finditer(r'--([\w-]+)\s*:', fc):
                defined_vars.add(f"--{m.group(1)}")

    # ── Vérifier les var() utilisées ──
    for m in re.finditer(r'var\(\s*(--[\w-]+)\s*(?:,\s*([^)]+))?\)', content):
        var_name = m.group(1)
        fallback = (m.group(2) or "").strip()
        if var_name not in defined_vars:
            # CSS valide : var(--x, fallback) ne casse pas si --x n'est pas défini.
            if fallback:
                continue
            line = content[:m.start()].count("\n") + 1
            issues.append(ValidationIssue(
                file_path=file_path, line=line, severity=Severity.ERROR,
                code="CSS_UNDEFINED_VAR",
                message=f'var({var_name}) utilisée mais jamais définie dans :root',
                suggestion=f"Ajouter '{var_name}: <valeur>;' dans :root {{}}",
            ))

    # ── Sélecteurs sans fermeture ──
    open_braces = content.count("{")
    close_braces = content.count("}")
    if open_braces != close_braces:
        issues.append(ValidationIssue(
            file_path=file_path, line=0, severity=Severity.ERROR,
            code="CSS_UNBALANCED_BRACES",
            message=f"Accolades déséquilibrées: {open_braces} '{{' vs {close_braces} '}}'",
            suggestion="Vérifier les blocs CSS non fermés",
        ))

    return issues


def _validate_js(
    file_path: str,
    content: str,
    all_files: Dict[str, str],
) -> List[ValidationIssue]:
    """Valide un fichier JS : imports, fonctions appelées, syntaxe basique."""
    issues: List[ValidationIssue] = []
    file_dir = str(PurePosixPath(file_path).parent)

    # ── Import/require vers fichier existant ──
    import_patterns = [
        re.compile(r'''import\s+.*?\s+from\s+['"](\.{1,2}/[^'"]+)['"]'''),
        re.compile(r'''require\(\s*['"](\.{1,2}/[^'"]+)['"]\s*\)'''),
    ]
    for pat in import_patterns:
        for m in pat.finditer(content):
            import_path = m.group(1)
            # Résoudre : ajouter .js si pas d'extension
            resolved = _resolve_relative_path(file_dir, import_path)
            candidates = [resolved, resolved + ".js", resolved + "/index.js"]
            if not any(c in all_files for c in candidates):
                line = content[:m.start()].count("\n") + 1
                issues.append(ValidationIssue(
                    file_path=file_path, line=line, severity=Severity.ERROR,
                    code="JS_MISSING_IMPORT",
                    message=f'import "{import_path}" → aucun fichier trouvé ({resolved})',
                    suggestion=f"Créer '{resolved}.js' ou corriger le chemin d'import",
                ))

    # ── Accolades/parenthèses équilibrées ──
    # Ignorer celles dans les strings et commentaires (heuristique simple)
    stripped = _strip_js_strings_and_comments(content)
    for opener, closer, name in [("{", "}", "accolades"), ("(", ")", "parenthèses"), ("[", "]", "crochets")]:
        o_count = stripped.count(opener)
        c_count = stripped.count(closer)
        if o_count != c_count:
            issues.append(ValidationIssue(
                file_path=file_path, line=0, severity=Severity.ERROR,
                code="JS_UNBALANCED_SYNTAX",
                message=f"{name} déséquilibrées: {o_count} '{opener}' vs {c_count} '{closer}'",
                suggestion=f"Fichier probablement tronqué — vérifier la fin du fichier",
            ))

    # ── Fonctions appelées depuis HTML (onclick, etc.) ──
    # Vérifier que les fonctions globales référencées dans HTML existent dans les JS
    js_functions: Set[str] = set()
    for m in re.finditer(r'(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:function|\([^)]*\)\s*=>))', content):
        name = m.group(1) or m.group(2)
        if name:
            js_functions.add(name)

    # ── Fonctions appelées dans le fichier mais jamais définies (intra-file + projet) ──
    # Skip pour les fichiers de test (test frameworks injectent des globals que nous ne connaissons pas)
    _basename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
    _is_test_file = _basename.startswith("test_") or _basename.startswith("test.") or ".test." in _basename or ".spec." in _basename or _basename.startswith("__test")
    if _is_test_file:
        return issues

    # Collecter toutes les définitions JS du projet
    all_project_js_funcs: Set[str] = set()
    for fp, c in all_files.items():
        if fp.endswith(".js"):
            # function declarations + arrow functions assigned to const/let/var
            for m in re.finditer(
                r'(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=\s*(?:function|\([^)]*\)\s*=>))',
                c,
            ):
                name = m.group(1) or m.group(2)
                if name:
                    all_project_js_funcs.add(name)
            # Class method definitions: `  methodName(args) {` or `async methodName(args) {`
            for m in re.finditer(
                r'^\s+(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{',
                c, re.MULTILINE,
            ):
                all_project_js_funcs.add(m.group(1))
            # Callback parameter names: `(resolve, reject) =>` or `function(resolve, reject)`
            for m in re.finditer(r'(?:function)?\s*\(([^)]+)\)\s*(?:=>|\{)', c):
                for param in m.group(1).split(","):
                    pname = param.strip().split("=")[0].strip().strip(".()")
                    if pname and pname.isidentifier():
                        all_project_js_funcs.add(pname)
            # window.xxx = ... or globalThis.xxx = ... (globally exposed)
            for m in re.finditer(r'(?:window|globalThis|self)\.\s*(\w+)\s*=', c):
                all_project_js_funcs.add(m.group(1))
            # Object property definitions: `xxx: function` or `xxx: (args) =>`
            for m in re.finditer(r'(\w+)\s*:\s*(?:function|\([^)]*\)\s*=>)', c):
                all_project_js_funcs.add(m.group(1))

    # Trouver les appels de type `nomFonction()` dans le fichier courant
    _BUILTIN_JS = frozenset({
        "alert", "confirm", "prompt", "console", "window", "document",
        "setTimeout", "setInterval", "clearTimeout", "clearInterval",
        "parseInt", "parseFloat", "JSON", "Math", "Date", "Array",
        "Object", "String", "Number", "Boolean", "history", "location",
        "navigator", "fetch", "XMLHttpRequest", "Promise", "Error",
        "Map", "Set", "Symbol", "Proxy", "Reflect", "WeakMap", "WeakSet",
        "requestAnimationFrame", "cancelAnimationFrame", "getComputedStyle",
        "addEventListener", "removeEventListener", "dispatchEvent",
        "require", "module", "exports", "import", "eval", "isNaN",
        "isFinite", "decodeURIComponent", "encodeURIComponent",
        "decodeURI", "encodeURI", "atob", "btoa",
        # Test frameworks (Jest / Vitest / Mocha / Jasmine)
        "describe", "it", "test", "expect", "beforeAll", "afterAll",
        "beforeEach", "afterEach", "jest", "vi", "mock", "spyOn",
        "assert", "suite", "setup", "teardown", "cy",
        # DOM insertion dynamique
        "insertAdjacentHTML", "createElement", "appendChild",
        "cloneNode", "replaceChild", "removeChild",
    })
    # Chercher les appels top-level `initXxx();` — typiquement dans DOMContentLoaded
    _called_funcs: Set[str] = set()
    for m in re.finditer(r'\b([a-zA-Z_$][\w$]*)\s*\(', stripped):
        name = m.group(1)
        if (
            name not in _BUILTIN_JS
            and not name[0].isupper()  # Ignorer constructeurs (new Something)
            and len(name) > 1
            and name not in ("if", "for", "while", "switch", "catch", "return", "typeof", "void", "delete",
                             "async", "await", "function", "class", "new", "throw", "yield", "super")
        ):
            _called_funcs.add(name)

    # Soustraire les fonctions définies → reste = non définies
    _undefined = _called_funcs - all_project_js_funcs
    # Exclure aussi les méthodes d'objet (xxx.method() → method seul n'est pas top-level)
    # On ne garde que les appels qui apparaissent en standalone (pas après un `.`)
    for func_name in sorted(_undefined):
        # Vérifier que l'appel n'est pas une méthode (précédé par ".")
        standalone_pattern = re.compile(r'(?<!\.)\b' + re.escape(func_name) + r'\s*\(')
        standalone_calls = standalone_pattern.findall(stripped)
        if not standalone_calls:
            continue
        # Trouver la première ligne d'appel
        if _has_typeof_function_guard(func_name, content):
            continue
        first_match = standalone_pattern.search(stripped)
        if first_match:
            line = content[:first_match.start()].count("\n") + 1
            issues.append(ValidationIssue(
                file_path=file_path, line=line, severity=Severity.ERROR,
                code="JS_UNDEFINED_FUNCTION",
                message=f"{func_name}() appelée mais jamais définie dans le projet",
                suggestion=f"Ajouter function {func_name}() {{...}} dans ce fichier ou un autre fichier JS",
            ))

    # Vérifier navigateTo si c'est un SPA
    if "navigateTo" in content or "data-page" in str(all_files.get("index.html", "")):
        # SPA détecté — vérifier que navigateTo est définie quelque part
        if "navigateTo" not in all_project_js_funcs and "navigateTo" in content:
            if not any(
                issue.code == "JS_UNDEFINED_FUNCTION" and "navigateTo" in issue.message
                for issue in issues
            ):
                issues.append(ValidationIssue(
                    file_path=file_path, line=0, severity=Severity.WARNING,
                    code="JS_UNDEFINED_FUNCTION",
                    message="navigateTo() appelée mais non définie dans le projet",
                    suggestion="Ajouter function navigateTo(page) {...} dans app.js",
                ))

    return issues


def _validate_python(
    file_path: str,
    content: str,
    all_files: Dict[str, str],
) -> List[ValidationIssue]:
    """Valide un fichier Python : syntaxe AST + imports relatifs."""
    issues: List[ValidationIssue] = []

    # ── Syntaxe AST ──
    try:
        tree = ast.parse(content)
    except SyntaxError as e:
        issues.append(ValidationIssue(
            file_path=file_path, line=e.lineno or 0, severity=Severity.ERROR,
            code="PY_SYNTAX_ERROR",
            message=f"Erreur de syntaxe: {e.msg}",
            suggestion=f"Corriger la syntaxe à la ligne {e.lineno}",
        ))
        return issues  # Pas possible d'analyser plus loin

    # ── Imports relatifs ──
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                # Import absolu — vérifier si c'est un module local
                module_path = node.module.replace(".", "/")
                candidates = [
                    f"{module_path}.py",
                    f"{module_path}/__init__.py",
                ]
                # Ne signaler que si ça ressemble à un import local du projet
                if module_path.startswith(("src/", "lib/", "utils/", "app/")):
                    if not any(c in all_files for c in candidates):
                        issues.append(ValidationIssue(
                            file_path=file_path, line=node.lineno, severity=Severity.WARNING,
                            code="PY_MISSING_MODULE",
                            message=f'import {node.module} → module introuvable dans le projet',
                            suggestion=f"Créer '{module_path}.py' ou corriger l'import",
                        ))

    # ── Fonctions/classes définies mais jamais utilisées (heuristique) ──
    defined_names: List[Tuple[str, int]] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if not node.name.startswith("_"):
                defined_names.append((node.name, node.lineno))
        elif isinstance(node, ast.ClassDef):
            if not node.name.startswith("_"):
                defined_names.append((node.name, node.lineno))

    # Note: vérification noms inutilisés désactivée (trop de faux positifs
    # sur les exports, entry points, décorateurs, etc.)

    return issues


# ═══════════════════════════════════════════════════════════════
# VALIDATION INTER-FICHIERS (CROSS-FILE)
# ═══════════════════════════════════════════════════════════════

def _validate_cross_file(all_files: Dict[str, str]) -> List[ValidationIssue]:
    """Validations qui nécessitent la vue globale de tous les fichiers."""
    issues: List[ValidationIssue] = []

    # ── HTML appelle des fonctions JS non définies (onclick, etc.) ──
    html_files = {fp: c for fp, c in all_files.items() if fp.endswith((".html", ".htm"))}
    js_files = {fp: c for fp, c in all_files.items() if fp.endswith(".js")}
    css_files = {fp: c for fp, c in all_files.items() if fp.endswith(".css")}

    # Collecter toutes les fonctions JS définies
    all_js_funcs: Set[str] = set()
    for js_content in js_files.values():
        for m in re.finditer(r'(?:function\s+(\w+)|(?:const|let|var)\s+(\w+)\s*=)', js_content):
            name = m.group(1) or m.group(2)
            if name:
                all_js_funcs.add(name)
        # Méthodes de classe: `methodName(...) {`
        for m in re.finditer(r'^\s+(?:async\s+)?(\w+)\s*\([^)]*\)\s*\{', js_content, re.MULTILINE):
            all_js_funcs.add(m.group(1))
        # Exports globaux: `window.xxx = ...`
        for m in re.finditer(r'(?:window|globalThis|self)\.\s*(\w+)\s*=', js_content):
            all_js_funcs.add(m.group(1))

    # Chercher les appels onclick/onsubmit etc. dans HTML
    for html_path, html_content in html_files.items():
        for m in re.finditer(r'on\w+=["\'](\w+)\s*\(', html_content):
            func_name = m.group(1)
            if func_name not in all_js_funcs and func_name not in (
                "alert", "confirm", "prompt", "console", "window", "document",
                "setTimeout", "setInterval", "clearTimeout", "clearInterval",
                "parseInt", "parseFloat", "JSON", "Math", "Date", "Array",
                "Object", "String", "Number", "Boolean", "history", "location",
                "navigator", "fetch", "XMLHttpRequest",
            ):
                line = html_content[:m.start()].count("\n") + 1
                issues.append(ValidationIssue(
                    file_path=html_path, line=line, severity=Severity.WARNING,
                    code="XREF_UNDEFINED_HANDLER",
                    message=f'onclick="{func_name}()" → fonction non définie dans les fichiers JS',
                    suggestion=f"Définir function {func_name}() dans l'un des fichiers JS",
                ))

    # ── JS querySelector/getElementById → classes/IDs doivent exister dans HTML ──
    if html_files and js_files:
        # Collecter toutes les classes et IDs définis dans le HTML
        all_html_classes: Set[str] = set()
        all_html_ids: Set[str] = set()
        all_html_data_attrs: Set[str] = set()  # ex: "data-target", "data-count"
        for html_content in html_files.values():
            # Classes
            for m in re.finditer(r'class=["\']([^"\']+)["\']', html_content):
                for cls in m.group(1).split():
                    all_html_classes.add(cls)
            # IDs
            for m in re.finditer(r'\bid=["\']([^"\']+)["\']', html_content):
                all_html_ids.add(m.group(1))
            # data-* attributes
            for m in re.finditer(r'\b(data-[\w-]+)', html_content):
                all_html_data_attrs.add(m.group(1))

        # Aussi collecter les classes/IDs/attrs depuis le DOM dynamique
        # dans les fichiers JS (template literals, insertAdjacentHTML, etc.)
        for js_content in js_files.values():
            for m in re.finditer(r'class=["\']([^"\']+)["\']', js_content):
                for cls in m.group(1).split():
                    all_html_classes.add(cls)
            for m in re.finditer(r'\bid=["\']([^"\']+)["\']', js_content):
                all_html_ids.add(m.group(1))
            for m in re.finditer(r'\b[\w$]+\.id\s*=\s*["\']([^"\']+)["\']', js_content):
                all_html_ids.add(m.group(1))
            for m in re.finditer(r'\.setAttribute\s*\(\s*["\']id["\']\s*,\s*["\']([^"\']+)["\']', js_content):
                all_html_ids.add(m.group(1))
            for m in re.finditer(r'\b[\w$]+\.className\s*=\s*["\']([^"\']+)["\']', js_content):
                for cls in m.group(1).split():
                    all_html_classes.add(cls)
            for m in re.finditer(r'\.classList\.add\s*\(([^)]*)\)', js_content):
                for cls in re.findall(r'["\']([^"\']+)["\']', m.group(1)):
                    all_html_classes.add(cls)
            for m in re.finditer(r'\b(data-[\w-]+)', js_content):
                all_html_data_attrs.add(m.group(1))
            for m in re.finditer(r'\.setAttribute\s*\(\s*["\'](data-[\w-]+)["\']', js_content):
                all_html_data_attrs.add(m.group(1))

        # Aussi collecter les classes depuis tous les CSS (pour les pseudo-classes JS)
        all_css_classes: Set[str] = set()
        for css_content in css_files.values():
            for m in re.finditer(r'\.([\w-]+)\s*[{:,\s]', css_content):
                all_css_classes.add(m.group(1))

        # Patterns JS qui ciblent des sélecteurs DOM
        _qs_patterns = [
            # querySelector('.class') / querySelectorAll('.class')
            re.compile(r'''querySelector(?:All)?\s*\(\s*['"]([^'"]+)['"]\s*\)'''),
            # getElementsByClassName('class')
            re.compile(r'''getElementsByClassName\s*\(\s*['"]([^'"]+)['"]\s*\)'''),
            # getElementById('id')
            re.compile(r'''getElementById\s*\(\s*['"]([^'"]+)['"]\s*\)'''),
        ]

        for js_path, js_content in js_files.items():
            stripped = _strip_js_strings_and_comments(js_content)
            # Re-scan on original content but only for querySelector patterns
            for pat in _qs_patterns:
                for m in pat.finditer(js_content):
                    selector = m.group(1)
                    line = js_content[:m.start()].count("\n") + 1

                    if "getElementById" in pat.pattern:
                        # getElementById — vérifier que l'ID existe dans HTML
                        if selector not in all_html_ids:
                            issues.append(ValidationIssue(
                                file_path=js_path, line=line, severity=Severity.ERROR,
                                code="XREF_JS_MISSING_ID",
                                message=f'getElementById("{selector}") → aucun id="{selector}" trouvé dans le HTML',
                                suggestion=f'Ajouter id="{selector}" dans le HTML ou corriger le sélecteur JS',
                            ))
                    elif "getElementsByClassName" in pat.pattern:
                        # getElementsByClassName — vérifier la classe
                        if selector not in all_html_classes:
                            issues.append(ValidationIssue(
                                file_path=js_path, line=line, severity=Severity.ERROR,
                                code="XREF_JS_MISSING_CLASS",
                                message=f'getElementsByClassName("{selector}") → aucune class="{selector}" trouvée dans le HTML',
                                suggestion=f'Ajouter class="{selector}" dans le HTML ou corriger le JS',
                            ))
                    else:
                        # querySelector/All — parser le sélecteur CSS
                        # Extraire les classes (.xxx), IDs (#xxx), data-attrs ([data-xxx])
                        _sel_classes = re.findall(r'\.([\w-]+)', selector)
                        _sel_ids = re.findall(r'#([\w-]+)', selector)
                        _sel_data = re.findall(r'\[(data-[\w-]+)', selector)

                        for cls in _sel_classes:
                            if cls not in all_html_classes and cls not in all_css_classes:
                                # Ignorer les pseudo-classes communes et les sélecteurs dynamiques
                                if cls in ("active", "visible", "hidden", "show", "open", "close",
                                           "disabled", "selected", "checked", "focused", "loading",
                                           "fade-in", "fade-out", "slide-in", "slide-out"):
                                    continue
                                issues.append(ValidationIssue(
                                    file_path=js_path, line=line, severity=Severity.ERROR,
                                    code="XREF_JS_MISSING_CLASS",
                                    message=f'querySelector("{selector}") cible .{cls} → classe inexistante dans le HTML',
                                    suggestion=f'Ajouter class="{cls}" dans le HTML ou utiliser le bon nom de classe',
                                ))
                        for id_val in _sel_ids:
                            if id_val not in all_html_ids:
                                issues.append(ValidationIssue(
                                    file_path=js_path, line=line, severity=Severity.ERROR,
                                    code="XREF_JS_MISSING_ID",
                                    message=f'querySelector("{selector}") cible #{id_val} → id inexistant dans le HTML',
                                    suggestion=f'Ajouter id="{id_val}" dans le HTML ou corriger le sélecteur',
                                ))
                        for data_attr in _sel_data:
                            if data_attr not in all_html_data_attrs:
                                issues.append(ValidationIssue(
                                    file_path=js_path, line=line, severity=Severity.ERROR,
                                    code="XREF_JS_MISSING_DATA_ATTR",
                                    message=f'querySelector("{selector}") cible [{data_attr}] → attribut inexistant dans le HTML',
                                    suggestion=f'Ajouter {data_attr}="..." dans le HTML ou corriger le sélecteur JS',
                                ))

    # ── Détection double @import CSS ──
    for css_path, css_content in css_files.items():
        _imports_seen: Dict[str, int] = {}
        for m in re.finditer(r'@import\s+(?:url\()?["\']?([^"\')\s;]+)', css_content):
            import_ref = m.group(1)
            line = css_content[:m.start()].count("\n") + 1
            if import_ref in _imports_seen:
                issues.append(ValidationIssue(
                    file_path=css_path, line=line, severity=Severity.WARNING,
                    code="CSS_DUPLICATE_IMPORT",
                    message=f'@import "{import_ref}" déjà importé à la ligne {_imports_seen[import_ref]}',
                    suggestion="Supprimer le @import en double",
                ))
            else:
                _imports_seen[import_ref] = line

    # ── Validation ES6 modules : <script> sans type="module" mais fichier utilise import/export ──
    if html_files and js_files:
        # Construire un mapping nom-fichier → chemin complet JS
        _js_name_to_path: Dict[str, str] = {}
        for js_path in js_files:
            _js_name = js_path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
            _js_name_to_path[_js_name] = js_path
            # Aussi avec chemin partiel (ex: "js/main.js")
            _js_name_to_path[js_path] = js_path

        # Détecter quels JS utilisent import/export top-level
        _js_uses_modules: Dict[str, bool] = {}
        for js_path, js_content in js_files.items():
            # Vérifier import/export au niveau module (pas dans des strings/commentaires)
            _stripped = _strip_js_strings_and_comments(js_content)
            _uses = bool(
                re.search(r'^\s*(?:import\s|export\s|export\s*\{|export\s+default)', _stripped, re.MULTILINE)
            )
            _js_uses_modules[js_path] = _uses

        # Scanner les <script src="..."> dans le HTML
        for html_path, html_content in html_files.items():
            for m in re.finditer(
                r'<script\b([^>]*)>',
                html_content,
                re.IGNORECASE,
            ):
                attrs = m.group(1)
                # Récupérer src=
                src_m = re.search(r'\bsrc\s*=\s*["\']([^"\']+)["\']', attrs, re.IGNORECASE)
                if not src_m:
                    continue
                src = src_m.group(1).split("?")[0].split("#")[0]
                has_module = bool(re.search(r'\btype\s*=\s*["\']module["\']', attrs, re.IGNORECASE))

                # Résoudre le fichier JS correspondant
                _match_path: Optional[str] = None
                for candidate in (src, src.lstrip("./"), src.rsplit("/", 1)[-1]):
                    if candidate in _js_name_to_path:
                        _match_path = _js_name_to_path[candidate]
                        break

                if _match_path is None:
                    continue

                line = html_content[:m.start()].count("\n") + 1
                if _js_uses_modules.get(_match_path) and not has_module:
                    issues.append(ValidationIssue(
                        file_path=html_path, line=line, severity=Severity.ERROR,
                        code="HTML_SCRIPT_MISSING_MODULE_TYPE",
                        message=(
                            f'<script src="{src}"> manque type="module" '
                            f'mais {_match_path} utilise import/export ES6'
                        ),
                        suggestion=f'Changer en <script src="{src}" type="module" defer>',
                    ))
                elif not _js_uses_modules.get(_match_path) and has_module:
                    # Inutile mais pas bloquant — warning seulement
                    issues.append(ValidationIssue(
                        file_path=html_path, line=line, severity=Severity.WARNING,
                        code="HTML_SCRIPT_UNNECESSARY_MODULE_TYPE",
                        message=(
                            f'<script src="{src}" type="module"> mais {_match_path} '
                            f"n'utilise pas import/export (inutile)"
                        ),
                        suggestion=f'Retirer type="module" si le fichier ne contient pas d\'import/export',
                    ))

    # ── Détection duplication de handlers (même fonctionnalité attachée 2+ fois) ──
    if html_files and js_files:
        # Collecter les fonctions appelées via onclick= dans HTML
        _onclick_funcs: Set[str] = set()
        for html_content in html_files.values():
            for m in re.finditer(r'on(?:click|submit|change)\s*=\s*["\'](\w+)\s*\(', html_content):
                _onclick_funcs.add(m.group(1))

        # Collecter les addEventListener ciblant les mêmes éléments dans JS
        for js_path, js_content in js_files.items():
            for m in re.finditer(
                r"addEventListener\s*\(\s*['\"]click['\"]\s*,\s*(?:function\s*\([^)]*\)\s*\{[^}]*?(\w+)\s*\(|(\w+)\s*\))",
                js_content,
            ):
                func = m.group(1) or m.group(2)
                if func and func in _onclick_funcs:
                    line = js_content[:m.start()].count("\n") + 1
                    issues.append(ValidationIssue(
                        file_path=js_path, line=line, severity=Severity.WARNING,
                        code="XREF_DUPLICATE_HANDLER",
                        message=f'{func}() est déjà attachée via onclick= dans le HTML ET via addEventListener dans le JS',
                        suggestion=f"Choisir UN SEUL mécanisme : soit onclick= dans le HTML, soit addEventListener dans le JS",
                    ))

    return issues


# ═══════════════════════════════════════════════════════════════
# VALIDATION INTER-FICHIERS PYTHON (NOM DES IMPORTS)
# ═══════════════════════════════════════════════════════════════

def _validate_cross_python(files: Dict[str, str]) -> List[ValidationIssue]:
    """Vérifie que les noms importés depuis des modules locaux existent réellement."""
    issues: List[ValidationIssue] = []
    py_files = {k: v for k, v in files.items() if k.endswith(".py")}
    if len(py_files) < 2:
        return issues

    # Construire un index: module_stem → set(noms_définis)
    _defined: Dict[str, Set[str]] = {}
    for fp, content in py_files.items():
        stem = fp.rsplit("/", 1)[-1].removesuffix(".py") if "/" in fp else fp.removesuffix(".py")
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        names: Set[str] = set()
        for node in ast.iter_child_nodes(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names.add(node.name)
            elif isinstance(node, ast.ClassDef):
                names.add(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        names.add(t.id)
        _defined[stem] = names

    # Vérifier chaque import from local
    for fp, content in py_files.items():
        try:
            tree = ast.parse(content)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if not node.module or node.level > 0:
                continue
            # Seuls les imports locaux (module_stem existe dans le projet)
            mod_stem = node.module.split(".")[-1]
            if mod_stem not in _defined:
                continue
            defined_names = _defined[mod_stem]
            for alias in node.names:
                if alias.name == "*":
                    continue
                if alias.name not in defined_names:
                    issues.append(ValidationIssue(
                        file_path=fp,
                        line=getattr(node, "lineno", 0),
                        severity=Severity.WARNING,
                        code="PY_MISSING_NAME",
                        message=f"from {node.module} import {alias.name} → '{alias.name}' introuvable dans {mod_stem}.py",
                        suggestion=f"Ajouter 'def {alias.name}' ou 'class {alias.name}' dans {mod_stem}.py, ou corriger le nom",
                    ))
    return issues


# ═══════════════════════════════════════════════════════════════
# VALIDATION INTER-FICHIERS NODE.JS (REQUIRE/IMPORT)
# ═══════════════════════════════════════════════════════════════

_RE_JS_REQUIRE = re.compile(r"""require\s*\(\s*['"](\.[^'"]+)['"]\s*\)""")
_RE_JS_IMPORT_FROM = re.compile(r"""(?:import|export)\s+.*?\s+from\s+['"](\.[^'"]+)['"]""")


def _validate_cross_node(files: Dict[str, str]) -> List[ValidationIssue]:
    """Vérifie que les require('./...') et import from './...' pointent vers des fichiers existants."""
    issues: List[ValidationIssue] = []
    js_files = {k: v for k, v in files.items() if k.rsplit(".", 1)[-1].lower() in ("js", "mjs", "jsx", "ts", "tsx")}
    if not js_files:
        return issues

    all_paths = set(files.keys())

    for fp, content in js_files.items():
        base_dir = fp.rsplit("/", 1)[0] if "/" in fp else "."
        refs = set()
        for m in _RE_JS_REQUIRE.finditer(content):
            refs.add((m.group(1), m.start()))
        for m in _RE_JS_IMPORT_FROM.finditer(content):
            refs.add((m.group(1), m.start()))

        for ref_path, _pos in refs:
            # Résoudre le chemin relatif
            resolved = _resolve_relative_path(base_dir, ref_path)
            # Essayer avec et sans extensions
            candidates = [resolved]
            if "." not in resolved.rsplit("/", 1)[-1]:
                candidates.extend([
                    f"{resolved}.js", f"{resolved}.ts", f"{resolved}.jsx", f"{resolved}.tsx",
                    f"{resolved}/index.js", f"{resolved}/index.ts",
                ])
            if not any(c in all_paths for c in candidates):
                line_no = content[:_pos].count("\n") + 1
                issues.append(ValidationIssue(
                    file_path=fp,
                    line=line_no,
                    severity=Severity.ERROR,
                    code="NODE_MISSING_MODULE",
                    message=f"require/import '{ref_path}' → fichier introuvable dans le projet",
                    suggestion=f"Créer '{resolved}.js' ou corriger le chemin d'import",
                ))
    return issues


# ═══════════════════════════════════════════════════════════════
# HELPERS
# ═══════════════════════════════════════════════════════════════

def _resolve_relative_path(base_dir: str, href: str) -> str:
    """Résout un chemin relatif depuis un répertoire de base."""
    href = href.split("?")[0].split("#")[0]  # Retirer query et fragment
    if not base_dir or base_dir == ".":
        return href.lstrip("./")
    try:
        resolved = str(PurePosixPath(base_dir) / href)
        # Normaliser (résoudre les ..)
        parts = []
        for part in resolved.replace("\\", "/").split("/"):
            if part == "..":
                if parts:
                    parts.pop()
            elif part and part != ".":
                parts.append(part)
        return "/".join(parts)
    except Exception:
        return href


def _strip_js_strings_and_comments(content: str) -> str:
    """Retire strings/commentaires JS en conservant les offsets et retours ligne."""
    out: list[str] = []
    i = 0
    n = len(content)
    mode = "code"
    quote = ""
    escaped = False

    while i < n:
        ch = content[i]
        nxt = content[i + 1] if i + 1 < n else ""

        if mode == "code":
            if ch == "/" and nxt == "/":
                out.extend((" ", " "))
                i += 2
                mode = "line_comment"
                continue
            if ch == "/" and nxt == "*":
                out.extend((" ", " "))
                i += 2
                mode = "block_comment"
                continue
            if ch in ("'", '"', "`"):
                quote = ch
                escaped = False
                out.append(" ")
                i += 1
                mode = "string"
                continue
            out.append(ch)
            i += 1
            continue

        if mode == "line_comment":
            out.append("\n" if ch == "\n" else " ")
            i += 1
            if ch == "\n":
                mode = "code"
            continue

        if mode == "block_comment":
            if ch == "*" and nxt == "/":
                out.extend((" ", " "))
                i += 2
                mode = "code"
                continue
            out.append("\n" if ch == "\n" else " ")
            i += 1
            continue

        # string/template literal. On masque tout, y compris le contenu GLSL/HTML,
        # afin que les appels comme max() dans un shader ne soient pas vus comme JS.
        if escaped:
            out.append("\n" if ch == "\n" else " ")
            escaped = False
            i += 1
            continue
        if ch == "\\":
            out.append(" ")
            escaped = True
            i += 1
            continue
        if ch == quote:
            out.append(" ")
            i += 1
            mode = "code"
            continue
        out.append("\n" if ch == "\n" else " ")
        i += 1

    return "".join(out)


def _has_typeof_function_guard(name: str, content: str) -> bool:
    """Retourne True si un appel variable() est protégé par typeof variable === 'function'."""
    return bool(
        re.search(
            r'typeof\s+' + re.escape(name) + r'\s*={2,3}\s*["\']function["\']',
            content,
        )
    )


# ═══════════════════════════════════════════════════════════════
# API PUBLIQUE
# ═══════════════════════════════════════════════════════════════

def validate_project(
    files: Dict[str, str],
    project_dir: Optional[Path] = None,
) -> ValidationReport:
    """
    Valide un ensemble de fichiers de projet.

    Args:
        files: Dict {chemin_relatif: contenu} de tous les fichiers du projet
        project_dir: Répertoire du projet (optionnel, pour vérifier les fichiers existants sur disque)

    Returns:
        ValidationReport avec toutes les issues trouvées
    """
    import time
    start = time.perf_counter()

    # Si project_dir fourni, inclure les fichiers existants sur disque
    all_files = {k.replace("\\", "/"): v for k, v in files.items()}
    if project_dir and project_dir.exists():
        for fp in project_dir.rglob("*"):
            if fp.is_file() and not any(p.startswith(".") for p in fp.relative_to(project_dir).parts):
                rel = str(fp.relative_to(project_dir)).replace("\\", "/")
                if rel not in all_files:
                    try:
                        all_files[rel] = fp.read_text(encoding="utf-8", errors="replace")
                    except Exception as e:
                        logger.debug(f"Read file {rel}: {e}")

    report = ValidationReport(files_checked=len(files))
    all_issues: List[ValidationIssue] = []

    # ── Validation par fichier ──
    for file_path, content in files.items():
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""

        if ext in ("html", "htm"):
            all_issues.extend(_validate_html(file_path, content, all_files))
        elif ext == "css":
            all_issues.extend(_validate_css(file_path, content, all_files))
        elif ext in ("js", "mjs", "jsx", "ts", "tsx"):
            all_issues.extend(_validate_js(file_path, content, all_files))
        elif ext == "py":
            all_issues.extend(_validate_python(file_path, content, all_files))

    # ── Validation inter-fichiers ──
    all_issues.extend(_validate_cross_file(all_files))
    all_issues.extend(_validate_cross_python(all_files))
    all_issues.extend(_validate_cross_node(all_files))

    # ── Validation LSP (si disponible et project_dir fourni) ──
    if project_dir and project_dir.exists():
        lsp_issues = _run_lsp_diagnostics(project_dir, list(files.keys()))
        all_issues.extend(lsp_issues)

    report.issues = all_issues
    report.duration_ms = (time.perf_counter() - start) * 1000
    return report


def _run_lsp_diagnostics(
    project_dir: Path,
    file_paths: List[str],
) -> List[ValidationIssue]:
    """Lance les diagnostics LSP de manière synchrone (best-effort)."""
    try:
        from .lsp_client import lsp_check_project, DiagnosticSeverity
    except ImportError:
        return []

    import asyncio

    # Convertir DiagnosticSeverity → notre Severity
    _sev_map = {
        DiagnosticSeverity.ERROR: Severity.ERROR,
        DiagnosticSeverity.WARNING: Severity.WARNING,
        DiagnosticSeverity.INFORMATION: Severity.INFO,
        DiagnosticSeverity.HINT: Severity.INFO,
    }

    try:
        # Essayer d'obtenir un event loop existant
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Déjà dans un event loop (appel depuis un handler async)
            # → on ne peut pas faire asyncio.run(), on skip LSP
            # Le handler peut appeler lsp_check_project directement
            return []

        diags = asyncio.run(lsp_check_project(project_dir, file_paths, timeout=15.0))

        issues: List[ValidationIssue] = []
        for d in diags:
            # Convertir le chemin absolu en relatif si possible
            try:
                rel_path = str(Path(d.file_path).relative_to(project_dir)).replace("\\", "/")
            except ValueError:
                rel_path = d.file_path

            issues.append(ValidationIssue(
                file_path=rel_path,
                line=d.line + 1,  # LSP est 0-based, nous sommes 1-based
                severity=_sev_map.get(d.severity, Severity.WARNING),
                code=f"LSP_{d.source}_{d.code}" if d.code else f"LSP_{d.source}",
                message=d.message,
                suggestion="",
            ))
        if issues:
            logger.info("[code_validator] LSP: {} diagnostic(s) supplémentaires", len(issues))
        return issues
    except Exception as e:
        logger.debug("[code_validator] LSP diagnostics skip: {}", e)
        return []


def validate_directory(project_dir: Path) -> ValidationReport:
    """
    Valide un répertoire de projet complet sur disque.
    """
    files: Dict[str, str] = {}
    for fp in project_dir.rglob("*"):
        if fp.is_file():
            rel = str(fp.relative_to(project_dir)).replace("\\", "/")
            # Skip hidden, node_modules, etc.
            if any(part.startswith(".") for part in fp.relative_to(project_dir).parts):
                continue
            if "node_modules" in rel or "__pycache__" in rel:
                continue
            try:
                files[rel] = fp.read_text(encoding="utf-8", errors="replace")
            except Exception as e:
                logger.debug(f"Read file {rel}: {e}")

    return validate_project(files, project_dir)


async def validate_project_async(
    files: Dict[str, str],
    project_dir: Optional[Path] = None,
) -> ValidationReport:
    """
    Version async de validate_project — utilise LSP directement via await.
    À utiliser depuis les handlers async (ReAct, project, website).
    """
    # D'abord le check régulier (synchrone, rapide)
    report = validate_project(files, project_dir)

    # Ajouter les diagnostics LSP si possible
    if project_dir and project_dir.exists():
        try:
            from .lsp_client import lsp_check_project, DiagnosticSeverity

            _sev_map = {
                DiagnosticSeverity.ERROR: Severity.ERROR,
                DiagnosticSeverity.WARNING: Severity.WARNING,
                DiagnosticSeverity.INFORMATION: Severity.INFO,
                DiagnosticSeverity.HINT: Severity.INFO,
            }

            diags = await lsp_check_project(project_dir, list(files.keys()), timeout=15.0)
            for d in diags:
                try:
                    rel_path = str(Path(d.file_path).relative_to(project_dir)).replace("\\", "/")
                except ValueError:
                    rel_path = d.file_path
                report.issues.append(ValidationIssue(
                    file_path=rel_path,
                    line=d.line + 1,
                    severity=_sev_map.get(d.severity, Severity.WARNING),
                    code=f"LSP_{d.source}_{d.code}" if d.code else f"LSP_{d.source}",
                    message=d.message,
                ))
            if diags:
                logger.info("[code_validator] LSP async: {} diagnostic(s)", len(diags))
        except Exception as e:
            logger.debug("[code_validator] LSP async skip: {}", e)

    return report
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
