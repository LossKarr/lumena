"""Shared file path + write guardrails used by chat and agent pipelines."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Tuple
import ast
import json

from loguru import logger


# ---------------------------------------------------------------------------
# Security: path boundary and blacklist checks (P0.2)
# ---------------------------------------------------------------------------

class PathSecurityError(Exception):
    """Raised when a file operation targets a forbidden path."""


@dataclass
class OutsideAccessGrant:
    """Per-turn bounded grant for accessing paths outside workspace.

    Only read/list/search is grantable via this mechanism. Write and delete
    outside workspace remain forbidden regardless of any grant.

    Usage: built by _detect_outside_access_grant(query) in agent_service.py
    and attached to HandlerContext for the duration of one turn.
    """

    # Roots/paths the agent may read from outside workspace this turn.
    allowed_roots: List[Path] = field(default_factory=list)
    allow_read: bool = False
    # Write and delete are never granted here — kept explicit as False.
    allow_write: bool = False
    allow_delete: bool = False

    def permits_read(self, resolved: Path) -> bool:
        """Return True if *resolved* falls under a granted root."""
        if not self.allow_read or not self.allowed_roots:
            return False
        try:
            rp = resolved.resolve()
        except (OSError, ValueError):
            rp = resolved
        for root in self.allowed_roots:
            try:
                root_r = root.resolve()
            except (OSError, ValueError):
                root_r = root
            if _is_within(rp, root_r) or rp == root_r:
                return True
        return False

    @classmethod
    def none(cls) -> "OutsideAccessGrant":
        """No outside access — default state."""
        return cls()

    @classmethod
    def for_paths(cls, *paths: Path) -> "OutsideAccessGrant":
        """Grant read access to specific paths or directory roots."""
        return cls(allowed_roots=list(paths), allow_read=True)


def _resolve_safe(path: Path) -> Path:
    """Resolve a path without following symlinks outside the project."""
    try:
        return path.resolve()
    except (OSError, ValueError):
        return path.absolute()


def _is_within(resolved: Path, root: Path) -> bool:
    """Check if resolved path is within root (both must be resolved)."""
    try:
        resolved.relative_to(root)
        return True
    except ValueError:
        return False


def check_path_boundary(resolved: Path, lumena_root: Path, workspace_root: Path) -> None:
    """Ensure a resolved path is within lumena_root or workspace_root.

    Raises PathSecurityError if the path is outside allowed boundaries.
    """
    lr = lumena_root.resolve()
    wr = workspace_root.resolve()
    rp = resolved.resolve()
    if not (_is_within(rp, lr) or _is_within(rp, wr)):
        raise PathSecurityError(
            f"Accès refusé: chemin hors des limites autorisées ({rp})"
        )


# Paths that must NOT be readable via generic read_file / grep_search
_READ_BLACKLIST_EXACT = {".env", ".env.local", ".env.production"}
_READ_BLACKLIST_PREFIXES: Tuple[str, ...] = (
    "data/mail/",
    "data\\mail\\",
    "data/browser_profiles/",
    "data\\browser_profiles\\",
)


def check_read_blacklist(resolved: Path, lumena_root: Path) -> None:
    """Block generic read access to secrets and private data.

    These files are only accessible via dedicated handlers with auth.
    Raises PathSecurityError if the path is blacklisted for reading.
    """
    lr = lumena_root.resolve()
    rp = resolved.resolve()
    if not _is_within(rp, lr):
        return  # outside lumena_root — boundary check handles this
    try:
        rel = rp.relative_to(lr)
    except ValueError:
        return
    rel_posix = rel.as_posix()
    # Exact filename match at lumena root
    if rel_posix in _READ_BLACKLIST_EXACT or rel.name in _READ_BLACKLIST_EXACT:
        raise PathSecurityError(
            f"Lecture refusée: {rel_posix} contient des secrets. "
            "Utilise /api/config/reveal avec authentification."
        )
    # Prefix match for sensitive directories
    rel_str = str(rel).replace("\\", "/") + "/"
    for prefix in ("data/mail/", "data/browser_profiles/"):
        if rel_str.startswith(prefix) or rel_posix.startswith(prefix):
            raise PathSecurityError(
                f"Lecture refusée: {rel_posix} est une zone privée. "
                "Utilise le handler dédié avec authentification."
            )


# Paths/prefixes that must NOT be writable via generic write/patch handlers
_WRITE_BLACKLIST_EXACT = {".env", ".env.local", ".env.production"}
_WRITE_BLACKLIST_PREFIXES: Tuple[str, ...] = (
    "data/",
    "models/",
    "backups/",
)
_WRITE_BLACKLIST_SUFFIXES: Tuple[str, ...] = (
    ".backup",
)


def check_write_blacklist(resolved: Path, lumena_root: Path) -> None:
    """Block writes to config, memory, and model files.

    Raises PathSecurityError if the path is blacklisted for writing.
    """
    lr = lumena_root.resolve()
    rp = resolved.resolve()
    if not _is_within(rp, lr):
        return  # outside lumena_root — let boundary check handle
    try:
        rel = rp.relative_to(lr)
    except ValueError:
        return
    rel_posix = rel.as_posix()
    # Exact filename
    if rel_posix in _WRITE_BLACKLIST_EXACT or rel.name in _WRITE_BLACKLIST_EXACT:
        raise PathSecurityError(
            f"Écriture refusée: {rel_posix} est un fichier de configuration protégé."
        )
    # Prefix match
    rel_str = str(rel).replace("\\", "/")
    if not rel_str.endswith("/"):
        rel_str_dir = rel_str + "/"  # won't actually be used — just for parent check
    for prefix in _WRITE_BLACKLIST_PREFIXES:
        if rel_str.startswith(prefix) or rel_posix.startswith(prefix):
            raise PathSecurityError(
                f"Écriture refusée: {rel_posix} est dans une zone protégée ({prefix.rstrip('/')}/). "
                "Les données de Lumena ne peuvent pas être modifiées par des outils génériques."
            )
    # Suffix match
    for suffix in _WRITE_BLACKLIST_SUFFIXES:
        if rel_posix.endswith(suffix):
            raise PathSecurityError(
                f"Écriture refusée: {rel_posix} (fichier {suffix})."
            )


def check_delete_allowed(resolved: Path, lumena_root: Path, workspace_root: Path) -> None:
    """Only allow deletions inside workspace_root by default.

    Raises PathSecurityError if the path is not in workspace_root.
    """
    wr = workspace_root.resolve()
    rp = resolved.resolve()
    if not _is_within(rp, wr):
        raise PathSecurityError(
            f"Suppression refusée: seuls les fichiers dans workspace/ peuvent être supprimés. "
            f"({rp} n'est pas dans {wr})"
        )


def strip_mission_workspace_prefix(path_str: str, subdir: str) -> str:
    """LOT 2.8 (run BudgetBuddy) — ramène un chemin à sa forme RELATIVE au dossier
    de mission. Les modèles recopient le chemin complet (`missions/<id>/app.py`,
    voire `workspace/missions/<id>/app.py`) alors que le résolveur préfixe DÉJÀ le
    dossier de mission → duplication `missions/<id>/missions/<id>/…`. On strippe
    défensivement (en boucle : tue aussi un chemin déjà dupliqué) les préfixes
    `workspace/` et `<subdir>/`. Pur ; sans subdir → chemin inchangé (hors mission)."""
    s = (path_str or "").replace("\\", "/").lstrip("/")
    sub = (subdir or "").replace("\\", "/").strip("/")
    if not sub:
        return path_str
    sub_low = sub.lower() + "/"
    changed = True
    while changed:
        changed = False
        if s.lower().startswith("workspace/"):
            s = s[len("workspace/"):]
            changed = True
        if s.lower().startswith(sub_low):
            s = s[len(sub_low):]
            changed = True
        # A1 — alias court `mission/` (singulier, non ambigu) : un worker peut
        # désigner son dossier sans jamais recopier l'identifiant long.
        if s.lower().startswith("mission/"):
            s = s[len("mission/"):]
            changed = True
    return s


@dataclass
class FileWriteResult:
    """Result of a strict write operation."""

    success: bool
    file_path: Path
    message: str
    workspace_redirected: bool = False
    workspace_relative: str = ""
    validation_errors: List[str] = field(default_factory=list)


class WorkspaceFileGuardrails:
    """Workspace-aware path resolver + strict post-write validation."""

    _current_project: Optional[str] = None
    # Projet ÉPINGLÉ pour la durée d'une mission/d'un lot : quand il est posé,
    # TOUS les fichiers (web ou non) vont dans CE seul `projet-…` au lieu de
    # dériver un dossier par nom de fichier (`projet-notes`, `projet-readme`…).
    # Évite le scatter d'un même livrable + la boucle de réécriture de l'agent.
    # Garde la convention date/projet du reste du système.
    _pinned_project: Optional[str] = None
    _project_extensions = {
        ".html",
        ".htm",
        ".css",
        ".js",
        ".jsx",
        ".ts",
        ".tsx",
        ".scss",
        ".sass",
        ".less",
    }

    def __init__(self, lumena_root: Path):
        self.lumena_root = Path(lumena_root)

    def _looks_like_project_root(self) -> bool:
        root = self.lumena_root.resolve()
        project_markers = ("src", "web", "data", "skills", "requirements.txt", "lumena_ultime.py")
        return any((root / marker).exists() for marker in project_markers)

    def _workspace_root(self) -> Path:
        from ..utils.paths import WORKSPACE_DIR
        root = self.lumena_root.resolve()
        if self._looks_like_project_root():
            return WORKSPACE_DIR
        return root

    def sanitize_workspace_relative_path(self, original: Path) -> Path:
        """Normalize a user path and remove traversal segments."""
        if original.is_absolute():
            try:
                relative = original.resolve().relative_to(self.lumena_root.resolve())
            except Exception:
                relative = Path(original.name)
        else:
            relative = original

        safe_parts = [part for part in relative.parts if part not in ("", ".", "..")]
        if not safe_parts:
            return Path(original.name or "output.txt")
        return Path(*safe_parts)

    def find_workspace_match(self, requested_path: Path, want_dir: bool = False) -> Optional[Path]:
        """Find the best matching file/dir in workspace.

        Scoring (higher is better):
          - Pénalise les chemins "louches" : .backups/, .bak, _archive, old/, tmp/,
            .history/, __pycache__/, node_modules/, et les sous-dossiers inclus dans
            des dossiers datés (YYYY-MM-DD/) qui dupliquent un projet racine.
          - Bonus forte pour les projets directement à la racine de workspace.
          - En cas d'égalité → fichier le plus récent (mtime).
        """
        import re as _re_fw
        workspace_root = self._workspace_root()
        if not workspace_root.exists():
            return None

        requested_rel = self.sanitize_workspace_relative_path(requested_path)
        if not requested_rel.name:
            return None

        matches: List[Path] = []
        for candidate in workspace_root.rglob(requested_rel.name):
            if want_dir and not candidate.is_dir():
                continue
            if not want_dir and not candidate.is_file():
                continue

            if len(requested_rel.parts) > 1:
                tail = candidate.parts[-len(requested_rel.parts) :]
                if tuple(tail) != requested_rel.parts:
                    continue

            matches.append(candidate)

        if not matches:
            return None

        _DATE_RE = _re_fw.compile(r"^\d{4}-\d{2}-\d{2}$")
        _BAD_SEG_RE = _re_fw.compile(
            r"^(\.backups?|\.bak|_?archive|_?old|_?tmp|_?temp|\.history|"
            r"__pycache__|node_modules|dist|build)$",
            _re_fw.IGNORECASE,
        )
        _BAD_SUFFIXES = (".bak", ".backup", ".old", ".orig", ".tmp")

        # Projets directement à la racine de workspace (référence anti-doublon)
        try:
            root_projects = {
                d.name.lower()
                for d in workspace_root.iterdir()
                if d.is_dir() and not _DATE_RE.match(d.name) and not d.name.startswith(".")
            }
        except OSError:
            root_projects = set()

        def _score(item: Path) -> tuple[int, float]:
            try:
                rel_parts = item.relative_to(workspace_root).parts
            except ValueError:
                rel_parts = item.parts
            score = 0
            # Pénalité très forte : segment de chemin de type backup/archive/cache
            for seg in rel_parts:
                if _BAD_SEG_RE.match(seg):
                    score -= 1000
                if any(seg.lower().endswith(suf) for suf in _BAD_SUFFIXES):
                    score -= 500
            # Pénalité : filename avec suffixe de backup (ex: documentation.html.bak_200120)
            name_lower = item.name.lower()
            for suf in _BAD_SUFFIXES:
                if suf in name_lower and not name_lower.endswith(requested_rel.suffix.lower() or ""):
                    score -= 500
            if _re_fw.search(r"\.bak[_.-]?\d*$", name_lower):
                score -= 500
            # Pénalité doublon : si le fichier est dans un dossier daté (YYYY-MM-DD)
            # ET son parent-projet immédiat existe aussi à la racine workspace
            # → c'est probablement une copie/miroir automatique.
            for i, seg in enumerate(rel_parts[:-1]):
                if _DATE_RE.match(seg) and i + 1 < len(rel_parts) - 1:
                    # seg suivant = dossier projet dans le dossier daté
                    date_child = rel_parts[i + 1]
                    # Si un des segments suivants correspond à un projet racine → doublon
                    for deeper in rel_parts[i + 2 : -1]:
                        if deeper.lower() in root_projects:
                            score -= 2000  # très forte pénalité : c'est un miroir
                            break
                    # Pénalité douce pour tout fichier rangé sous une date
                    score -= 100
                    break
            # Bonus : chemin court (projet racine)
            score -= len(rel_parts)
            # Tiebreak : le plus récent gagne
            try:
                mtime = item.stat().st_mtime
            except OSError:
                mtime = 0.0
            return (score, mtime)

        matches.sort(key=_score, reverse=True)
        return matches[0]

    def resolve_user_path(
        self,
        path: str,
        want_dir: bool = False,
        outside_grant: Optional[OutsideAccessGrant] = None,
        mission_workspace_subdir: Optional[str] = None,
    ) -> Path:
        """Resolve a read/list/edit path with workspace fallback.

        After resolution, enforces boundary check: result must be within
        lumena_root or workspace_root, OR covered by an explicit
        OutsideAccessGrant (read-only, bounded to declared roots).

        Raises PathSecurityError if the path is outside all allowed bounds.
        """
        candidate = Path(path)

        # FIX: Eviter le double workspace/workspace/
        # Si le path commence par 'workspace/', on strip toujours le prefix :
        # que lumena_root soit le projet (lumena/) ou le workspace lui-meme (lumena/workspace/),
        # _workspace_root() ajoute deja /workspace donc le prefix est redondant.
        candidate_str = candidate.as_posix()
        if (
            not candidate.is_absolute()
            and candidate_str.startswith("workspace/")
        ):
            stripped = candidate_str[len("workspace/"):]
            if stripped:  # Ne pas stripper si c'est juste "workspace/"
                ws_root = self._workspace_root()
                ws_candidate = ws_root / stripped
                if ws_candidate.exists():
                    return ws_candidate
                # Meme si pas encore existant (creation), retourner le chemin sans double workspace
                candidate = Path(stripped)

        if candidate.is_absolute():
            resolved = candidate.resolve() if candidate.exists() else candidate
        else:
            root_candidate = self.lumena_root / candidate
            if root_candidate.exists():
                resolved = root_candidate
            elif candidate.exists():
                resolved = candidate.resolve()
            elif mission_workspace_subdir:
                # LOT 2.1 — le fichier du dossier de la mission PRIME sur un homonyme
                # (find_workspace_match). Existe → lecture ; sinon → création dans le
                # dossier de la mission (cohérence read-après-write). Param explicite.
                # LOT 2.8 — strip défensif anti-duplication (chemin complet recopié).
                _cand_28 = Path(strip_mission_workspace_prefix(
                    str(candidate), mission_workspace_subdir))
                resolved = (
                    self._workspace_root() / mission_workspace_subdir
                    / self.sanitize_workspace_relative_path(_cand_28)
                )
            else:
                workspace_match = self.find_workspace_match(candidate, want_dir=want_dir)
                if workspace_match:
                    resolved = workspace_match
                elif candidate.is_absolute():
                    resolved = candidate
                else:
                    # Anti-pollution: ne JAMAIS créer un fichier de projet web
                    # (.html/.css/.js/.tsx...) directement à la racine de Lumena.
                    # Rediriger vers workspace/<projet>/ via get_workspace_path().
                    ext = candidate.suffix.lower()
                    if ext in self._project_extensions:
                        try:
                            resolved = self.get_workspace_path(str(candidate))
                        except Exception:
                            resolved = self.lumena_root / candidate
                    else:
                        resolved = self.lumena_root / candidate

        # P0.2: Boundary check — result must be inside lumena_root or workspace_root.
        rp = _resolve_safe(resolved)
        lr = self.lumena_root.resolve()
        wr = self._workspace_root().resolve()
        within_bounds = _is_within(rp, lr) or _is_within(rp, wr)
        if within_bounds:
            return resolved

        # Outside normal bounds — only allowed if the grant explicitly covers it.
        if outside_grant is not None and outside_grant.permits_read(rp):
            logger.debug(f"[guardrails] Accès hors workspace accordé par grant: {rp}")
            return resolved

        raise PathSecurityError(
            f"Accès refusé: chemin hors des limites autorisées ({rp})"
        )

    def should_use_workspace(self, path: str) -> bool:
        """Return True when the path should be redirected into workspace."""
        path_lower = path.lower()
        system_patterns = [
            "src/",
            "src\\",
            "skills/",
            "skills\\",
            "data/",
            "data\\",
            ".agent/",
            ".agent\\",
            "qui_suis_je",
            "memory.md",
            "config",
            "settings",
            ".env",
            ".git",
            "workspace/",
            "workspace\\",
        ]
        if any(pattern in path_lower for pattern in system_patterns):
            return False

        candidate = Path(path)
        if candidate.is_absolute():
            try:
                candidate.relative_to(self.lumena_root)
            except ValueError:
                return False

        return True

    @staticmethod
    def _normalize_project_name(value: str) -> str:
        clean = (value or "").strip().lower().replace(" ", "-").replace("_", "-")
        clean = clean.strip("-")
        if not clean:
            clean = "default"
        return clean if clean.startswith("projet-") else f"projet-{clean}"

    @classmethod
    def pin_project(cls, name: Optional[str]) -> None:
        """Épingle un projet unique pour le lot courant (None = relâche)."""
        cls._pinned_project = cls._normalize_project_name(name) if name else None

    @classmethod
    @contextmanager
    def pinned_project(cls, name: Optional[str]):
        """Contexte : tous les fichiers écrits dedans vont au même projet épinglé."""
        prev = cls._pinned_project
        cls.pin_project(name)
        try:
            yield
        finally:
            cls._pinned_project = prev

    def get_workspace_path(
        self,
        original_path: str,
        project_name: Optional[str] = None,
        mission_workspace_subdir: Optional[str] = None,
    ) -> Path:
        """Compute workspace target path while preserving relative subfolders."""
        original = Path(original_path)

        # LOT 2.1 — scope mission : dossier ISOLÉ, DÉTERMINISTE (pas de date, pas de
        # dérivation par nom, PAS de _pinned_project de classe → concurrence-safe).
        # Param explicite fourni par le handler ; None → résolution actuelle.
        if mission_workspace_subdir:
            # LOT 2.8 — strip défensif : le modèle passe souvent le chemin complet
            # (missions/<id>/app.py) → sans strip, duplication missions/<id>/missions/<id>.
            original = Path(strip_mission_workspace_prefix(
                str(original), mission_workspace_subdir))
            workspace_dir = self._workspace_root() / mission_workspace_subdir
            workspace_dir.mkdir(parents=True, exist_ok=True)
            return workspace_dir / self.sanitize_workspace_relative_path(original)

        today = datetime.now().strftime("%Y-%m-%d")
        ext = original.suffix.lower()

        if WorkspaceFileGuardrails._pinned_project and not project_name:
            # Mission/lot épinglé : un seul dossier pour TOUT le livrable.
            project_folder = WorkspaceFileGuardrails._pinned_project
        elif ext in self._project_extensions:
            if project_name:
                project_folder = self._normalize_project_name(project_name)
                WorkspaceFileGuardrails._current_project = project_folder
            elif WorkspaceFileGuardrails._current_project:
                project_folder = WorkspaceFileGuardrails._current_project
            elif original.stem.lower() == "index":
                project_folder = "projet-web-site"
                WorkspaceFileGuardrails._current_project = project_folder
            else:
                stem = original.stem.lower() or "site"
                project_folder = WorkspaceFileGuardrails._current_project or f"projet-web-{stem}"
                WorkspaceFileGuardrails._current_project = project_folder
        else:
            WorkspaceFileGuardrails._current_project = None
            if project_name:
                project_folder = self._normalize_project_name(project_name)
            else:
                stem = original.stem.lower().replace("_", "-") or "fichier"
                project_folder = f"projet-{stem}"

        workspace_dir = self._workspace_root() / today / project_folder
        workspace_dir.mkdir(parents=True, exist_ok=True)

        relative_original = self.sanitize_workspace_relative_path(original)
        return workspace_dir / relative_original

    def resolve_write_target(
        self,
        path: str,
        project_name: Optional[str] = None,
        mission_workspace_subdir: Optional[str] = None,
    ) -> Tuple[Path, bool, str]:
        """Resolve a write target path and workspace metadata."""
        original = Path(path)
        if self.should_use_workspace(path) and not original.is_absolute():
            target = self.get_workspace_path(
                path, project_name=project_name,
                mission_workspace_subdir=mission_workspace_subdir,
            )
            rel = str(target.relative_to(self.lumena_root)).replace("\\", "/")
            return target, True, rel

        if original.is_absolute():
            target = original
            # Écriture vers un chemin absolu : vérification boundary stricte.
            # Aucun grant ne permet l'écriture hors workspace.
            rp = _resolve_safe(target)
            lr = self.lumena_root.resolve()
            wr = self._workspace_root().resolve()
            if not (_is_within(rp, lr) or _is_within(rp, wr)):
                raise PathSecurityError(
                    f"Écriture refusée: {rp} est hors des limites autorisées. "
                    "Les écritures hors workspace ne sont jamais permises."
                )
        else:
            target = self.lumena_root / original
        return target, False, ""

    def validate_write_result(
        self,
        file_path: Path,
        expected_content: str,
        require_non_empty: bool = True,
    ) -> List[str]:
        """Validate write/readback + lightweight syntax checks."""
        errors: List[str] = []
        if require_non_empty and not expected_content:
            errors.append("Contenu vide interdit en mode strict.")

        if not file_path.exists():
            errors.append("Fichier absent apres ecriture.")
            return errors

        readback = file_path.read_text(encoding="utf-8", errors="replace")
        if require_non_empty and not readback:
            errors.append("Fichier vide apres ecriture.")
        if readback != expected_content:
            errors.append("Readback different du contenu ecrit.")

        errors.extend(self._syntax_errors(file_path, readback))
        return errors

    def validate_existing_file(
        self,
        file_path: Path,
        require_non_empty: bool = True,
    ) -> List[str]:
        """Validate an existing file after in-place edit."""
        if not file_path.exists():
            return ["Fichier absent apres operation."]
        content = file_path.read_text(encoding="utf-8", errors="replace")
        errors: List[str] = []
        if require_non_empty and not content:
            errors.append("Fichier vide apres operation.")
        errors.extend(self._syntax_errors(file_path, content))
        return errors

    def write_file_strict(
        self,
        path: str,
        content: str,
        project_name: Optional[str] = None,
        require_non_empty: bool = True,
        mission_workspace_subdir: Optional[str] = None,
    ) -> FileWriteResult:
        """Write and validate immediately."""
        target, redirected, workspace_relative = self.resolve_write_target(
            path, project_name=project_name,
            mission_workspace_subdir=mission_workspace_subdir,
        )
        # LOT Z25 — un dossier qui NAIT ici doit se voir. Le message disait deja
        # le chemin complet, mais rien ne signalait qu'il venait d'etre INVENTE :
        # un chemin plausible + aucun signal de nouveaute = le modele croit avoir
        # ecrit dans le livrable. Run « jeu 3D » : `jeu-3d-monde-ouvert/README.md`
        # relatif a resolu sous missions/<id>/, un arbre neuf qui PORTE LE NOM du
        # livrable — le README n'a jamais rejoint `workspace/jeu-3d-monde-ouvert/`.
        _parent_existed = target.parent.exists()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

        errors = self.validate_write_result(
            file_path=target,
            expected_content=content,
            require_non_empty=require_non_empty,
        )
        if errors:
            return FileWriteResult(
                success=False,
                file_path=target,
                message="; ".join(errors),
                workspace_redirected=redirected,
                workspace_relative=workspace_relative,
                validation_errors=errors,
            )

        msg = f"✅ Fichier ecrit: {target.name} ({len(content)} caracteres)\n📍 Chemin complet: {target}"
        if redirected:
            msg += f"\n📁 Range dans le workspace: {workspace_relative}"
        msg += self._new_directory_notice(target, _parent_existed)
        return FileWriteResult(
            success=True,
            file_path=target,
            message=msg,
            workspace_redirected=redirected,
            workspace_relative=workspace_relative,
        )

    def _new_directory_notice(self, target: Path, parent_existed: bool) -> str:
        """LOT Z25 — dire qu'un dossier vient de naitre, et s'il a un homonyme.

        Deux notes, factuelles, jamais bloquantes :
          1. le dossier parent n'existait pas -> il a ete CREE a l'instant ;
          2. un dossier du MEME NOM existe deja a la racine du workspace, la ou
             vivent les livrables -> tres probablement l'endroit voulu.

        La note 2 est celle qui aurait sauve le README du run « jeu 3D » :
        `workspace/jeu-3d-monde-ouvert/` existait deja quand l'arbre fantome
        `missions/<id>/jeu-3d-monde-ouvert/` est ne. Ne leve jamais.
        """
        if parent_existed:
            return ""
        try:
            parent = Path(target).parent
            note = f"\n🆕 Dossier CREE (il n'existait pas) : {parent}"
        except Exception:
            return ""
        # La recherche d'homonyme touche le disque : elle peut echouer. Elle a
        # son propre garde — sinon un echec ICI jetterait la note de naissance,
        # qui n'en depend pas. (C'est exactement le defaut que tout ce chantier
        # corrige : un fait acquis perdu a cause d'un calcul voisin.)
        try:
            nom = parent.name
            if nom:
                jumeau = self._workspace_root() / nom
                if jumeau.exists() and jumeau.resolve() != parent.resolve():
                    note += (
                        f"\n⚠️ Un dossier '{nom}' existe DEJA ici : {jumeau}. "
                        "Tu viens d'en creer un SECOND, ailleurs — ce que tu ecris "
                        "n'ira pas dans le premier. Verifie que c'est bien voulu."
                    )
        except Exception:
            pass
        return note

    def _syntax_errors(self, file_path: Path, content: str) -> List[str]:
        """Perform lightweight syntax checks by extension."""
        ext = file_path.suffix.lower()
        errors: List[str] = []

        if ext == ".json":
            try:
                json.loads(content)
            except json.JSONDecodeError as exc:
                errors.append(f"JSON invalide: {exc}")
            return errors

        if ext == ".py":
            try:
                ast.parse(content)
            except SyntaxError as exc:
                errors.append(f"Python invalide: {exc}")
            return errors

        if ext in {".html", ".htm"}:
            lowered = content.lower()
            if "<html" in lowered and "</html>" not in lowered:
                errors.append("HTML potentiellement tronque: </html> manquant.")
            if "<body" in lowered and "</body>" not in lowered:
                errors.append("HTML potentiellement tronque: </body> manquant.")

        if ext in {
            ".html",
            ".htm",
            ".css",
            ".js",
            ".jsx",
            ".ts",
            ".tsx",
            ".scss",
            ".sass",
            ".less",
        }:
            if not self._is_balanced(content):
                # Non-blocking: web files often have complex nesting;
                # log for debugging but don't reject the write.
                logger.debug(f"Delimiter balance warning for {file_path.name}")

        return errors

    def _is_balanced(self, text: str) -> bool:
        """Check brace/paren/bracket balance while ignoring quoted strings."""
        pairs = {"{": "}", "(": ")", "[": "]"}
        closing = {value: key for key, value in pairs.items()}
        stack: List[str] = []

        in_single = False
        in_double = False
        in_backtick = False
        escaped = False

        for ch in text:
            if in_single or in_double or in_backtick:
                if escaped:
                    escaped = False
                    continue
                if ch == "\\":
                    escaped = True
                    continue
                if in_single and ch == "'":
                    in_single = False
                elif in_double and ch == '"':
                    in_double = False
                elif in_backtick and ch == "`":
                    in_backtick = False
                continue

            if ch == "'":
                in_single = True
                continue
            if ch == '"':
                in_double = True
                continue
            if ch == "`":
                in_backtick = True
                continue

            if ch in pairs:
                stack.append(ch)
                continue
            if ch in closing:
                if not stack or stack[-1] != closing[ch]:
                    return False
                stack.pop()

        if in_single or in_double or in_backtick:
            return False
        return not stack
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
