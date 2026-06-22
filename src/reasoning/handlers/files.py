"""
files.py - Handlers fichiers fragmentés depuis react.py.

Handlers: read_file, write_file, edit_file, multi_edit_file, apply_patch,
          list_directory, find_files, delete_file, create_zip, open_file,
          view_outline + apply_patch interne (self_improve).

Chaque handler est une fonction async standalone:
    async def handler_name(ctx: HandlerContext, **kwargs) -> HandlerResult
"""

from __future__ import annotations

import ast
import os
import platform
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef
from ...tools.tree_sitter_parser import parse_file_outline

# P0.2: Security guardrails
try:
    from ...tools.file_guardrails import (
        PathSecurityError,
        _is_within,
        check_path_boundary,
        check_read_blacklist,
        check_write_blacklist,
        check_delete_allowed,
    )
except ImportError:
    PathSecurityError = Exception
    _is_within = None
    check_path_boundary = None
    check_read_blacklist = None
    check_write_blacklist = None
    check_delete_allowed = None


def _assert_write_boundary(resolved: Path, ctx: HandlerContext) -> None:
    """Lève PathSecurityError si resolved est hors workspace/lumena_root.

    À appeler en tête de chaque handler mutatif, juste après resolve_path().
    Empêche qu'un OutsideAccessGrant de lecture soit détourné en vecteur d'écriture.
    Silencieux si file_guardrails non disponible (mode test léger).
    """
    if ctx.file_guardrails is None or _is_within is None:
        return
    try:
        lr = ctx.lumena_root.resolve()
        wr = ctx.file_guardrails._workspace_root().resolve()
        rp = resolved.resolve() if resolved.exists() else resolved
        if not (_is_within(rp, lr) or _is_within(rp, wr)):
            raise PathSecurityError(
                f"Écriture refusée: {rp} est hors des limites autorisées. "
                "Un grant de lecture ne permet jamais d'écrire hors workspace."
            )
    except PathSecurityError:
        raise
    except Exception:
        pass  # Erreur de résolution — ne pas bloquer, laisser le handler gérer

# Imports optionnels (même pattern que react.py)
try:
    from ...tools.apply_patch import apply_patch as _apply_patch_fn, edit_file as _edit_file_fn, parse_patch as _parse_patch_fn
    from ...tools.compaction import get_token_stats as _get_token_stats_fn, format_token_stats as _format_token_stats_fn
    ADVANCED_TOOLS_AVAILABLE = True
except ImportError:
    ADVANCED_TOOLS_AVAILABLE = False
    _apply_patch_fn = None
    _edit_file_fn = None
    _parse_patch_fn = None

try:
    from ...telemetry import (
        compute_workspace_relative as _compute_workspace_relative,
        read_text_if_exists as _read_text_if_exists,
        get_file_edits_store as _get_file_edits_store,
        current_trace_context as _current_trace_context,
    )
    TELEMETRY_AVAILABLE = True
except Exception:
    TELEMETRY_AVAILABLE = False
    _compute_workspace_relative = None
    _read_text_if_exists = None
    _get_file_edits_store = None
    _current_trace_context = None

try:
    from ...runtime.context import get_current_runtime_context as _get_current_runtime_context
except Exception:
    _get_current_runtime_context = None


# ─── Helpers internes ──────────────────────────────────────────────────────

def _invalidate_read_cache(file_path: Path) -> None:
    """Levier 5: invalide le cache de lecture après toute modification."""
    try:
        from .batch import invalidate_file_cache
        invalidate_file_cache(file_path)
    except Exception:
        pass


async def _append_syntax_warning(message: str, target_path: Path, workspace_root: Optional[Path] = None) -> str:
    """P7 — Append un warning de syntaxe à un message de succès si applicable.

    Gardé par le flag REACT_QUALITY_GATES (opt-OUT).
    Ne modifie jamais le message en cas d'erreur (best-effort).
    """
    try:
        from src.config.codeagent_flags import REACT_QUALITY_GATES
        if not REACT_QUALITY_GATES:
            return message
        from src.utils.syntax_check import check_syntax
        warn = await check_syntax(target_path, workspace_root=workspace_root)
        if warn:
            return f"{message}\n\n⚠️ Syntaxe/lint : {warn}"
    except Exception as exc:
        logger.debug(f"[P7 syntax_check] skip: {exc}")
    return message


def _record_file_edit(
    ctx: HandlerContext,
    *,
    tool_name: str,
    action: str,
    file_path: Path,
    before_content: Optional[str],
    after_content: Optional[str],
    existed_before: bool,
    summary: str,
    workspace_relative: Optional[str] = None,
) -> None:
    """Enregistre un edit de fichier dans le store telemetry (même logique que ToolRegistry)."""
    if not TELEMETRY_AVAILABLE or _get_file_edits_store is None:
        return

    trace_id, turn_id = None, None
    if _current_trace_context is not None:
        try:
            tctx = _current_trace_context() or {}
            trace_id = tctx.get("trace_id")
            turn_id = tctx.get("turn_id")
        except Exception:
            pass  # trace context unavailable, non-critical

    if not trace_id:
        return

    try:
        store = _get_file_edits_store()
        store.start_edit_session(trace_id=trace_id, turn_id=turn_id)
        if workspace_relative is None and _compute_workspace_relative is not None:
            workspace_relative = _compute_workspace_relative(file_path, ctx.runtime_root)
        task_id = None
        if callable(_get_current_runtime_context):
            try:
                runtime_ctx = _get_current_runtime_context()
                task_id = getattr(runtime_ctx, "task_id", None) if runtime_ctx else None
            except Exception:
                task_id = None
        store.record_edit(
            trace_id=trace_id,
            turn_id=turn_id,
            task_id=task_id,
            tool_name=tool_name,
            action=action,
            file_path=str(file_path),
            workspace_relative=workspace_relative,
            before_content=before_content,
            after_content=after_content,
            existed_before=existed_before,
            summary=summary,
        )
    except Exception as exc:
        logger.debug("file_edit record skipped: {}", exc)


def _before_snapshot(resolved: Path):
    """Capture l'état d'un fichier avant modification."""
    if _read_text_if_exists is not None:
        return _read_text_if_exists(resolved)
    existed = resolved.exists()
    content = (
        resolved.read_text(encoding="utf-8", errors="replace")
        if existed and resolved.is_file()
        else None
    )
    return existed, content


# ─── P1: Destructive write guard (anti-CSS-catastrophe) ────────────────────

# Seuil de réduction qui déclenche le blocage (60% = garde-fou strict).
# Ex: 1392 lignes → 212 lignes = 85% de réduction = BLOQUÉ.
_DESTRUCTIVE_REDUCTION_THRESHOLD = 0.60
# Taille minimale du fichier existant pour activer le guard (évite faux-positifs sur petits fichiers)
_DESTRUCTIVE_MIN_SIZE_CHARS = 400


def _auto_backup_before_write(target_path: Path, before_content: Optional[str]) -> Optional[Path]:
    """Crée un backup timestampé dans .backups/ AVANT toute écriture destructrice.

    Retourne le chemin du backup ou None si non applicable.
    Best-effort : silencieux en cas d'échec (ne bloque JAMAIS l'écriture).
    """
    try:
        if not target_path.exists() or before_content is None:
            return None
        from datetime import datetime as _dt
        backup_dir = target_path.parent / ".backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"{target_path.name}.{ts}"
        backup_path.write_text(before_content, encoding="utf-8", errors="replace")
        logger.info("💾 [write_file] Backup auto → {}", backup_path)
        return backup_path
    except Exception as exc:
        logger.debug("Backup skipped: {}", exc)
        return None


def _check_destructive_write(
    target_path: Path,
    before_content: Optional[str],
    new_content: str,
    force_rewrite: bool,
    rewrite_reason: str,
) -> Optional[str]:
    """Retourne un message d'erreur si l'écriture est jugée destructrice et non autorisée.

    Règles :
    - Si fichier n'existait pas → pas de garde
    - Si before_content est None ou trop petit → pas de garde
    - Si new_size < before_size * (1 - seuil) → destructeur
    - force_rewrite=True + rewrite_reason non vide → autorisé (mais backup requis côté appelant)
    """
    if before_content is None or not target_path.exists():
        return None
    before_size = len(before_content)
    if before_size < _DESTRUCTIVE_MIN_SIZE_CHARS:
        return None
    new_size = len(new_content or "")
    if new_size >= before_size * (1 - _DESTRUCTIVE_REDUCTION_THRESHOLD):
        return None  # Réduction acceptable
    reduction_pct = int((1 - new_size / before_size) * 100) if before_size else 0
    if force_rewrite and str(rewrite_reason or "").strip():
        # Autorisé mais loggé fort
        logger.warning(
            "⚠️ [write_file] Écriture destructrice AUTORISÉE sur {} "
            "({}→{} chars, -{}%) — motif: {}",
            target_path.name, before_size, new_size, reduction_pct,
            str(rewrite_reason).strip()[:120],
        )
        return None
    return (
        f"❌ REFUS écriture destructrice: {target_path.name} passerait de "
        f"{before_size} → {new_size} caractères (-{reduction_pct}%). "
        f"Seuil critique : {int(_DESTRUCTIVE_REDUCTION_THRESHOLD*100)}%.\n"
        f"Pour écrire quand même, utilise force_rewrite=true ET rewrite_reason='motif explicite'.\n"
        f"💡 Préfère edit_file / apply_patch / str_replace pour des modifications ciblées."
    )


