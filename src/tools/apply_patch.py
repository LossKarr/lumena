"""
🔧 LUMENA - Smart Patching System

Permet d'appliquer des modifications ciblées aux fichiers
sans réécrire le contenu complet → économie de tokens.

Format du patch:
*** Begin Patch
*** Add File: path/to/new_file.py
[contenu du fichier]
*** End File

*** Update File: path/to/existing.py
@@
- old_line
+ new_line
@@ function_name
- def function_name():
+ def function_name(param):
*** End File

*** Delete File: path/to/remove.py
*** End Patch
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple, Union
import re
import logging
from datetime import datetime

logger = logging.getLogger("lumena.apply_patch")

# Markers
BEGIN_PATCH = "*** Begin Patch"
END_PATCH = "*** End Patch"
ADD_FILE = "*** Add File:"
UPDATE_FILE = "*** Update File:"
DELETE_FILE = "*** Delete File:"
MOVE_TO = "*** Move to:"
END_FILE = "*** End File"
CHANGE_CONTEXT = "@@"
EOF_MARKER = "*** End of File"

# Unicode spaces to normalize
UNICODE_SPACES = re.compile(r'[\u00A0\u2000-\u200A\u202F\u205F\u3000]')

# ── Ponctuation Unicode → ASCII (smart quotes, dashes, etc.) ──
_SMART_SINGLE_QUOTES = re.compile(r'[\u2018\u2019\u201A\u201B]')
_SMART_DOUBLE_QUOTES = re.compile(r'[\u201C\u201D\u201E\u201F]')
_SMART_DASHES = re.compile(r'[\u2010\u2013\u2014\u2212]')


def _normalize_punctuation(text: str) -> str:
    """Normalise les guillemets courbes et tirets Unicode en ASCII."""
    text = _SMART_SINGLE_QUOTES.sub("'", text)
    text = _SMART_DOUBLE_QUOTES.sub('"', text)
    text = _SMART_DASHES.sub('-', text)
    text = UNICODE_SPACES.sub(' ', text)
    return text


def seek_sequence(
    lines: List[str],
    pattern: List[str],
    start: int = 0,
    eof: bool = False,
) -> int:
    """
    Cherche une séquence de lignes dans un fichier avec matching 4-passes.

    Pass 1: exact
    Pass 2: trimEnd (trailing whitespace)
    Pass 3: trim complet (leading+trailing)
    Pass 4: trim + normalisation Unicode (smart quotes, dashes, espaces)

    Args:
        lines: lignes du fichier
        pattern: lignes recherchées
        start: index de départ
        eof: si True, commence la recherche depuis la fin

    Returns:
        index de la première ligne matchée

    Raises:
        ValueError si pattern non trouvé
    """
    if not pattern:
        return start

    plen = len(pattern)
    search_start = start
    if eof and len(lines) >= plen:
        search_start = max(start, len(lines) - plen)

    # Pass 1 : exact
    for i in range(search_start, len(lines) - plen + 1):
        if all(lines[i + j] == pattern[j] for j in range(plen)):
            return i

    # Pass 2 : trimEnd (trailing whitespace)
    pat_rstrip = [p.rstrip() for p in pattern]
    for i in range(search_start, len(lines) - plen + 1):
        if all(lines[i + j].rstrip() == pat_rstrip[j] for j in range(plen)):
            return i

    # Pass 3 : trim complet
    pat_strip = [p.strip() for p in pattern]
    for i in range(search_start, len(lines) - plen + 1):
        if all(lines[i + j].strip() == pat_strip[j] for j in range(plen)):
            return i

    # Pass 4 : trim + normalisation ponctuation Unicode
    pat_norm = [_normalize_punctuation(p.strip()) for p in pattern]
    for i in range(search_start, len(lines) - plen + 1):
        if all(_normalize_punctuation(lines[i + j].strip()) == pat_norm[j] for j in range(plen)):
            return i

    # Trailing newline tolerance : si le dernier élément du pattern est vide,
    # réessayer sans (gère LF trailing vs non-trailing).
    if len(pattern) > 1 and pattern[-1].strip() == "":
        try:
            return seek_sequence(lines, pattern[:-1], start, eof)
        except ValueError:
            pass

    raise ValueError(
        f"Pattern ({plen} lignes) non trouvé à partir de la ligne {start}. "
        f"Premières lignes du pattern: {pattern[:3]!r}"
    )


@dataclass
class UpdateChunk:
    """Un chunk de modification dans un fichier."""
    context: Optional[str] = None  # Ex: "function_name" pour cibler
    old_lines: List[str] = field(default_factory=list)
    new_lines: List[str] = field(default_factory=list)
    is_end_of_file: bool = False


@dataclass
class AddFileHunk:
    """Hunk pour ajouter un nouveau fichier."""
    kind: str = "add"
    path: str = ""
    contents: str = ""


@dataclass
class DeleteFileHunk:
    """Hunk pour supprimer un fichier."""
    kind: str = "delete"
    path: str = ""


@dataclass
class UpdateFileHunk:
    """Hunk pour modifier un fichier existant."""
    kind: str = "update"
    path: str = ""
    move_path: Optional[str] = None
    chunks: List[UpdateChunk] = field(default_factory=list)


Hunk = Union[AddFileHunk, DeleteFileHunk, UpdateFileHunk]


@dataclass
class PatchResult:
    """Résultat de l'application d'un patch."""
    success: bool
    added: List[str] = field(default_factory=list)
    modified: List[str] = field(default_factory=list)
    deleted: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    
    def summary(self) -> str:
        """Génère un résumé du patch."""
        parts = []
        if self.added:
            parts.append(f"✅ Créés: {', '.join(self.added)}")
        if self.modified:
            parts.append(f"📝 Modifiés: {', '.join(self.modified)}")
        if self.deleted:
            parts.append(f"🗑️ Supprimés: {', '.join(self.deleted)}")
        if self.errors:
            parts.append(f"❌ Erreurs: {'; '.join(self.errors)}")
        return "\n".join(parts) if parts else "Aucune modification"


def normalize_unicode_spaces(text: str) -> str:
    """Normalise les espaces Unicode en espaces ASCII."""
    return UNICODE_SPACES.sub(' ', text)


def parse_patch(patch_text: str) -> List[Hunk]:
    """Parse un patch et retourne la liste des hunks."""
    patch_text = normalize_unicode_spaces(patch_text)
    lines = patch_text.split('\n')
    hunks: List[Hunk] = []
    
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        
        # Skip empty lines and comments
        if not line or line.startswith('#'):
            i += 1
            continue
        
        # Begin Patch marker (optional)
        if line == BEGIN_PATCH:
            i += 1
            continue
        
        # End Patch marker
        if line == END_PATCH:
            break
        
        # Add File
        if line.startswith(ADD_FILE):
            path = line[len(ADD_FILE):].strip()
            content_lines = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("***"):
                content_lines.append(lines[i])
                i += 1
            hunks.append(AddFileHunk(path=path, contents='\n'.join(content_lines)))
            continue
        
        # Delete File
        if line.startswith(DELETE_FILE):
            path = line[len(DELETE_FILE):].strip()
            hunks.append(DeleteFileHunk(path=path))
            i += 1
            continue
        
        # Update File
        if line.startswith(UPDATE_FILE):
            path = line[len(UPDATE_FILE):].strip()
            hunk = UpdateFileHunk(path=path)
            i += 1
            
            # Parse chunks until End File or next file
            while i < len(lines):
                chunk_line = lines[i].strip()
                
                if chunk_line.startswith("***"):
                    break
                
                if chunk_line.startswith(CHANGE_CONTEXT):
                    # Parse context (function/class name after @@)
                    context = chunk_line[len(CHANGE_CONTEXT):].strip() or None
                    chunk = UpdateChunk(context=context)
                    i += 1
                    
                    # Parse old (-) and new (+) lines
                    while i < len(lines):
                        diff_line = lines[i]
                        
                        if diff_line.strip().startswith("@@") or diff_line.strip().startswith("***"):
                            break
                        
                        if diff_line.startswith("-"):
                            chunk.old_lines.append(diff_line[1:])  # Remove -
                        elif diff_line.startswith("+"):
                            chunk.new_lines.append(diff_line[1:])  # Remove +
                        elif diff_line.strip() == EOF_MARKER:
                            chunk.is_end_of_file = True
                        elif diff_line.startswith(" "):
                            # Context line (unchanged)
                            chunk.old_lines.append(diff_line[1:])
                            chunk.new_lines.append(diff_line[1:])
                        else:
                            # Plain line, add to both
                            chunk.old_lines.append(diff_line.rstrip())
                            chunk.new_lines.append(diff_line.rstrip())
                        
                        i += 1
                    
                    if chunk.old_lines or chunk.new_lines:
                        hunk.chunks.append(chunk)
                else:
                    i += 1
            
            hunks.append(hunk)
            continue
        
        i += 1
    
    return hunks


def find_and_replace(content: str, old_lines: List[str], new_lines: List[str], context: Optional[str] = None) -> Tuple[str, bool]:
    """
    Trouve et remplace des lignes dans le contenu.
    Utilise seek_sequence (fuzzy 4-passes) pour un matching robuste.
    """
    if not old_lines:
        # Simple insertion (pas de old_lines à chercher)
        if context:
            pattern = re.compile(rf'^(.*{re.escape(context)}.*$)', re.MULTILINE)
            match = pattern.search(content)
            if match:
                insert_pos = match.end()
                new_content = content[:insert_pos] + '\n' + '\n'.join(new_lines) + content[insert_pos:]
                return new_content, True
        return content + '\n' + '\n'.join(new_lines), True

    content_lines = content.split('\n')

    # Utiliser seek_sequence avec matching 4-passes
    start_idx = 0
    if context:
        # Si un contexte est fourni, chercher d'abord le contexte pour restreindre la zone
        try:
            start_idx = seek_sequence(content_lines, [context], 0)
        except ValueError:
            # Contexte non trouvé, chercher depuis le début
            start_idx = 0

    try:
        match_idx = seek_sequence(content_lines, old_lines, start_idx)
        new_content_lines = (
            content_lines[:match_idx] +
            new_lines +
            content_lines[match_idx + len(old_lines):]
        )
        return '\n'.join(new_content_lines), True
    except ValueError:
        return content, False


def _backup_file(path: Path, content: str) -> None:
    """Sauvegarde `content` dans `<path.parent>/.backups/<path.name>`.
    Crée le dossier .backups s'il n'existe pas — les backups restent
    hors de la vue principale du projet."""
    backup_dir = path.parent / ".backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / path.name).write_text(content, encoding="utf-8")


def apply_update_hunk(file_path: Path, hunk: UpdateFileHunk) -> Tuple[bool, str]:
    """
    Applique un hunk de mise à jour à un fichier.
    Utilise seek_sequence pour un matching robuste et applique les
    remplacements en ordre inverse pour préserver les indices.
    """
    if not file_path.exists():
        return False, f"Fichier non trouvé: {file_path}"

    try:
        content = file_path.read_text(encoding='utf-8')
        original_content = content
        lines = content.split('\n')

        # Phase 1 : calculer tous les remplacements (indices + contenu)
        replacements: List[Tuple[int, int, List[str]]] = []  # (start_idx, old_len, new_lines)
        search_start = 0
        errors: List[str] = []

        for chunk in hunk.chunks:
            try:
                # Si contexte fourni, se positionner dessus
                if chunk.context:
                    try:
                        ctx_idx = seek_sequence(lines, [chunk.context], search_start)
                        search_start = ctx_idx + 1
                    except ValueError:
                        pass  # Pas trouvé, chercher depuis la position courante

                if not chunk.old_lines:
                    # Insertion pure — après search_start
                    replacements.append((search_start, 0, chunk.new_lines))
                else:
                    match_idx = seek_sequence(
                        lines, chunk.old_lines, search_start, eof=chunk.is_end_of_file
                    )
                    replacements.append((match_idx, len(chunk.old_lines), chunk.new_lines))
                    search_start = match_idx + len(chunk.old_lines)
            except ValueError as e:
                errors.append(
                    f"Chunk non trouvé dans {file_path.name}: "
                    f"{chunk.old_lines[:3]!r}... (contexte={chunk.context!r})"
                )
                logger.warning(f"Chunk non trouvé dans {file_path}: {e}")

        if errors and not replacements:
            return False, "; ".join(errors)

        # Phase 2 : appliquer en ordre INVERSE pour préserver les indices
        for start_idx, old_len, new_lines in reversed(replacements):
            lines[start_idx:start_idx + old_len] = new_lines

        content = '\n'.join(lines)

        if content != original_content:
            _backup_file(file_path, original_content)
            file_path.write_text(content, encoding='utf-8')
            msg = f"Modifié: {file_path.name}"
            if errors:
                msg += f" (partiellement, {len(errors)} chunk(s) ignoré(s))"
            return True, msg
        else:
            return False, f"Aucune modification appliquée à {file_path.name}"

    except Exception as e:
        return False, f"Erreur: {e}"


async def apply_patch(patch_content: str, workspace_root: Path = None) -> PatchResult:
    """
    Applique un patch complet.
    
    Args:
        patch_content: Contenu du patch au format Lumena
        workspace_root: Racine du workspace (pour chemins relatifs)
    
    Returns:
        PatchResult avec le résumé des modifications
    """
    result = PatchResult(success=True)
    
    if workspace_root is None:
        workspace_root = Path.cwd()
    
    try:
        hunks = parse_patch(patch_content)
        
        if not hunks:
            result.errors.append("Aucun hunk trouvé dans le patch")
            result.success = False
            return result
        
        for hunk in hunks:
            if isinstance(hunk, AddFileHunk):
                # Créer un nouveau fichier
                file_path = workspace_root / hunk.path
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text(hunk.contents, encoding='utf-8')
                result.added.append(hunk.path)
                logger.info(f"✅ Créé: {hunk.path}")
            
            elif isinstance(hunk, DeleteFileHunk):
                # Supprimer un fichier
                file_path = workspace_root / hunk.path
                if file_path.exists():
                    # Backup avant suppression
                    backup_dir = workspace_root / ".lumena_backups" / datetime.now().strftime("%Y%m%d_%H%M%S")
                    backup_dir.mkdir(parents=True, exist_ok=True)
                    (backup_dir / file_path.name).write_text(
                        file_path.read_text(encoding='utf-8'), 
                        encoding='utf-8'
                    )
                    file_path.unlink()
                    result.deleted.append(hunk.path)
                    logger.info(f"🗑️ Supprimé: {hunk.path}")
                else:
                    result.errors.append(f"Fichier non trouvé: {hunk.path}")
            
            elif isinstance(hunk, UpdateFileHunk):
                # Modifier un fichier existant
                file_path = workspace_root / hunk.path
                success, message = apply_update_hunk(file_path, hunk)
                
                if success:
                    result.modified.append(hunk.path)
                    logger.info(f"📝 {message}")
                    
                    # Gérer le renommage si move_path
                    if hunk.move_path:
                        new_path = workspace_root / hunk.move_path
                        new_path.parent.mkdir(parents=True, exist_ok=True)
                        file_path.rename(new_path)
                        logger.info(f"📦 Déplacé vers: {hunk.move_path}")
                else:
                    result.errors.append(message)
        
        result.success = len(result.errors) == 0 or len(result.added) + len(result.modified) + len(result.deleted) > 0
    
    except Exception as e:
        result.success = False
        result.errors.append(f"Erreur globale: {e}")
        logger.error(f"Erreur apply_patch: {e}")
    
    return result


# === EDIT FILE TOOL ===

async def edit_file(
    file_path: str,
    old_content: str,
    new_content: str,
    workspace_root: Path = None
) -> str:
    """
    Édite un fichier en remplaçant old_content par new_content.
    
    Plus simple que apply_patch, pour des remplacements uniques.
    
    Args:
        file_path: Chemin du fichier
        old_content: Contenu à remplacer
        new_content: Nouveau contenu
        workspace_root: Racine du workspace
    
    Returns:
        Message de succès/erreur
    """
    if workspace_root is None:
        workspace_root = Path.cwd()
    
    path = workspace_root / file_path
    
    # Fallback: si le chemin relatif ne marche pas, essayer le chemin absolu
    if not path.exists():
        abs_candidate = Path(file_path)
        if abs_candidate.is_absolute() and abs_candidate.exists():
            path = abs_candidate
        else:
            # Chercher récursivement dans workspace/
            from ..utils.paths import WORKSPACE_DIR as _WS_DIR
            ws_dir = _WS_DIR
            if ws_dir.exists():
                name = Path(file_path).name
                matches = sorted(ws_dir.rglob(name), key=lambda p: p.stat().st_mtime, reverse=True)
                # Filtrer par suffixe de chemin si possible
                tail_parts = Path(file_path).parts
                for m in matches:
                    if m.parts[-len(tail_parts):] == tail_parts:
                        path = m
                        break
                else:
                    if matches:
                        path = matches[0]
            if not path.exists():
                return f"❌ Fichier non trouvé: {file_path}"
    
    try:
        content = path.read_text(encoding='utf-8')
        
        # Garde idempotente : si new_content est déjà présent ET old_content
        # en est un sous-ensemble (cas "append-by-replace"), l'édition a déjà
        # été appliquée — on ne la rejoue pas pour éviter les doublons.
        if new_content in content and old_content in new_content:
            return f"✅ Déjà appliqué (idempotent): {path.name}"

        if old_content not in content:
            # Niveau 1: normalisation unicode (espaces insécables, etc.)
            norm_content = UNICODE_SPACES.sub(' ', content)
            norm_old = UNICODE_SPACES.sub(' ', old_content)
            if norm_old in norm_content:
                _backup_file(path, content)
                new_full = norm_content.replace(norm_old, new_content, 1)
                path.write_text(new_full, encoding='utf-8')
                return f"✅ Modifié: {path.name} (normalisation unicode)"

            # Niveau 1.5: normalisation ponctuation (smart quotes, dashes)
            punct_content = _normalize_punctuation(content)
            punct_old = _normalize_punctuation(old_content)
            if punct_old in punct_content:
                _backup_file(path, content)
                new_full = punct_content.replace(punct_old, new_content, 1)
                path.write_text(new_full, encoding='utf-8')
                return f"✅ Modifié: {path.name} (normalisation ponctuation)"

            # Niveau 2: normalisation whitespace (strip lignes + espaces multiples)
            import re as _re
            def _ws_normalize(text: str) -> str:
                lines = text.split('\n')
                return '\n'.join(l.rstrip() for l in lines)
            ws_content = _ws_normalize(content)
            ws_old = _ws_normalize(old_content.strip())
            if ws_old in ws_content:
                _backup_file(path, content)
                new_full = ws_content.replace(ws_old, _ws_normalize(new_content.strip()), 1)
                path.write_text(new_full, encoding='utf-8')
                return f"✅ Modifié: {path.name} (normalisation whitespace)"

            # Niveau 3: chercher ligne par ligne (ancien fallback)
            normalized_old = old_content.strip()
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if normalized_old in line:
                    lines[i] = line.replace(normalized_old, new_content.strip())
                    new_full_content = '\n'.join(lines)
                    _backup_file(path, content)
                    path.write_text(new_full_content, encoding='utf-8')
                    return f"✅ Modifié: {path.name} (ligne {i+1})"
            
            # Niveau 4: matching multi-ligne flou (ignorer indentation)
            old_lines = [l.strip() for l in old_content.strip().splitlines() if l.strip()]
            if len(old_lines) >= 2:
                content_lines = content.splitlines()
                for start_idx in range(len(content_lines)):
                    if old_lines[0] in content_lines[start_idx].strip():
                        # Vérifier les lignes suivantes
                        match = True
                        end_idx = start_idx
                        old_i = 0
                        for ci in range(start_idx, min(start_idx + len(old_lines) * 2, len(content_lines))):
                            stripped = content_lines[ci].strip()
                            if not stripped:
                                continue
                            if old_i < len(old_lines) and old_lines[old_i] in stripped:
                                old_i += 1
                                end_idx = ci
                            elif old_i > 0:
                                match = False
                                break
                        if match and old_i == len(old_lines):
                            _backup_file(path, content)
                            new_lines = content_lines[:start_idx] + new_content.splitlines() + content_lines[end_idx + 1:]
                            path.write_text('\n'.join(new_lines), encoding='utf-8')
                            return f"✅ Modifié: {path.name} (lignes {start_idx+1}-{end_idx+1}, matching flou)"

            return (
                f"❌ Contenu non trouvé dans {path.name}\n"
                f"💡 Conseil: utilise read_file pour relire le contenu exact du fichier.\n"
                f"🔎 Cherché ({len(old_content.splitlines())} lignes): {old_content[:200]!r}..."
            )
        
        # Backup
        _backup_file(path, content)

        # Remplacer
        new_full_content = content.replace(old_content, new_content, 1)
        path.write_text(new_full_content, encoding='utf-8')
        
        return f"✅ Modifié: {path.name}"
    
    except Exception as e:
        return f"❌ Erreur: {e}"


def multi_edit_file(edits: List[dict], base_path: Optional[Path] = None) -> str:
    """
    PHASE 1: MultiEdit - Editions multiples en un seul appel.
    
    Permet de faire plusieurs modifications sur un ou plusieurs fichiers
    en un seul appel LLM → économie de ~50% de tokens.
    
    Args:
        edits: Liste de dicts avec {file, old_content, new_content}
        base_path: Chemin de base pour les fichiers relatifs
        
    Returns:
        Résumé des modifications effectuées
        
    Exemple:
        multi_edit_file([
            {"file": "main.py", "old": "x = 1", "new": "x = 2"},
            {"file": "utils.py", "old": "def foo():", "new": "def foo(x):"},
        ])
    """
    if not edits:
        return "Erreur: Aucune edition fournie"
    
    results = []
    errors = []
    modified_files = set()
    
    for i, edit in enumerate(edits):
        file_path = edit.get("file") or edit.get("path") or edit.get("file_path")
        old_content = edit.get("old") or edit.get("old_content")
        new_content = edit.get("new") or edit.get("new_content")
        
        if not all([file_path, old_content is not None, new_content is not None]):
            errors.append(f"Edit #{i+1}: paramètres manquants (file, old, new)")
            continue
        
        try:
            # Résoudre le chemin
            if base_path:
                path = base_path / file_path
            else:
                path = Path(file_path)
            
            if not path.exists():
                # Fallback: chemin absolu
                abs_candidate = Path(file_path)
                if abs_candidate.is_absolute() and abs_candidate.exists():
                    path = abs_candidate
                else:
                    # Chercher dans workspace/
                    from ..utils.paths import WORKSPACE_DIR as _WS_DIR
                    ws_dir = _WS_DIR
                    found = False
                    if ws_dir.exists():
                        name = Path(file_path).name
                        matches = sorted(ws_dir.rglob(name), key=lambda p: p.stat().st_mtime, reverse=True)
                        tail_parts = Path(file_path).parts
                        for m in matches:
                            if m.parts[-len(tail_parts):] == tail_parts:
                                path = m
                                found = True
                                break
                        if not found and matches:
                            path = matches[0]
                            found = True
                    if not found:
                        errors.append(f"{path.name}: fichier non trouvé")
                        continue
            
            # Lire le contenu
            content = path.read_text(encoding='utf-8')
            
            # Garde idempotente (même logique que edit_file)
            if new_content in content and old_content in new_content:
                results.append(f"OK (idempotent): {path.name}")
                continue

            # Vérifier que old_content existe
            if old_content not in content:
                # Niveau 1: normalisation unicode
                normalized_content = UNICODE_SPACES.sub(' ', content)
                normalized_old = UNICODE_SPACES.sub(' ', old_content)
                
                if normalized_old in normalized_content:
                    content = normalized_content
                    old_content = normalized_old
                else:
                    # Niveau 2: normalisation whitespace (strip trailing)
                    def _ws_norm(t: str) -> str:
                        return '\n'.join(l.rstrip() for l in t.split('\n'))
                    ws_content = _ws_norm(content)
                    ws_old = _ws_norm(old_content.strip())
                    if ws_old in ws_content:
                        content = ws_content
                        old_content = ws_old
                    else:
                        errors.append(f"{path.name}: contenu à remplacer non trouvé")
                        continue
            
            # Appliquer la modification
            new_full_content = content.replace(old_content, new_content, 1)
            
            # Créer backup seulement si premier edit sur ce fichier
            if path not in modified_files:
                backup = path.with_suffix(path.suffix + f'.bak_{datetime.now().strftime("%H%M%S")}')
                try:
                    backup.write_text(content, encoding='utf-8')
                except OSError:
                    pass  # Backup optionnel
            
            # Écrire
            path.write_text(new_full_content, encoding='utf-8')
            modified_files.add(path)
            results.append(f"OK: {path.name}")
            
        except Exception as e:
            errors.append(f"{file_path}: {e}")
    
    # Résumé
    summary_parts = []
    if results:
        summary_parts.append(f"**{len(results)} éditions réussies:**")
        summary_parts.extend(results)
    
    if errors:
        summary_parts.append(f"\n**{len(errors)} erreurs:**")
        for err in errors:
            summary_parts.append(f"ERREUR: {err}")
    
    if not summary_parts:
        return "Erreur: Aucune modification effectuee"
    
    return "\n".join(summary_parts)


# ---------------------------------------------------------------------------
# edit_by_lines — édition déterministe par numéros de ligne (P0)
# ---------------------------------------------------------------------------

def edit_by_lines(file_path: str, start_line: int, end_line: int, new_content: str) -> str:
    """Remplace les lignes [start_line, end_line] (1-indexed) par new_content.

    Retourne un message succès/erreur. Crée un backup avant modification.
    """
    p = Path(file_path)
    if not p.exists():
        return f"❌ Fichier non trouvé : {file_path}"

    try:
        original = p.read_text(encoding="utf-8")
    except Exception as exc:
        return f"❌ Impossible de lire {file_path} : {exc}"

    lines = original.split("\n")
    # Gérer le trailing newline : splitlines garde une entrée vide à la fin
    total = len(lines)

    if start_line < 1 or start_line > total:
        return f"❌ start_line={start_line} hors limites (fichier a {total} lignes)"
    if end_line < start_line:
        return f"❌ end_line={end_line} < start_line={start_line}"
    if end_line > total:
        return f"❌ end_line={end_line} hors limites (fichier a {total} lignes)"

    # Backup
    _backup_file(p, original)

    # Remplacement : indices 0-based
    before = lines[: start_line - 1]
    after = lines[end_line:]

    # new_content peut être multi-lignes, on le split
    new_lines = new_content.split("\n")
    # Supprimer le trailing empty si new_content se termine par \n
    if new_lines and new_lines[-1] == "":
        new_lines = new_lines[:-1]

    result_lines = before + new_lines + after
    p.write_text("\n".join(result_lines), encoding="utf-8")

    n_replaced = end_line - start_line + 1
    return f"✅ {file_path} : lignes {start_line}-{end_line} remplacées ({n_replaced} → {len(new_lines)} lignes)"


# === Tests ===
if __name__ == "__main__":
    # Test du parser
    test_patch = """
*** Begin Patch
*** Add File: test/hello.py
print("Hello World!")
*** End File

*** Update File: src/main.py
@@
-old_line = 1
+new_line = 2
*** End File
*** End Patch
"""
    
    hunks = parse_patch(test_patch)
    print(f"Hunks trouvés: {len(hunks)}")
    for h in hunks:
        print(f"  - {h.kind}: {h.path}")
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
