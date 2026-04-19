"""
🌍 LUMENA — World Model

Modèle mental vivant du projet en cours d'édition.

Contrairement au RepoMap (vue statique du repo Lumena) et au CodeIndex
(index sémantique pré-calculé), le WorldModel suit l'état réel des fichiers
pendant la session d'un agent : structure (sections, classes, sélecteurs),
numéros de ligne, dernière itération de modification.

Objectif : éviter que l'agent ne relise / grep à l'aveugle un fichier
qu'il vient d'écrire ou de modifier. Il voit la structure à jour et
va directement à l'edit_lines / str_replace pertinent.

Support :
- Python : via AST natif (réutilise ast_parser.ASTParser)
- HTML   : regex sections <header>/<main>/<section>/<footer> + <h1-3 id=>
- CSS    : sélecteurs top-level, @media, commentaires de section /* === X === */
- JS/TS  : regex function / class / const fn top-level
- Autres : métadonnées seulement (lignes, iter)

Budget typique en prompt : 200-800 tokens selon nombre de fichiers actifs.
"""

from __future__ import annotations

import re
import ast
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from threading import RLock

from loguru import logger


# ── Dataclasses ─────────────────────────────────────────────────────────────


@dataclass
class Section:
    """Une section détectée dans un fichier (classe, sélecteur, balise…)."""
    name: str
    kind: str  # "class" | "function" | "method" | "selector" | "media" | "tag" | "comment"
    line_start: int
    line_end: int
    added_at_iter: Optional[int] = None  # itération où cette section est apparue

    def to_compact(self) -> str:
        suffix = f" [+ iter {self.added_at_iter}]" if self.added_at_iter is not None else ""
        return f"{self.name} (L{self.line_start}-{self.line_end}){suffix}"


@dataclass
class FileModel:
    """État d'un fichier vu par le WorldModel."""
    path: str                        # relative au workspace root
    total_lines: int = 0
    language: str = "unknown"
    sections: List[Section] = field(default_factory=list)
    last_edit_iter: int = 0
    last_action: str = ""            # "write_file" | "str_replace" | ...
    version: int = 0                 # incrémenté à chaque update

    def to_compact(self, max_sections: int = 12) -> str:
        """Format compact pour injection prompt."""
        header = f"{self.path} [{self.total_lines}L, iter {self.last_edit_iter} {self.last_action}]"
        if not self.sections:
            return header
        lines = [header]
        # Tri par ligne de début (ordre naturel du fichier)
        ordered = sorted(self.sections, key=lambda s: s.line_start)
        shown = ordered[:max_sections]
        for i, sec in enumerate(shown):
            connector = "├─" if i < len(shown) - 1 else "└─"
            lines.append(f"  {connector} {sec.to_compact()}")
        if len(ordered) > max_sections:
            lines.append(f"  ⋮ (+{len(ordered) - max_sections} sections)")
        return "\n".join(lines)


# ── Parsers par langage ─────────────────────────────────────────────────────


_CSS_SELECTOR_RE = re.compile(r"^\s*([#\.\w\[\]:,\-\*\s>\+~]+?)\s*\{", re.MULTILINE)
_CSS_MEDIA_RE = re.compile(r"^\s*(@media[^{]+?)\s*\{", re.MULTILINE)
_CSS_COMMENT_SECTION_RE = re.compile(r"/\*\s*=+\s*(.+?)\s*=+\s*\*/", re.DOTALL)
_HTML_TAG_RE = re.compile(
    r"<(header|main|footer|nav|section|article|aside|form)\b[^>]*>",
    re.IGNORECASE,
)
_HTML_HEADING_RE = re.compile(r"<(h[1-3])\b[^>]*>\s*(.+?)\s*</\1>", re.IGNORECASE)
_JS_FUNC_RE = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:function\s+(\w+)|class\s+(\w+)|const\s+(\w+)\s*=\s*(?:async\s+)?\()",
    re.MULTILINE,
)