# ─── P1: Destructive write guard (anti-CSS-catastrophe) ────────────────────

# Seuil de réduction qui déclenche le blocage (60% = garde-fou strict).
# Ex: 1392 lignes → 212 lignes = 85% de réduction = BLOQUÉ.
_DESTRUCTIVE_REDUCTION_THRESHOLD = 0.60
# Taille minimale du fichier existant pour activer le guard (évite faux-positifs sur petits fichiers)
_DESTRUCTIVE_MIN_SIZE_CHARS = 400


def _auto_backup_before_write(target_path: Path, before_content: Optional[str]) -> Optional[Path]:
    """Crée un backup timestampé dans .backups/ AVANT toute écriture destructrice.

    Retourne le chemin du backup ou None si non applicable.
    Best-effort : silencieux en cas d'échec (ne bloque JAMAIS l'écriture).
    """
    try:
        if not target_path.exists() or before_content is None:
            return None
        from datetime import datetime as _dt
        backup_dir = target_path.parent / ".backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = _dt.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"{target_path.name}.{ts}"
        backup_path.write_text(before_content, encoding="utf-8", errors="replace")
        logger.info("💾 [write_file] Backup auto → {}", backup_path)
        return backup_path
    except Exception as exc:
        logger.debug("Backup skipped: {}", exc)
        return None


def _check_destructive_write(
    target_path: Path,
    before_content: Optional[str],
    new_content: str,
    force_rewrite: bool,
    rewrite_reason: str,
) -> Optional[str]:
    """Retourne un message d'erreur si l'écriture est jugée destructrice et non autorisée.

    Règles :
    - Si fichier n'existait pas → pas de garde
    - Si before_content est None ou trop petit → pas de garde
    - Si new_size < before_size * (1 - seuil) → destructeur
    - force_rewrite=True + rewrite_reason non vide → autorisé (mais backup requis côté appelant)
    """
    if before_content is None or not target_path.exists():
        return None
    before_size = len(before_content)
    if before_size < _DESTRUCTIVE_MIN_SIZE_CHARS:
        return None
    new_size = len(new_content or "")
    if new_size >= before_size * (1 - _DESTRUCTIVE_REDUCTION_THRESHOLD):
        return None  # Réduction acceptable
    reduction_pct = int((1 - new_size / before_size) * 100) if before_size else 0
    if force_rewrite and str(rewrite_reason or "").strip():
        # Autorisé mais loggé fort
        logger.warning(
            "⚠️ [write_file] Écriture destructrice AUTORISÉE sur {} "
            "({}→{} chars, -{}%) — motif: {}",
            target_path.name, before_size, new_size, reduction_pct,
            str(rewrite_reason).strip()[:120],
        )
        return None
    return (
        f"❌ REFUS écriture destructrice: {target_path.name} passerait de "
        f"{before_size} → {new_size} caractères (-{reduction_pct}%). "
        f"Seuil critique : {int(_DESTRUCTIVE_REDUCTION_THRESHOLD*100)}%.\n"
        f"Pour écrire quand même, utilise force_rewrite=true ET rewrite_reason='motif explicite'.\n"
        f"💡 Préfère edit_file / apply_patch / str_replace pour des modifications ciblées."
    )


def _after_snapshot(resolved: Path) -> Optional[str]:
    """Lit le contenu après modification."""
    if _read_text_if_exists is not None:
        _exists, content = _read_text_if_exists(resolved)
        return content
    if resolved.exists() and resolved.is_file():
        return resolved.read_text(encoding="utf-8", errors="replace")
    return None


# ─── Handlers ──────────────────────────────────────────────────────────────

# Clés sensibles dont la VALEUR doit être masquée dans toute sortie read_file
# (config.php, .env, ini, json…). On garde la clé, on remplace la valeur.
_SECRET_KEY_ALT = (
    r"db_?pass(?:word)?|password|passwd|pwd|secret|secret_key|"
    r"client_secret|api_?key|access_?token|auth_?token|token|"
    r"admin_setup_token|private_key|webhook_secret|aws_secret\w*"
)
_REDACTED = "***REDACTED***"

# PHP define('KEY', 'value') / define("KEY","value")
_RE_PHP_DEFINE = re.compile(
    r"(define\(\s*['\"]\w*(?:" + _SECRET_KEY_ALT + r")\w*['\"]\s*,\s*['\"])([^'\"]*)(['\"])",
    re.IGNORECASE,
)
# Affectations / mappings : KEY='value' | KEY: "value" | 'KEY' => "value" | KEY=value | $key='value'
_RE_ASSIGN = re.compile(
    r"([\$'\"]?\w*(?:" + _SECRET_KEY_ALT + r")\w*['\"]?\s*(?:=>|[:=])\s*['\"]?)"
    r"([^'\"\r\n,;}]+?)"
    r"(['\"]?\s*[,;)\r\n]|['\"]?$)",
    re.IGNORECASE | re.MULTILINE,
)


def _redact_secrets(text: str) -> str:
    """Masque les valeurs des clés sensibles (PHP define/assign, env, json, ini).

    Ne modifie jamais les noms de clés — seulement les valeurs — pour rester lisible
    sans jamais exposer de secret en observation/logs/conversation.
    """
    if not text:
        return text
    out = _RE_PHP_DEFINE.sub(lambda m: m.group(1) + _REDACTED + m.group(3), text)
    out = _RE_ASSIGN.sub(lambda m: m.group(1) + _REDACTED + m.group(3), out)
    return out