def _line_of(text: str, offset: int) -> int:
    """1-based line of a character offset."""
    return text.count("\n", 0, offset) + 1


def _end_brace(text: str, open_idx: int) -> int:
    """Trouve l'index de l'accolade fermante correspondant à open_idx.
    Retourne open_idx si non trouvé (robust fallback).
    """
    depth = 0
    i = open_idx
    n = len(text)
    while i < n:
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return open_idx


def _parse_python(content: str) -> List[Section]:
    try:
        tree = ast.parse(content)
    except SyntaxError:
        return []
    secs: List[Section] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            kind = "async_function" if isinstance(node, ast.AsyncFunctionDef) else "function"
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            secs.append(Section(name=f"def {node.name}", kind=kind,
                                line_start=node.lineno, line_end=end))
        elif isinstance(node, ast.ClassDef):
            end = getattr(node, "end_lineno", node.lineno) or node.lineno
            secs.append(Section(name=f"class {node.name}", kind="class",
                                line_start=node.lineno, line_end=end))
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    iend = getattr(item, "end_lineno", item.lineno) or item.lineno
                    secs.append(Section(
                        name=f"  {node.name}.{item.name}",
                        kind="method",
                        line_start=item.lineno,
                        line_end=iend,
                    ))
    return secs


def _parse_css(content: str) -> List[Section]:
    secs: List[Section] = []
    # Commentaires de section /* === Responsive === */
    for m in _CSS_COMMENT_SECTION_RE.finditer(content):
        name = m.group(1).strip().splitlines()[0][:60]
        line = _line_of(content, m.start())
        secs.append(Section(name=f"/* {name} */", kind="comment",
                            line_start=line, line_end=line))
    # @media queries
    for m in _CSS_MEDIA_RE.finditer(content):
        name = m.group(1).strip()[:80]
        start_line = _line_of(content, m.start())
        brace_idx = content.find("{", m.end() - 1)
        end_line = _line_of(content, _end_brace(content, brace_idx)) if brace_idx >= 0 else start_line
        secs.append(Section(name=name, kind="media",
                            line_start=start_line, line_end=end_line))
    # Top-level selectors (ignore ceux dans @media : on saute les blocs enfants)
    # Approche pragmatique : tout sélecteur au niveau 0 (depth)
    depth = 0
    i = 0
    n = len(content)
    while i < n:
        c = content[i]
        if c == "{":
            if depth == 0:
                # Chercher le sélecteur en amont
                # Récup le texte depuis le dernier } ou début ou fin de commentaire
                lookback_start = max(
                    content.rfind("}", 0, i) + 1,
                    content.rfind("*/", 0, i) + 2 if content.rfind("*/", 0, i) != -1 else 0,
                    0,
                )
                selector_raw = content[lookback_start:i].strip()
                # Ignorer @media / @keyframes / @supports (déjà gérés ou non pertinents)
                if selector_raw and not selector_raw.lstrip().startswith("@"):
                    selector = selector_raw.replace("\n", " ")[:80]
                    start_line = _line_of(content, i - len(selector_raw))
                    end_idx = _end_brace(content, i)
                    end_line = _line_of(content, end_idx)
                    # Ne pas dédoubler si déjà marqué comme commentaire section
                    if not any(s.line_start == start_line for s in secs):
                        secs.append(Section(name=selector, kind="selector",
                                            line_start=start_line, line_end=end_line))
            depth += 1
        elif c == "}":
            depth = max(0, depth - 1)
        i += 1
    return secs


def _parse_html(content: str) -> List[Section]:
    secs: List[Section] = []
    seen_lines: set[int] = set()
    for m in _HTML_TAG_RE.finditer(content):
        tag = m.group(1).lower()
        line = _line_of(content, m.start())
        if line in seen_lines:
            continue
        seen_lines.add(line)
        # Attributs id/class ?
        attrs = content[m.start():m.end()]
        id_m = re.search(r'id\s*=\s*["\']([^"\']+)', attrs)
        cls_m = re.search(r'class\s*=\s*["\']([^"\']+)', attrs)
        suffix = ""
        if id_m:
            suffix = f"#{id_m.group(1)}"
        elif cls_m:
            suffix = f".{cls_m.group(1).split()[0]}"
        secs.append(Section(name=f"<{tag}{suffix}>", kind="tag",
                            line_start=line, line_end=line))
    for m in _HTML_HEADING_RE.finditer(content):
        level = m.group(1).lower()
        text = re.sub(r"<[^>]+>", "", m.group(2)).strip()[:50]
        line = _line_of(content, m.start())
        secs.append(Section(name=f"<{level}> {text}", kind="tag",
                            line_start=line, line_end=line))
    return secs


def _parse_js(content: str) -> List[Section]:
    secs: List[Section] = []
    for m in _JS_FUNC_RE.finditer(content):
        name = m.group(1) or m.group(2) or m.group(3)
        if not name:
            continue
        line = _line_of(content, m.start())
        kind = "class" if m.group(2) else "function"
        secs.append(Section(name=f"{kind} {name}", kind=kind,
                            line_start=line, line_end=line))
    return secs


_LANG_BY_EXT: Dict[str, str] = {
    ".py": "python",
    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "css",
    ".js": "javascript",
    ".mjs": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
}


def _detect_language(path: str) -> str:
    for ext, lang in _LANG_BY_EXT.items():
        if path.lower().endswith(ext):
            return lang
    return "unknown"


def _parse_content(path: str, content: str) -> Tuple[str, List[Section]]:
    lang = _detect_language(path)
    if lang == "python":
        return lang, _parse_python(content)
    if lang == "css":
        return lang, _parse_css(content)
    if lang == "html":
        return lang, _parse_html(content)
    if lang in ("javascript", "typescript"):
        return lang, _parse_js(content)
    return lang, []


# ── WorldModel ──────────────────────────────────────────────────────────────