async def read_file_handler(
    ctx: HandlerContext,
    path: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> HandlerResult:
    """Lit un fichier avec pagination lignes. Les valeurs de secrets sont masquées."""
    try:
        resolved = ctx.resolve_path(path)
        # P0.2: block reads to secrets / private data
        if check_read_blacklist is not None:
            try:
                check_read_blacklist(resolved, ctx.lumena_root)
            except PathSecurityError as sec_err:
                return HandlerResult.fail(str(sec_err), handler_name="read_file")
        if not resolved.exists():
            _home = Path.home()
            return HandlerResult.ok(
                f"❌ Fichier non trouvé: {path}\n"
                f"💡 Chemins essayés: workspace/, lumena/, cwd/\n"
                f"💡 Home utilisateur: {_home}\n"
                f"💡 Bureau: {_home / 'Desktop'}",
                handler_name="read_file",
            )

        # Levier 5: lecture via cache LRU + invalidation mtime
        try:
            from .batch import _read_text_cached as _cached_read
            content = _cached_read(resolved)
            if not content and resolved.stat().st_size > 0:
                # Cache a échoué (ex. résolution iffy) → fallback direct
                content = resolved.read_text(encoding="utf-8", errors="replace")
        except Exception:
            content = resolved.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        total_lines = len(lines)
        if total_lines == 0:
            return HandlerResult.ok(f"📄 {path} est vide.", handler_name="read_file")

        page_size = ctx.ide_read_page_size() if ctx.is_ide_runtime() else 350
        has_custom_range = start_line is not None or end_line is not None
        start = max(1, int(start_line) if start_line is not None else 1)

        if end_line is not None:
            end = max(start, int(end_line))
        elif has_custom_range:
            end = start + page_size - 1
        else:
            # Lecture complète sans troncature quand aucune plage n'est spécifiée
            end = total_lines

        start = min(start, total_lines)
        end = min(end, total_lines)
        selected = lines[start - 1:end]
        body = _redact_secrets("\n".join(selected))  # masque toute valeur de secret

        header = f"📄 {path} (lignes {start}-{end}/{total_lines})"
        if end < total_lines:
            next_start = end + 1
            next_end = min(next_start + page_size - 1, total_lines)
            header += (
                f"\n[...SUITE DISPONIBLE: read_file(path='{path}', "
                f"start_line={next_start}, end_line={next_end})]"
            )

        return HandlerResult.ok(f"{header}\n{body}", handler_name="read_file")
    except Exception as e:
        return HandlerResult.fail(f"Erreur lecture: {e}", handler_name="read_file")


async def list_directory_handler(ctx: HandlerContext, path: str = ".") -> HandlerResult:
    """Liste les fichiers et dossiers d'un répertoire."""
    try:
        dir_path = ctx.resolve_path(path, want_dir=True)

        if not dir_path.exists():
            _home = Path.home()
            return HandlerResult.ok(
                f"❌ Répertoire non trouvé: {path}\n"
                f"💡 Essayé: workspace/, lumena/, cwd/\n"
                f"💡 Home utilisateur: {_home}\n"
                f"💡 Bureau: {_home / 'Desktop'}\n"
                f"💡 Si tu cherches le bureau, utilise le chemin EXACT: {_home / 'Desktop'}",
                handler_name="list_directory",
            )
        if not dir_path.is_dir():
            return HandlerResult.ok(f"❌ Ce n'est pas un répertoire: {path}", handler_name="list_directory")

        items = []
        for item in sorted(dir_path.iterdir()):
            if item.is_dir():
                items.append(f"📁 {item.name}/")
            else:
                items.append(f"📄 {item.name}")

        if not items:
            return HandlerResult.ok(f"📂 {path} est vide", handler_name="list_directory")

        # Tracker sécurisé : mémoriser les .exe découverts pour whitelist dynamique
        for item in dir_path.iterdir():
            if item.is_file() and item.suffix.lower() == ".exe":
                ctx._discovered_executables.add(item.name.lower())
                # Aussi le nom sans extension
                ctx._discovered_executables.add(item.stem.lower())
                logger.debug("🔓 Exe découvert et autorisé pour la session: %s", item.name)

        max_items = ctx.ide_list_max_items() if ctx.is_ide_runtime() else 50
        suffix = ""
        if len(items) > max_items:
            suffix = f"\n[... {len(items) - max_items} elements supplementaires non affiches ...]"
        return HandlerResult.ok(
            f"📂 Contenu de {path}:\n" + "\n".join(items[:max_items]) + suffix,
            handler_name="list_directory",
        )
    except Exception as e:
        return HandlerResult.fail(f"Erreur liste: {e}", handler_name="list_directory")


async def find_files_handler(ctx: HandlerContext, pattern: str, path: str = "workspace") -> HandlerResult:
    """Recherche recursive de fichiers par nom/pattern."""
    try:
        pattern_text = str(pattern or "").strip()
        if not pattern_text:
            return HandlerResult.ok("Pattern vide pour find_files", handler_name="find_files")

        root_dir = ctx.resolve_path(path, want_dir=True)
        if not root_dir.exists():
            return HandlerResult.ok(f"Repertoire non trouve: {path}", handler_name="find_files")
        if not root_dir.is_dir():
            return HandlerResult.ok(f"Ce n'est pas un repertoire: {path}", handler_name="find_files")

        # Splitter multi-patterns séparés par espaces ou virgules
        _raw_patterns = [p.strip() for p in re.split(r'[,\s]+', pattern_text) if p.strip()]
        if not _raw_patterns:
            _raw_patterns = [pattern_text]

        max_results = ctx.ide_find_max_results() if ctx.is_ide_runtime() else 80
        matches: List[Path] = []
        dir_matches: List[Path] = []

        for _sub_pattern in _raw_patterns:
            has_glob = any(ch in _sub_pattern for ch in "*?[]")
            _sub_lower = _sub_pattern.lower()

            if has_glob:
                for candidate in root_dir.rglob(_sub_pattern):
                    if candidate.is_file() and candidate not in matches:
                        matches.append(candidate)
                        if len(matches) >= max_results:
                            break
                    elif candidate.is_dir() and candidate not in dir_matches:
                        dir_matches.append(candidate)
            else:
                for candidate in root_dir.rglob("*"):
                    if candidate.is_dir() and _sub_lower in candidate.name.lower() and candidate not in dir_matches:
                        dir_matches.append(candidate)
                    elif candidate.is_file() and _sub_lower in candidate.name.lower() and candidate not in matches:
                        matches.append(candidate)
                        if len(matches) >= max_results:
                            break
            if len(matches) >= max_results:
                break

        # Fallback projet root — si rien trouvé dans workspace/, chercher depuis ROOT_DIR
        if not matches and not dir_matches:
            from ...utils.paths import ROOT_DIR
            project_root = ROOT_DIR.resolve()
            if project_root != root_dir.resolve():
                for _sub_pattern in _raw_patterns:
                    has_glob = any(ch in _sub_pattern for ch in "*?[]")
                    _sub_lower = _sub_pattern.lower()
                    if has_glob:
                        for candidate in project_root.rglob(_sub_pattern):
                            if candidate.is_file() and candidate not in matches:
                                matches.append(candidate)
                                if len(matches) >= max_results:
                                    break
                    else:
                        for candidate in project_root.rglob("*"):
                            if candidate.is_dir() and _sub_lower in candidate.name.lower() and candidate not in dir_matches:
                                dir_matches.append(candidate)
                            elif candidate.is_file() and _sub_lower in candidate.name.lower() and candidate not in matches:
                                matches.append(candidate)
                                if len(matches) >= max_results:
                                    break
                    if len(matches) >= max_results:
                        break

        # Fallback journal
        if not matches and "journal" in pattern_text.lower():
            from ...utils.paths import JOURNAL_DIR
            journal_dir = JOURNAL_DIR
            if journal_dir.exists():
                for candidate in journal_dir.rglob("*"):
                    if not candidate.is_file():
                        continue
                    if any(ch in pattern_text for ch in "*?[]"):
                        if candidate.match(pattern_text):
                            matches.append(candidate)
                    else:
                        if pattern_text.lower() in candidate.name.lower() or "journal" in candidate.name.lower():
                            matches.append(candidate)
                    if len(matches) >= max_results:
                        break

        if not matches and not dir_matches:
            return HandlerResult.ok(
                f"Aucun fichier trouve pour '{pattern_text}' dans {path}",
                handler_name="find_files",
            )

        file_lines = []
        for d in dir_matches:
            try:
                rel = d.relative_to(ctx.runtime_root)
                file_lines.append(f"- 📁 {rel.as_posix()}/")
            except Exception:
                file_lines.append(f"- 📁 {d}/")
        for file_path in matches:
            try:
                rel = file_path.relative_to(ctx.runtime_root)
                file_lines.append(f"- {rel.as_posix()}")
            except Exception:
                file_lines.append(f"- {file_path}")

        total_found = len(dir_matches) + len(matches)
        result_lines = [f"Fichiers trouves pour '{pattern_text}' ({total_found}):"]
        result_lines.extend(file_lines)
        if len(matches) >= max_results:
            result_lines.append(f"... (limite a {max_results} resultats)")
        return HandlerResult.ok("\n".join(result_lines), handler_name="find_files")
    except Exception as e:
        return HandlerResult.fail(f"Erreur find_files: {e}", handler_name="find_files")


async def open_file_handler(ctx: HandlerContext, path: str) -> HandlerResult:
    """Ouvre un fichier dans son application par défaut."""
    resolved = ctx.resolve_path(path)
    if not resolved.exists():
        return HandlerResult.ok(
            f"❌ Fichier non trouvé: {path}\n💡 Essayé: workspace/, lumena/, cwd/",
            handler_name="open_file",
        )
    try:
        system = platform.system()
        if system == "Windows":
            os.startfile(str(resolved))
        elif system == "Darwin":
            subprocess.Popen(["open", str(resolved)])
        else:
            subprocess.Popen(["xdg-open", str(resolved)])
        return HandlerResult.ok(
            f"✅ Fichier ouvert: {resolved.name}\n📂 Chemin: {resolved}",
            handler_name="open_file",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur ouverture: {e}", handler_name="open_file")


async def write_file_handler(
    ctx: HandlerContext,
    path: str = None,
    content: str = None,
    input: str = None,
    project: str = None,
    file_path: str = None,
    force_rewrite: bool = False,
    rewrite_reason: str = "",
) -> HandlerResult:
    """Ecrit dans un fichier avec support workspace intelligent."""
    if path is None and file_path is not None:
        path = file_path
    if path is None:
        return HandlerResult.ok("❌ Erreur: parametre 'path' (ou 'file_path') requis", handler_name="write_file")
    if content is None and input is not None:
        content = input
    if content is None:
        return HandlerResult.ok("❌ Erreur: parametre 'content' (ou 'input') requis", handler_name="write_file")

    try:
        ide_runtime = ctx.is_ide_runtime()
        requested_path = Path(path)
        ide_direct_path = False
        if ide_runtime and requested_path.is_absolute():
            target_path = requested_path
            resolved_workspace_relative = ""
            ide_direct_path = True
        else:
            target_path, _redirected, resolved_workspace_relative = ctx.file_guardrails.resolve_write_target(
                path,
                project_name=project,
            )
        # P0.2: vérifier la boundary avant toute écriture (couvre le chemin IDE direct)
        try:
            _assert_write_boundary(target_path, ctx)
        except PathSecurityError as sec_err:
            return HandlerResult.fail(str(sec_err), handler_name="write_file")
        # P0.2: block writes to protected zones (.env, data/, models/, backups/)
        if check_write_blacklist is not None:
            try:
                check_write_blacklist(target_path, ctx.lumena_root)
            except PathSecurityError as sec_err:
                return HandlerResult.fail(str(sec_err), handler_name="write_file")
        existed_before, before_content = _before_snapshot(target_path)

        patch_strict = ctx.patch_strict_enabled()
        if patch_strict and existed_before and not force_rewrite:
            return HandlerResult.ok(
                "❌ Patch strict actif: fichier existant. "
                "Utilise edit_file/apply_patch pour modifier une partie, "
                "ou force_rewrite=true avec rewrite_reason explicite.",
                handler_name="write_file",
            )
        if patch_strict and existed_before and force_rewrite and not str(rewrite_reason or "").strip():
            return HandlerResult.ok(
                "❌ Patch strict actif: rewrite_reason est requis quand force_rewrite=true.",
                handler_name="write_file",
            )

        # ── P1: Garde-fou destructif (anti-CSS-catastrophe) ──
        # Bloque les réductions massives (>60%) sauf force_rewrite + rewrite_reason
        _destructive_err = _check_destructive_write(
            target_path, before_content, content, force_rewrite, rewrite_reason,
        )
        if _destructive_err:
            return HandlerResult.ok(_destructive_err, handler_name="write_file")
        # Backup auto avant toute écriture écrasant un fichier existant
        if existed_before:
            _auto_backup_before_write(target_path, before_content)

        if ide_runtime and ide_direct_path:
            target_path.parent.mkdir(parents=True, exist_ok=True)
            target_path.write_text(content, encoding="utf-8")
            # Readback validation (même protection que write_file_strict)
            if not target_path.exists():
                return HandlerResult.ok(
                    f"❌ Écriture échouée: le fichier n'a pas été créé sur disque ({target_path})",
                    handler_name="write_file",
                )
            readback = target_path.read_text(encoding="utf-8")
            if readback != content:
                return HandlerResult.ok(
                    f"❌ Écriture corrompue: readback != contenu attendu ({target_path})",
                    handler_name="write_file",
                )
            write_file_path = target_path
            write_workspace_relative = resolved_workspace_relative or ""
            write_message = f"✅ Fichier ecrit: {target_path.name} ({len(content)} caracteres)"
        else:
            write_result = ctx.file_guardrails.write_file_strict(
                path=path,
                content=content,
                project_name=project,
                require_non_empty=True,
            )
            if not write_result.success:
                details = "; ".join(write_result.validation_errors) if write_result.validation_errors else write_result.message
                return HandlerResult.ok(f"❌ Validation ecriture echouee: {details}", handler_name="write_file")
            write_file_path = write_result.file_path
            write_workspace_relative = write_result.workspace_relative
            write_message = write_result.message

        action = "updated" if existed_before else "created"
        summary = f"{action}: {write_file_path.name}"
        if existed_before and force_rewrite and str(rewrite_reason or "").strip():
            summary += f" (force_rewrite: {rewrite_reason.strip()[:80]})"
        _record_file_edit(
            ctx,
            tool_name="write_file",
            action=action,
            file_path=write_file_path,
            before_content=before_content,
            after_content=content,
            existed_before=existed_before,
            summary=summary,
            workspace_relative=write_workspace_relative or resolved_workspace_relative,
        )
        _invalidate_read_cache(write_file_path)
        # P7 — syntax/lint warning (opt-OUT via LUMENA_REACT_QUALITY_GATES)
        write_message = await _append_syntax_warning(
            write_message, write_file_path, workspace_root=ctx.lumena_root,
        )
        return HandlerResult.ok(write_message, handler_name="write_file")
    except Exception as e:
        return HandlerResult.fail(f"Erreur ecriture: {e}", handler_name="write_file")


async def delete_file_handler(ctx: HandlerContext, path: str) -> HandlerResult:
    """Supprime un fichier. Protège src/ et data/ de Lumena."""
    try:
        if not path:
            return HandlerResult.fail("❌ delete_file: paramètre 'path' requis", handler_name="delete_file")
        file_path = ctx.resolve_path(path, want_dir=False)
        if not file_path.exists():
            return HandlerResult.fail(f"❌ Fichier introuvable: {path}", handler_name="delete_file")
        if not file_path.is_file():
            return HandlerResult.fail(f"❌ N'est pas un fichier: {path}", handler_name="delete_file")
        # P0.2: Only allow deletions inside workspace/ by default
        if check_delete_allowed is not None:
            try:
                check_delete_allowed(file_path, ctx.lumena_root, ctx.file_guardrails._workspace_root())
            except PathSecurityError as sec_err:
                return HandlerResult.fail(str(sec_err), handler_name="delete_file")
        else:
            # Fallback: legacy protection for src/ and data/
            lumena_root = ctx.lumena_root
            from ...utils.paths import DATA_DIR
            for protected in (lumena_root / "src", DATA_DIR):
                try:
                    file_path.resolve().relative_to(protected.resolve())
                    return HandlerResult.fail(
                        f"❌ Suppression refusée: fichier protégé ({protected.name}/): {file_path}",
                        handler_name="delete_file",
                    )
                except ValueError:
                    pass
        file_path.unlink()
        _invalidate_read_cache(file_path)
        return HandlerResult.ok(
            f"🗑️ Fichier supprimé\n- fichier: {file_path.name}\n- chemin: {file_path}",
            handler_name="delete_file",
        )
    except PermissionError:
        return HandlerResult.fail(f"❌ Permission refusée: {path}", handler_name="delete_file")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur delete_file: {e}", handler_name="delete_file")


async def create_zip_handler(
    ctx: HandlerContext,
    source_paths: str,
    zip_path: str = "",
    overwrite: bool = True,
) -> HandlerResult:
    """Crée un fichier ZIP depuis un ou plusieurs chemins (fichiers ou dossiers)."""
    import zipfile
    import os
    from datetime import datetime
    try:
        if isinstance(source_paths, (list, tuple)):
            source_paths = ",".join(str(p) for p in source_paths)
        raw_items = [s.strip() for s in (source_paths or "").split(",") if s.strip()]
        if not raw_items:
            return HandlerResult.fail(
                "❌ create_zip: source_paths requis (ex: 'fichier.txt,dossier/')",
                handler_name="create_zip",
            )

        resolved_sources: List[Path] = []
        for item in raw_items:
            src = ctx.resolve_path(item, want_dir=False)
            if not src.exists():
                return HandlerResult.fail(
                    f"❌ create_zip: source introuvable: {item}", handler_name="create_zip"
                )
            resolved_sources.append(src)

        root = ctx.lumena_root
        if zip_path and str(zip_path).strip():
            out_zip = ctx.resolve_path(str(zip_path).strip(), want_dir=False)
        else:
            out_zip = root / f"archive_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"

        if out_zip.suffix.lower() != ".zip":
            out_zip = out_zip.with_suffix(".zip")
        try:
            _assert_write_boundary(out_zip, ctx)
        except PathSecurityError as sec_err:
            return HandlerResult.fail(str(sec_err), handler_name="create_zip")
        if out_zip.exists() and out_zip.is_dir():
            return HandlerResult.fail(
                f"❌ create_zip: le chemin de sortie est un dossier: {out_zip}",
                handler_name="create_zip",
            )
        if out_zip.exists() and not overwrite:
            return HandlerResult.fail(
                f"❌ create_zip: fichier déjà existant (overwrite=False): {out_zip}",
                handler_name="create_zip",
            )

        out_zip.parent.mkdir(parents=True, exist_ok=True)
        if out_zip.exists() and overwrite:
            out_zip.unlink()

        base_candidates = [str(p if p.is_dir() else p.parent) for p in resolved_sources]
        try:
            common_base = Path(os.path.commonpath(base_candidates))
        except Exception:
            common_base = resolved_sources[0].parent

        added_files = 0
        added_dirs = 0
        with zipfile.ZipFile(out_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for src in resolved_sources:
                if src.is_file():
                    try:
                        arcname = src.relative_to(common_base)
                    except Exception:
                        arcname = Path(src.name)
                    zf.write(src, arcname=str(arcname))
                    added_files += 1
                    continue
                if src.is_dir():
                    added_dirs += 1
                    for child in src.rglob("*"):
                        if child.is_file():
                            try:
                                arcname = child.relative_to(common_base)
                            except Exception:
                                arcname = Path(src.name) / child.relative_to(src)
                            zf.write(child, arcname=str(arcname))
                            added_files += 1

        return HandlerResult.ok(
            f"✅ ZIP créé\n- sortie: {out_zip}\n- fichiers ajoutés: {added_files}\n- dossiers sources: {added_dirs}",
            handler_name="create_zip",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur create_zip: {e}", handler_name="create_zip")


async def edit_file_handler(
    ctx: HandlerContext,
    file_path: str,
    old_content: str,
    new_content: str,
) -> HandlerResult:
    """Modifie un fichier en remplacant old_content par new_content."""
    if not ADVANCED_TOOLS_AVAILABLE:
        return HandlerResult.ok("❌ Module apply_patch non disponible", handler_name="edit_file")
    try:
        resolved = ctx.resolve_path(file_path)
        try:
            _assert_write_boundary(resolved, ctx)
        except PathSecurityError as sec_err:
            return HandlerResult.fail(str(sec_err), handler_name="edit_file")
        existed_before, before_content = _before_snapshot(resolved)

        result = await _edit_file_fn(
            file_path=str(resolved),
            old_content=old_content,
            new_content=new_content,
            workspace_root=ctx.lumena_root,
        )
        result_text = str(result)
        if result_text.strip().startswith(("❌", "Erreur")):
            # P7 — auto-relecture si le contenu n'a pas été trouvé : aide le LLM au tour suivant
            try:
                from src.config.codeagent_flags import REACT_QUALITY_GATES
                _lower = result_text.lower()
                _not_found = any(
                    s in _lower for s in ("non trouv", "pas trouv", "not found", "introuvable")
                )
                if REACT_QUALITY_GATES and _not_found and resolved.exists():
                    try:
                        _current = resolved.read_text(encoding="utf-8", errors="ignore")
                        _preview = "\n".join(
                            f"{i+1:>4} | {line}" for i, line in enumerate(_current.splitlines()[:80])
                        )
                        result = (
                            f"{result_text}\n\n"
                            f"💡 Contenu actuel de {resolved.name} (80 premières lignes) — "
                            "copie les lignes EXACTES pour ton prochain edit_file :\n"
                            f"{_preview}"
                        )
                    except Exception:
                        pass
            except Exception:
                pass
            return HandlerResult.ok(result, handler_name="edit_file")

        if ctx.file_guardrails is not None:
            validation_errors = ctx.file_guardrails.validate_existing_file(
                file_path=resolved,
                require_non_empty=True,
            )
            if validation_errors:
                return HandlerResult.ok(
                    f"❌ Validation apres edition echouee: {'; '.join(validation_errors)}",
                    handler_name="edit_file",
                )

        after_content = _after_snapshot(resolved)
        _invalidate_read_cache(resolved)
        _record_file_edit(
            ctx,
            tool_name="edit_file",
            action="edited",
            file_path=resolved,
            before_content=before_content,
            after_content=after_content,
            existed_before=existed_before,
            summary=f"edited: {resolved.name}",
            workspace_relative=(_compute_workspace_relative(resolved, ctx.lumena_root)
                                if _compute_workspace_relative else None),
        )
        # P7 — syntax/lint warning post-edit
        result_str = await _append_syntax_warning(
            str(result), resolved, workspace_root=ctx.lumena_root,
        )
        return HandlerResult.ok(result_str, handler_name="edit_file")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur edit_file: {e}", handler_name="edit_file")


async def multi_edit_file_handler(ctx: HandlerContext, edits: list) -> HandlerResult:
    """Editions multiples en un seul appel."""
    if not ADVANCED_TOOLS_AVAILABLE:
        return HandlerResult.ok("❌ Module apply_patch non disponible", handler_name="multi_edit_file")
    try:
        from ...tools.apply_patch import multi_edit_file as _multi_edit_fn

        workspace_root = ctx.lumena_root
        # Pré-résoudre les chemins via ctx.resolve_path pour cohérence
        resolved_edits = []
        before_map: Dict[str, Dict[str, Any]] = {}
        for edit in edits or []:
            if not isinstance(edit, dict):
                continue
            fp = str(edit.get("file_path", "") or edit.get("file", "") or edit.get("path", "") or "").strip()
            if not fp:
                continue
            resolved = ctx.resolve_path(fp)
            existed_before, before_content = _before_snapshot(resolved)
            before_map[str(resolved)] = {
                "path": resolved,
                "existed_before": existed_before,
                "before_content": before_content,
                "file_path": fp,
            }
            # Réécrire l'edit avec le chemin résolu absolu
            resolved_edit = dict(edit)
            resolved_edit["file"] = str(resolved)
            resolved_edit["file_path"] = str(resolved)
            resolved_edit["path"] = str(resolved)
            resolved_edits.append(resolved_edit)

        result = _multi_edit_fn(resolved_edits if resolved_edits else edits, base_path=None)
        result_text = str(result)

        if not result_text.strip().startswith(("❌", "Erreur", "Error")):
            for info in before_map.values():
                resolved = info["path"]
                after_content = _after_snapshot(resolved)
                _record_file_edit(
                    ctx,
                    tool_name="multi_edit_file",
                    action="edited",
                    file_path=resolved,
                    before_content=info.get("before_content"),
                    after_content=after_content,
                    existed_before=bool(info.get("existed_before")),
                    summary=f"multi_edit: {info.get('file_path')}",
                    workspace_relative=(_compute_workspace_relative(resolved, ctx.lumena_root)
                                        if _compute_workspace_relative else None),
                )

        return HandlerResult.ok(result, handler_name="multi_edit_file")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur multi_edit: {e}", handler_name="multi_edit_file")


def insert_at_anchor_core(
    file_text: str,
    anchor: str,
    content: str,
    position: str = "before",
    occurrence: Any = "first",
) -> str:
    """
    Insère ``content`` autour d'une ancre textuelle (language-agnostic).

    - position ∈ {"before", "after", "replace"}
    - occurrence ∈ {"first", "last", int (1-indexed)}

    Raises ValueError si l'ancre est absente ou si les paramètres sont invalides.
    Retourne le nouveau contenu complet.
    """
    if not anchor:
        raise ValueError("anchor vide")
    pos = (position or "before").lower().strip()
    if pos not in ("before", "after", "replace"):
        raise ValueError(f"position invalide: {position!r} (attendu: before/after/replace)")

    # Collecte toutes les positions de l'ancre (exact match).
    offsets: List[int] = []
    start = 0
    while True:
        idx = file_text.find(anchor, start)
        if idx < 0:
            break
        offsets.append(idx)
        start = idx + 1  # overlaps OK

    if not offsets:
        raise ValueError(f"anchor introuvable: {anchor!r}")

    # Résolution de l'occurrence.
    if isinstance(occurrence, str):
        occ_norm = occurrence.lower().strip()
        if occ_norm in ("first", "", "1"):
            target = offsets[0]
        elif occ_norm == "last":
            target = offsets[-1]
        else:
            try:
                n = int(occ_norm)
            except ValueError:
                raise ValueError(f"occurrence invalide: {occurrence!r}")
            if n < 1 or n > len(offsets):
                raise ValueError(f"occurrence {n} hors bornes (1..{len(offsets)})")
            target = offsets[n - 1]
    elif isinstance(occurrence, int):
        if occurrence < 1 or occurrence > len(offsets):
            raise ValueError(f"occurrence {occurrence} hors bornes (1..{len(offsets)})")
        target = offsets[occurrence - 1]
    else:
        raise ValueError(f"occurrence invalide: {occurrence!r}")

    anchor_len = len(anchor)
    if pos == "before":
        # Préserve l'indentation de la ligne de l'ancre si content n'est pas déjà indenté.
        line_start = file_text.rfind("\n", 0, target) + 1
        indent = file_text[line_start:target]
        content_has_leading_ws = content and content[0] in (" ", "\t")
        if indent and indent.strip() == "" and not content_has_leading_ws:
            # Préfixe chaque ligne du content avec l'indent de l'ancre.
            insertion = indent + content.rstrip("\n") + "\n"
        else:
            insertion = content if content.endswith("\n") else content + "\n"
        # Insertion au début de la ligne de l'ancre.
        new_text = file_text[:line_start] + insertion + file_text[line_start:]
    elif pos == "after":
        # Insère juste après l'ancre (même ligne, continue en ligne suivante si content commence par \n).
        end_of_anchor = target + anchor_len
        if content.startswith("\n"):
            insertion = content
        else:
            insertion = "\n" + content
        if not insertion.endswith("\n"):
            insertion += "\n"
        new_text = file_text[:end_of_anchor] + insertion + file_text[end_of_anchor:]
    else:  # replace
        new_text = file_text[:target] + content + file_text[target + anchor_len:]

    return new_text


async def insert_at_anchor_handler(
    ctx: HandlerContext,
    path: str,
    anchor: str,
    content: str,
    position: str = "before",
    occurrence: Any = "first",
) -> HandlerResult:
    """
    Insère ``content`` autour d'une ancre textuelle dans un fichier.

    Action 1-shot pour remplacer le pattern "grep+read_file+str_replace" :
    - HTML : anchor="</main>" / "</body>" / "<!-- DASHBOARD -->"
    - Python : anchor="# END IMPORTS" / "def main():"
    - JS/TS : anchor="export default" / "// EOF"
    - Java/C# : anchor="} // end class"

    position ∈ {"before", "after", "replace"}
    occurrence ∈ {"first", "last", N} (N = 1-indexed)
    """
    try:
        resolved = ctx.resolve_path(path)
        try:
            _assert_write_boundary(resolved, ctx)
        except PathSecurityError as sec_err:
            return HandlerResult.fail(str(sec_err), handler_name="insert_at_anchor")
        if check_write_blacklist is not None:
            try:
                check_write_blacklist(resolved, ctx.lumena_root)
            except PathSecurityError as sec_err:
                return HandlerResult.fail(str(sec_err), handler_name="insert_at_anchor")

        if not resolved.exists():
            return HandlerResult.ok(
                f"❌ Fichier introuvable: {path}",
                handler_name="insert_at_anchor",
            )

        existed_before, before_content = _before_snapshot(resolved)
        file_text = before_content or ""

        try:
            new_text = insert_at_anchor_core(
                file_text=file_text,
                anchor=anchor,
                content=content,
                position=position,
                occurrence=occurrence,
            )
        except ValueError as ve:
            return HandlerResult.ok(
                f"❌ insert_at_anchor: {ve}. Relis le fichier et vérifie l'ancre exacte.",
                handler_name="insert_at_anchor",
            )

        if new_text == file_text:
            return HandlerResult.ok(
                f"⚠️ insert_at_anchor: aucune modification (contenu identique)",
                handler_name="insert_at_anchor",
            )

        resolved.write_text(new_text, encoding="utf-8")

        after_content = _after_snapshot(resolved)
        _invalidate_read_cache(resolved)
        _record_file_edit(
            ctx,
            tool_name="insert_at_anchor",
            action="patched",
            file_path=resolved,
            before_content=before_content,
            after_content=after_content,
            existed_before=existed_before,
            summary=f"insert_at_anchor[{position}]: {resolved.name}",
            workspace_relative=(_compute_workspace_relative(resolved, ctx.lumena_root)
                                if _compute_workspace_relative else None),
        )
        return HandlerResult.ok(
            f"✅ insert_at_anchor({position}) OK dans {path} (ancre: {anchor[:40]!r})",
            handler_name="insert_at_anchor",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur insert_at_anchor: {e}", handler_name="insert_at_anchor")


async def apply_patch_handler(
    ctx: HandlerContext,
    file_path: str,
    old_content: str,
    new_content: str,
    description: str = "",
) -> HandlerResult:
    """Applique un patch au code source de LUMENA ou à un fichier workspace."""
    try:
        # D'abord essayer de résoudre le chemin via le système normal (workspace inclus)
        resolved = ctx.resolve_path(file_path)
        try:
            _assert_write_boundary(resolved, ctx)
        except PathSecurityError as sec_err:
            return HandlerResult.fail(str(sec_err), handler_name="apply_patch")

        # Si le fichier résolu existe, utiliser edit_file directement (plus robuste)
        if resolved.exists():
            result = await _edit_file_fn(
                file_path=str(resolved),
                old_content=old_content,
                new_content=new_content,
                workspace_root=ctx.lumena_root,
            )
            result_text = str(result)
            if not result_text.strip().startswith(("❌", "Erreur")):
                try:
                    from ...learning.instincts import get_instinct_system
                    instincts = get_instinct_system()
                    instincts.learn(
                        pattern=f"modification de {file_path}",
                        response=description or "patch appliqué",
                        was_successful=True,
                        category="code",
                    )
                except Exception as e:
                    logger.debug("[files] apply_patch learn: %s", e)
                # P7 — syntax/lint warning post-patch
                result = await _append_syntax_warning(
                    result_text, resolved, workspace_root=ctx.lumena_root,
                )
            return HandlerResult.ok(result, handler_name="apply_patch")

        # Fallback: self_improve pour le code source Lumena
        from ...autonomy.self_improve import get_self_improver

        lumena_root = ctx.lumena_root
        improver = get_self_improver(lumena_root)
        success, message = improver.apply_patch(file_path, old_content, new_content, description)

        if success:
            try:
                from ...learning.instincts import get_instinct_system
                instincts = get_instinct_system()
                instincts.learn(
                    pattern=f"modification de {file_path}",
                    response=description or "patch appliqué",
                    was_successful=True,
                    category="code",
                )
            except Exception as e:
                logger.debug(f"Memorize file edit: {e}")

        return HandlerResult.ok(message, handler_name="apply_patch")
    except ImportError as e:
        return HandlerResult.ok(f"❌ Module self_improve non disponible: {e}", handler_name="apply_patch")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur apply_patch: {e}", handler_name="apply_patch")


async def apply_patch_new_handler(ctx: HandlerContext, patch_content: str) -> HandlerResult:
    """Applique un patch multi-fichiers (format unifié)."""
    if not ADVANCED_TOOLS_AVAILABLE:
        return HandlerResult.ok("❌ Module apply_patch non disponible", handler_name="apply_patch")
    try:
        lumena_root = ctx.lumena_root
        patch_hunks = _parse_patch_fn(patch_content)

        touched: Dict[str, Dict[str, Any]] = {}
        for hunk in patch_hunks:
            kind = getattr(hunk, "kind", "")
            hunk_path = getattr(hunk, "path", "")
            if not hunk_path:
                continue
            resolved = (lumena_root / hunk_path).resolve()
            # P0.2: block patches to protected zones
            if check_write_blacklist is not None:
                try:
                    check_write_blacklist(resolved, lumena_root)
                except PathSecurityError as sec_err:
                    return HandlerResult.fail(str(sec_err), handler_name="apply_patch")
            existed_before, before_content = _before_snapshot(resolved)
            touched[str(resolved)] = {
                "path": resolved,
                "kind": kind,
                "existed_before": existed_before,
                "before_content": before_content,
                "hunk_path": hunk_path,
            }

        result = await _apply_patch_fn(patch_content=patch_content, workspace_root=lumena_root)
        summary = result.summary()

        if result.success:
            for info in touched.values():
                resolved = info["path"]
                after_content = _after_snapshot(resolved)

                kind = info.get("kind")
                if kind == "add":
                    action = "created" if not info.get("existed_before") else "updated"
                elif kind == "update":
                    action = "patched"
                else:
                    action = "patched"

                _record_file_edit(
                    ctx,
                    tool_name="apply_patch",
                    action=action,
                    file_path=resolved,
                    before_content=info.get("before_content"),
                    after_content=after_content,
                    existed_before=bool(info.get("existed_before")),
                    summary=f"patched: {info.get('hunk_path')}",
                    workspace_relative=(_compute_workspace_relative(resolved, ctx.lumena_root)
                                        if _compute_workspace_relative else None),
                )

        return HandlerResult.ok(summary, handler_name="apply_patch")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur apply_patch: {e}", handler_name="apply_patch")


async def view_outline_handler(ctx: HandlerContext, path: str) -> HandlerResult:
    """Affiche la structure d'un fichier de code supporte."""
    try:
        resolved = ctx.resolve_path(path)
        if not resolved or not resolved.exists():
            return HandlerResult.ok(f"❌ Fichier non trouvé: {path}", handler_name="view_outline")
        suffix = resolved.suffix.lower()
        if suffix == ".py":
            content = resolved.read_text(encoding="utf-8")
            tree = ast.parse(content)

            lines = [f"📋 **Structure de `{resolved.name}`**\n"]

            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    methods = [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
                    lines.append(f"### 🏷️ `class {node.name}` (ligne {node.lineno})")
                    if methods:
                        lines.append(f"   Méthodes: {', '.join(methods[:10])}")
                    lines.append("")

            for node in ast.iter_child_nodes(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
                    lines.append(f"### 🔹 `{prefix}def {node.name}()` (ligne {node.lineno})")

            if len(lines) == 1:
                lines.append("Aucune classe ou fonction trouvée.")

            return HandlerResult.ok("\n".join(lines), handler_name="view_outline")

        if suffix in {".js", ".jsx", ".ts", ".tsx", ".rs", ".go"}:
            outline = parse_file_outline(str(resolved))
            return HandlerResult.ok(
                f"📋 **Structure de `{resolved.name}`**\n\n{outline}",
                handler_name="view_outline",
            )

        supported = ".py, .js, .jsx, .ts, .tsx, .rs, .go"
        if suffix not in {".py", ".js", ".jsx", ".ts", ".tsx", ".rs", ".go"}:
            return HandlerResult.ok(
                f"❌ Fichier non supporté pour view_outline ({resolved.suffix}). Extensions supportées: {supported}",
                handler_name="view_outline",
            )
        return HandlerResult.ok(f"❌ Fichier non supporté: {resolved.suffix}", handler_name="view_outline")
    except SyntaxError as e:
        return HandlerResult.ok(f"❌ Erreur de syntaxe Python: {e}", handler_name="view_outline")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur: {e}", handler_name="view_outline")

async def grep_search_handler(
    ctx: HandlerContext,
    pattern: str = "",
    path: str = ".",
    ignore_case: bool = False,
    is_regex: bool = False,
    max_results: int = 50,
) -> HandlerResult:
    """Recherche un pattern (texte ou regex) dans les fichiers."""
    import re as _re

    if not pattern:
        return HandlerResult.fail(
            "❌ grep_search: pattern vide.", handler_name="grep_search"
        )
    try:
        root = ctx.lumena_root
        target = (root / path).resolve()
        # P0.2: boundary + read blacklist check on search target
        if check_path_boundary is not None:
            try:
                check_path_boundary(target, root, ctx.file_guardrails._workspace_root())
            except PathSecurityError as sec_err:
                return HandlerResult.fail(str(sec_err), handler_name="grep_search")
        if check_read_blacklist is not None:
            try:
                check_read_blacklist(target, root)
            except PathSecurityError as sec_err:
                return HandlerResult.fail(str(sec_err), handler_name="grep_search")
        if not target.exists():
            return HandlerResult.fail(
                f"❌ Chemin non trouvé: {path}", handler_name="grep_search"
            )

        max_results = max(1, min(int(max_results), 200))
        max_file_size = 1_000_000  # 1 Mo

        flags = _re.IGNORECASE if ignore_case else 0
        if is_regex:
            try:
                regex = _re.compile(pattern, flags)
            except _re.error as e:
                return HandlerResult.fail(
                    f"❌ Regex invalide: {e}", handler_name="grep_search"
                )
        else:
            regex = _re.compile(_re.escape(pattern), flags)

        files_to_search = [target] if target.is_file() else list(target.rglob("*"))

        results = []
        for file_path in files_to_search:
            if not file_path.is_file():
                continue
            try:
                if file_path.stat().st_size > max_file_size:
                    continue
                content = file_path.read_text(encoding="utf-8", errors="ignore")
                for i, line in enumerate(content.split("\n"), 1):
                    if regex.search(line):
                        try:
                            rel = file_path.relative_to(root)
                        except ValueError:
                            rel = file_path
                        results.append(f"{rel}:{i}: {line.strip()[:100]}")
                        if len(results) >= max_results:
                            break
            except (IOError, OSError):
                continue
            if len(results) >= max_results:
                break

        if not results:
            return HandlerResult.ok(
                f"🔍 Aucun résultat pour '{pattern}' dans {path}",
                handler_name="grep_search",
            )
        header = f"🔍 {len(results)} résultat(s) pour '{pattern}':\n\n"
        return HandlerResult.ok(
            header + "\n".join(results), handler_name="grep_search"
        )
    except Exception as e:
        return HandlerResult.fail(
            f"❌ Erreur grep: {e}", handler_name="grep_search"
        )


async def undo_edit_handler(ctx: HandlerContext, file_path: str = "") -> HandlerResult:
    """Annule le dernier edit d'un fichier en le restaurant depuis le backup le plus récent.
    Sans argument, liste les backups disponibles."""
    try:
        import shutil

        from ...utils.paths import BACKUPS_DIR
        backup_root = BACKUPS_DIR
        if not backup_root.exists():
            return HandlerResult.fail("❌ Aucun backup disponible", handler_name="undo_edit")

        sessions = sorted(
            [d for d in backup_root.iterdir() if d.is_dir()],
            key=lambda d: d.stat().st_mtime,
            reverse=True,
        )
        if not sessions:
            return HandlerResult.fail("❌ Aucun backup disponible", handler_name="undo_edit")

        if not file_path:
            # Lister les backups disponibles plutôt que de tout restaurer (sécurité)
            lines = [
                f"  - {s.name} ({len([f for f in s.rglob('*') if f.is_file()])} fichiers)"
                for s in sessions[:5]
            ]
            return HandlerResult.ok(
                "📦 Backups disponibles (plus récent en premier):\n"
                + "\n".join(lines)
                + "\n\nPour restaurer un fichier: undo_edit(file_path='chemin/du/fichier')",
                handler_name="undo_edit",
            )

        latest = sessions[0]
        target = ctx.resolve_path(file_path)
        try:
            _assert_write_boundary(target, ctx)
        except PathSecurityError as sec_err:
            return HandlerResult.fail(str(sec_err), handler_name="undo_edit")

        # Trouver le fichier dans le backup (chemin relatif ou par nom)
        backup_file: Optional[Path] = None
        try:
            relative = target.relative_to(ctx.lumena_root)
            candidate = latest / relative
            if candidate.exists():
                backup_file = candidate
        except ValueError:
            pass  # pas de backup trouvée avec ce format

        if backup_file is None:
            # Fallback: chercher par nom de fichier
            candidates = list(latest.rglob(target.name))
            if not candidates:
                return HandlerResult.fail(
                    f"❌ Backup non trouvé pour '{file_path}' dans {latest.name}",
                    handler_name="undo_edit",
                )
            backup_file = candidates[0]
            # Reconstituer la cible originale depuis le chemin du backup
            target = ctx.lumena_root / backup_file.relative_to(latest)

        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup_file, target)
        return HandlerResult.ok(
            f"✅ '{target.name}' restauré depuis le backup {latest.name}",
            handler_name="undo_edit",
        )
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur undo_edit: {e}", handler_name="undo_edit")


async def create_directory_handler(
    ctx: HandlerContext,
    path: str,
    exist_ok: bool = True,
) -> HandlerResult:
    """Crée un répertoire (et les parents manquants)."""
    try:
        from pathlib import Path as _Path
        target = _Path(path)
        if not target.is_absolute():
            # Strip leading "workspace/" to avoid workspace/workspace/ doubling
            path_posix = target.as_posix()
            if path_posix.startswith("workspace/"):
                stripped = path_posix[len("workspace/"):]
                if stripped:
                    target = _Path(stripped)
            # P3 — en mission (projet épinglé), aligner create_directory sur le
            # routage de write_file (workspace/<date>/<projet>/…) pour éviter le
            # piège « dossier vide » (création à la racine pendant que les fichiers
            # vont dans le sous-dossier épinglé).
            from src.tools.file_guardrails import WorkspaceFileGuardrails as _WFG
            if _WFG._pinned_project:
                from datetime import datetime as _dt
                from src.utils.paths import WORKSPACE_DIR as _WS_DIR
                target = _WS_DIR / _dt.now().strftime("%Y-%m-%d") / _WFG._pinned_project / target
            else:
                target = ctx.runtime_root / target
        try:
            _assert_write_boundary(target, ctx)
        except PathSecurityError as sec_err:
            return HandlerResult.fail(str(sec_err), handler_name="create_directory")
        target.mkdir(parents=True, exist_ok=exist_ok)
        return HandlerResult.ok(f"✅ Répertoire créé: {target}", handler_name="create_directory")
    except FileExistsError:
        return HandlerResult.fail(f"❌ Le répertoire existe déjà: {path}", handler_name="create_directory")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur create_directory: {e}", handler_name="create_directory")


async def file_crawl_campaign_handler(
    ctx: HandlerContext,
    root_path: str,
    campaign_id: str = "",
    extensions: Optional[List[str]] = None,
    max_files: int = 5000,
    keyword_hint: str = "",
) -> HandlerResult:
    """Lance ou reprend une campagne d'indexation de fichiers locaux."""
    try:
        from ...tools.local_file_crawler import LocalFileCrawler
        from ...utils.paths import CRAWLER_DIR
        crawler = LocalFileCrawler(CRAWLER_DIR)
        result = await crawler.crawl_campaign(
            root_path=root_path,
            campaign_id=campaign_id,
            files_per_run=500,
            max_total_files=max_files,
            keyword_hint=keyword_hint,
        )
        if not result.get("success"):
            return HandlerResult.fail(f"❌ Campagne fichiers échouée: {result.get('error', 'erreur inconnue')}", handler_name="file_crawl_campaign")
        return HandlerResult.ok(
            f"📁 Campagne fichiers: {result.get('campaign_id')}\n"
            f"- run_id: {result.get('run_id')}\n"
            f"- fichiers scannés (batch): {result.get('run_scanned')}\n"
            f"- total scanné: {result.get('files_scanned_total')}/{result.get('max_total_files')}\n"
            f"- done: {result.get('done')}",
            handler_name="file_crawl_campaign",
        )
    except ImportError:
        return HandlerResult.fail("❌ file_crawl_campaign: module local_file_crawler non disponible.", handler_name="file_crawl_campaign")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur file_crawl_campaign: {e}", handler_name="file_crawl_campaign")


async def file_crawl_campaign_status_handler(
    ctx: HandlerContext,
    campaign_id: str,
) -> HandlerResult:
    """Retourne l'état d'une campagne d'indexation de fichiers."""
    try:
        from ...tools.local_file_crawler import LocalFileCrawler
        from ...utils.paths import CRAWLER_DIR
        crawler = LocalFileCrawler(CRAWLER_DIR)
        result = crawler.campaign_status(campaign_id)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ Status campagne indisponible: {result.get('error', 'erreur inconnue')}", handler_name="file_crawl_campaign_status")
        return HandlerResult.ok(
            f"📊 Campagne fichiers: {result.get('campaign_id')}\n"
            f"- fichiers scannés: {result.get('files_scanned_total')}/{result.get('max_total_files')}\n"
            f"- done: {result.get('done')}",
            handler_name="file_crawl_campaign_status",
        )
    except ImportError:
        return HandlerResult.fail("❌ file_crawl_campaign_status: module local_file_crawler non disponible.", handler_name="file_crawl_campaign_status")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur file_crawl_campaign_status: {e}", handler_name="file_crawl_campaign_status")


async def file_crawl_campaign_export_handler(
    ctx: HandlerContext,
    campaign_id: str,
    top_n: int = 100,
) -> HandlerResult:
    """Exporte les résultats d'une campagne d'indexation vers un fichier."""
    try:
        from ...tools.local_file_crawler import LocalFileCrawler
        from ...utils.paths import CRAWLER_DIR
        crawler = LocalFileCrawler(CRAWLER_DIR)
        result = crawler.campaign_export_index(campaign_id, top_n=top_n)
        if not result.get("success"):
            return HandlerResult.fail(f"❌ Export index impossible: {result.get('error', 'erreur inconnue')}", handler_name="file_crawl_campaign_export")
        return HandlerResult.ok(
            f"📚 Index fichiers exporté: {result.get('campaign_id')}\n"
            f"- count: {result.get('count')}\n"
            f"- json: {result.get('index_json')}\n"
            f"- md: {result.get('index_md')}",
            handler_name="file_crawl_campaign_export",
        )
    except ImportError:
        return HandlerResult.fail("❌ file_crawl_campaign_export: module local_file_crawler non disponible.", handler_name="file_crawl_campaign_export")
    except Exception as e:
        return HandlerResult.fail(f"❌ Erreur file_crawl_campaign_export: {e}", handler_name="file_crawl_campaign_export")


# ─── Registration ──────────────────────────────────────────────────────────

def get_file_handler_defs() -> List[HandlerDef]:
    """Retourne toutes les définitions de handlers fichiers pour le registre V2."""
    return [
        HandlerDef(
            name="read_file",
            description="Lit un fichier (pagination en lignes).",
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Chemin du fichier a lire"},
                    "start_line": {"type": "integer", "description": "Ligne de debut (optionnel)"},
                    "end_line": {"type": "integer", "description": "Ligne de fin (optionnel)"},
                },
                "required": ["path"],
            },
            handler=read_file_handler,
            category="files",
            source_module="handlers.files",
        ),
        HandlerDef(
            name="write_file",
            description="Ecrit dans un fichier (cree ou ecrase avec workspace intelligent).",
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Chemin du fichier"},
                    "content": {"type": "string", "description": "Contenu a ecrire"},
                    "project": {"type": "string", "description": "Nom du projet (optionnel)"},
                    "force_rewrite": {"type": "boolean", "description": "Forcer la reecriture si fichier existant"},
                    "rewrite_reason": {"type": "string", "description": "Raison de la reecriture"},
                },
                "required": ["path", "content"],
            },
            handler=write_file_handler,
            category="files",
            source_module="handlers.files",
        ),
        HandlerDef(
            name="edit_file",
            description="Modifie un fichier en remplacant old_content par new_content. Supporte matching flou (whitespace, unicode). Si echec, relire le fichier avec read_file et copier le contenu exact.",
            parameters={
                "properties": {
                    "file_path": {"type": "string", "description": "Chemin du fichier"},
                    "old_content": {"type": "string", "description": "Contenu a remplacer"},
                    "new_content": {"type": "string", "description": "Nouveau contenu"},
                },
                "required": ["file_path", "old_content", "new_content"],
            },
            handler=edit_file_handler,
            category="files",
            source_module="handlers.files",
        ),
        HandlerDef(
            name="multi_edit_file",
            description="Editions multiples en un seul appel. Chaque edit DOIT avoir les clés: file_path (ou file/path), old_content (ou old), new_content (ou new).",
            parameters={
                "properties": {
                    "edits": {"type": "array", "items": {"type": "object"}, "description": "Liste d'objets [{\"file_path\": \"chemin\", \"old_content\": \"ancien texte\", \"new_content\": \"nouveau texte\"}]. Chaque objet DOIT avoir file_path + old_content + new_content."},
                },
                "required": ["edits"],
            },
            handler=multi_edit_file_handler,
            category="files",
            source_module="handlers.files",
        ),
        HandlerDef(
            name="insert_at_anchor",
            description=(
                "Insère du contenu autour d'une ancre textuelle (language-agnostic). "
                "Remplace le pattern grep+read_file+str_replace en 1 seule action. "
                "Marche pour tous les langages: HTML (</main>, </body>), Python (# END IMPORTS), "
                "JS/TS (export default), Java/C# (} // end class), CSS (/* END */), etc."
            ),
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Chemin du fichier à modifier"},
                    "anchor": {"type": "string", "description": "Texte exact à localiser dans le fichier (ex: '</main>', '# END IMPORTS')"},
                    "content": {"type": "string", "description": "Contenu à insérer"},
                    "position": {"type": "string", "description": "Où insérer: 'before' (défaut) | 'after' | 'replace' (remplace l'ancre)"},
                    "occurrence": {"type": "string", "description": "Quelle occurrence: 'first' (défaut) | 'last' | N (entier 1-indexé)"},
                },
                "required": ["path", "anchor", "content"],
            },
            handler=insert_at_anchor_handler,
            category="files",
            source_module="handlers.files",
        ),
        HandlerDef(
            name="apply_patch",
            description="Applique un patch au code source.",
            parameters={
                "properties": {
                    "file_path": {"type": "string", "description": "Chemin du fichier"},
                    "old_content": {"type": "string", "description": "Contenu original"},
                    "new_content": {"type": "string", "description": "Contenu modifie"},
                    "description": {"type": "string", "description": "Description du patch"},
                },
                "required": ["file_path", "old_content", "new_content"],
            },
            handler=apply_patch_handler,
            category="files",
            source_module="handlers.files",
        ),
        HandlerDef(
            name="list_directory",
            description="Liste les fichiers et dossiers d'un repertoire.",
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Chemin du repertoire (defaut: '.')"},
                },
                "required": [],
            },
            handler=list_directory_handler,
            category="files",
            source_module="handlers.files",
        ),
        HandlerDef(
            name="find_files",
            description="Recherche recursive de fichiers par pattern.",
            parameters={
                "properties": {
                    "pattern": {"type": "string", "description": "Pattern de recherche (nom ou glob)"},
                    "path": {"type": "string", "description": "Repertoire de recherche (defaut: workspace)"},
                },
                "required": ["pattern"],
            },
            handler=find_files_handler,
            category="files",
            source_module="handlers.files",
        ),
        HandlerDef(
            name="delete_file",
            description="Supprime un fichier.",
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Chemin du fichier a supprimer"},
                },
                "required": ["path"],
            },
            handler=delete_file_handler,
            category="files",
            source_module="handlers.files",
        ),
        HandlerDef(
            name="create_zip",
            description="Cree un fichier ZIP.",
            parameters={
                "properties": {
                    "source_paths": {"type": "string", "description": "Chemins sources (separes par virgule)"},
                    "zip_path": {"type": "string", "description": "Chemin du ZIP (optionnel)"},
                    "overwrite": {"type": "boolean", "description": "Ecraser si existant (defaut: true)"},
                },
                "required": ["source_paths"],
            },
            handler=create_zip_handler,
            category="files",
            source_module="handlers.files",
        ),
        HandlerDef(
            name="open_file",
            description="Ouvre un fichier dans l'application par defaut.",
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Chemin du fichier a ouvrir"},
                },
                "required": ["path"],
            },
            handler=open_file_handler,
            category="files",
            source_module="handlers.files",
        ),
        HandlerDef(
            name="view_outline",
            description="Affiche la structure d'un fichier de code (Python, JS/TS, Rust, Go).",
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Chemin du fichier code"},
                },
                "required": ["path"],
            },
            handler=view_outline_handler,
            category="files",
            source_module="handlers.files",
        ),
        HandlerDef(
            name="view_file_outline",
            description="Alias de view_outline. Affiche la structure d'un fichier de code (Python, JS/TS, Rust, Go).",
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Chemin du fichier code"},
                },
                "required": ["path"],
            },
            handler=view_outline_handler,
            category="files",
            source_module="handlers.files",
        ),
        HandlerDef(
            name="grep_search",
            description="Recherche un pattern (texte ou regex) dans les fichiers. Plus puissant que search_in_code: chemin arbitraire, regex, récursif.",
            parameters={
                "properties": {
                    "pattern": {"type": "string", "description": "Texte ou regex à rechercher"},
                    "path": {"type": "string", "description": "Chemin fichier ou dossier (défaut: racine)", "default": "."},
                    "ignore_case": {"type": "boolean", "description": "Ignorer la casse", "default": False},
                    "is_regex": {"type": "boolean", "description": "Interpréter comme regex Python", "default": False},
                    "max_results": {"type": "integer", "description": "Nombre max de résultats", "default": 50},
                },
                "required": ["pattern"],
            },
            handler=grep_search_handler,
            category="files",
            source_module="handlers.files",
        ),
        HandlerDef(
            name="undo_edit",
            description="Restaure un fichier depuis le backup le plus récent (annule le dernier edit). Sans argument, liste les backups disponibles.",
            parameters={
                "properties": {
                    "file_path": {"type": "string", "description": "Chemin du fichier à restaurer (laisser vide pour lister les backups)"},
                },
                "required": [],
            },
            handler=undo_edit_handler,
            category="files",
            source_module="handlers.files",
        ),
        HandlerDef(
            name="create_directory",
            description="Crée un répertoire (et tous les parents manquants si nécessaire).",
            parameters={
                "properties": {
                    "path": {"type": "string", "description": "Chemin absolu ou relatif du répertoire à créer"},
                    "exist_ok": {"type": "boolean", "description": "Ne pas lever d'erreur si le répertoire existe déjà", "default": True},
                },
                "required": ["path"],
            },
            handler=create_directory_handler,
            category="files",
            source_module="handlers.files",
        ),
        HandlerDef(
            name="file_crawl_campaign",
            description="Lance ou reprend une campagne d'indexation de fichiers locaux (exploration récursive d'un répertoire).",
            parameters={
                "properties": {
                    "root_path": {"type": "string", "description": "Répertoire racine à indexer"},
                    "campaign_id": {"type": "string", "description": "ID campagne existante (vide = nouveau)", "default": ""},
                    "extensions": {"type": "array", "items": {"type": "string"}, "description": "Extensions à inclure ex: ['.py', '.md'] (vide = toutes)", "default": []},
                    "max_files": {"type": "integer", "description": "Nombre max de fichiers à indexer", "default": 5000},
                    "keyword_hint": {"type": "string", "description": "Mots-clés pour filtrer les fichiers pertinents", "default": ""},
                },
                "required": ["root_path"],
            },
            handler=file_crawl_campaign_handler,
            category="files",
            source_module="handlers.files",
        ),
        HandlerDef(
            name="file_crawl_campaign_status",
            description="Retourne l'état d'une campagne d'indexation de fichiers locaux (progression, statistiques).",
            parameters={
                "properties": {
                    "campaign_id": {"type": "string", "description": "ID de la campagne"},
                },
                "required": ["campaign_id"],
            },
            handler=file_crawl_campaign_status_handler,
            category="files",
            source_module="handlers.files",
        ),
        HandlerDef(
            name="file_crawl_campaign_export",
            description="Exporte les résultats d'une campagne d'indexation de fichiers dans un fichier CSV/JSON.",
            parameters={
                "properties": {
                    "campaign_id": {"type": "string", "description": "ID de la campagne à exporter"},
                    "top_n": {"type": "integer", "description": "Nombre de fichiers les plus pertinents à exporter", "default": 100},
                },
                "required": ["campaign_id"],
            },
            handler=file_crawl_campaign_export_handler,
            category="files",
            source_module="handlers.files",
        ),
    ]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under AGPL-3.0 (open source) or a Commercial License (proprietary use)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