class WorldModel:
    """
    Modèle mental vivant du projet en cours.

    Utilisation :
        wm = WorldModel(workspace_root)
        wm.update_from_write("css/style.css", content, iter_num=2)
        wm.update_from_edit("css/style.css", iter_num=6,
                            content_after=new_content, action="str_replace")
        print(wm.get_compact(max_files=10, max_tokens=800))
    """

    _instances: Dict[str, "WorldModel"] = {}
    _lock = RLock()

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = Path(workspace_root).resolve()
        self._files: Dict[str, FileModel] = {}
        self._state_lock = RLock()

    # ── Updates ────────────────────────────────────────────────────────────

    def _norm(self, path: str) -> str:
        p = str(path).replace("\\", "/").strip()
        # Rendre relatif au workspace si absolu
        try:
            abs_path = Path(p)
            if abs_path.is_absolute():
                rel = abs_path.resolve().relative_to(self.workspace_root)
                return str(rel).replace("\\", "/")
        except Exception:
            pass
        return p

    def update_from_write(
        self,
        path: str,
        content: str,
        iter_num: int = 0,
        action: str = "write_file",
    ) -> FileModel:
        """Enregistre la création/réécriture complète d'un fichier."""
        norm = self._norm(path)
        lang, sections = _parse_content(norm, content)
        # Marque les sections nouvelles comme ajoutées à cet iter
        for sec in sections:
            sec.added_at_iter = iter_num
        total_lines = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
        with self._state_lock:
            fm = FileModel(
                path=norm,
                total_lines=total_lines,
                language=lang,
                sections=sections,
                last_edit_iter=iter_num,
                last_action=action,
                version=(self._files[norm].version + 1) if norm in self._files else 1,
            )
            self._files[norm] = fm
        return fm

    def update_from_edit(
        self,
        path: str,
        iter_num: int = 0,
        content_after: Optional[str] = None,
        action: str = "str_replace",
    ) -> Optional[FileModel]:
        """Enregistre une modification partielle. Si content_after fourni, reparse.
        Sinon, marque juste le fichier comme touché à iter_num (structure obsolète).
        """
        norm = self._norm(path)
        with self._state_lock:
            prev = self._files.get(norm)
            if content_after is not None:
                lang, new_sections = _parse_content(norm, content_after)
                # Identifie nouvelles sections (par name+kind) pour marquer added_at_iter
                prev_keys = {(s.name, s.kind): s for s in prev.sections} if prev else {}
                for sec in new_sections:
                    key = (sec.name, sec.kind)
                    if key in prev_keys:
                        sec.added_at_iter = prev_keys[key].added_at_iter
                    else:
                        sec.added_at_iter = iter_num
                total_lines = content_after.count("\n") + (
                    1 if content_after and not content_after.endswith("\n") else 0
                )
                fm = FileModel(
                    path=norm,
                    total_lines=total_lines,
                    language=lang,
                    sections=new_sections,
                    last_edit_iter=iter_num,
                    last_action=action,
                    version=(prev.version + 1) if prev else 1,
                )
                self._files[norm] = fm
                return fm
            # Sans contenu : on incrémente version et on marque stale
            if prev:
                prev.last_edit_iter = iter_num
                prev.last_action = action
                prev.version += 1
                return prev
            # Fichier inconnu — on ne peut rien parser, on crée une entrée minimale
            fm = FileModel(path=norm, total_lines=0, language=_detect_language(norm),
                           sections=[], last_edit_iter=iter_num, last_action=action, version=1)
            self._files[norm] = fm
            return fm

    def forget(self, path: str) -> None:
        norm = self._norm(path)
        with self._state_lock:
            self._files.pop(norm, None)

    def clear(self) -> None:
        with self._state_lock:
            self._files.clear()

    # ── Queries ────────────────────────────────────────────────────────────

    def get_file(self, path: str) -> Optional[FileModel]:
        return self._files.get(self._norm(path))

    def active_files(self) -> List[FileModel]:
        """Retourne les fichiers triés par itération d'édition décroissante."""
        with self._state_lock:
            return sorted(self._files.values(), key=lambda f: f.last_edit_iter, reverse=True)

    def get_compact(self, max_files: int = 10, max_tokens: int = 800) -> str:
        """Format compact pour injection dans le prompt.
        Budget approximatif : 4 chars ≈ 1 token.
        """
        files = self.active_files()[:max_files]
        if not files:
            return ""
        header = "🌍 WORLD MODEL (structure live des fichiers modifiés cette session)"
        parts: List[str] = [header]
        budget = max_tokens * 4
        for fm in files:
            entry = fm.to_compact()
            cost = len(entry) + 2
            if cost > budget:
                parts.append(f"  ⋮ (+{len(files) - len(parts) + 1} fichiers)")
                break
            parts.append(entry)
            budget -= cost
        return "\n".join(parts)


# ── Singleton per workspace ─────────────────────────────────────────────────


def get_world_model(workspace_root: Optional[Path] = None) -> WorldModel:
    """Retourne l'instance du WorldModel pour ce workspace (singleton par path).
    Si workspace_root est None, retourne un WorldModel vide sur Path.cwd().
    """
    with WorldModel._lock:
        key = str(Path(workspace_root).resolve()) if workspace_root else str(Path.cwd().resolve())
        inst = WorldModel._instances.get(key)
        if inst is None:
            try:
                inst = WorldModel(Path(key))
            except Exception as exc:
                logger.debug(f"[WorldModel] init fallback: {exc}")
                inst = WorldModel(Path.cwd())
            WorldModel._instances[key] = inst
        return inst


def reset_world_model(workspace_root: Optional[Path] = None) -> None:
    """Réinitialise le WorldModel pour ce workspace (utile entre tâches)."""
    with WorldModel._lock:
        if workspace_root is None:
            WorldModel._instances.clear()
            return
        key = str(Path(workspace_root).resolve())
        WorldModel._instances.pop(key, None)
