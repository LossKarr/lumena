"""
P8 — CodeFileWatcher : bridge IDE → CodeAgent.

Surveille le workspace d'une session CodeAgent active.
Quand un fichier source est modifié depuis l'IDE (en dehors du CodeAgent),
invalide le cache du CodeIndex pour ce workspace afin que les prochaines
recherches RAG soient fraîches.

Activé si LUMENA_FILE_WATCHER_BRIDGE=1 (opt-IN, désactivé par défaut).
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from loguru import logger

_CODE_EXTENSIONS = {".py", ".js", ".ts", ".jsx", ".tsx", ".vue", ".css", ".html"}

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False


class _CodeChangeHandler(FileSystemEventHandler if WATCHDOG_AVAILABLE else object):
    """Handler watchdog qui invalide le cache CodeIndex sur changement."""

    def __init__(self, workspace: Path, on_change: Callable[[Path], None]) -> None:
        if WATCHDOG_AVAILABLE:
            super().__init__()
        self.workspace = workspace
        self.on_change = on_change
        self._ignore_dirs = {"node_modules", ".git", "__pycache__", ".venv", ".backups", "dist", "build"}

    def _is_relevant(self, src_path: str) -> bool:
        p = Path(src_path)
        if p.suffix.lower() not in _CODE_EXTENSIONS:
            return False
        return not any(part in self._ignore_dirs for part in p.parts)

    def on_modified(self, event) -> None:
        if not event.is_directory and self._is_relevant(event.src_path):
            self.on_change(Path(event.src_path))

    def on_created(self, event) -> None:
        if not event.is_directory and self._is_relevant(event.src_path):
            self.on_change(Path(event.src_path))


class CodeFileWatcher:
    """
    Surveille un workspace et invalide les caches contextuels sur changement.

    Usage :
        watcher = CodeFileWatcher(workspace)
        watcher.start()
        # ... session CodeAgent ...
        watcher.stop()
    """

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace).resolve()
        self._observer: Optional["Observer"] = None
        self._running = False

    def start(self) -> bool:
        if not WATCHDOG_AVAILABLE:
            logger.debug("[CodeFileWatcher] watchdog non disponible — bridge IDE désactivé")
            return False
        if self._running:
            return True

        try:
            handler = _CodeChangeHandler(self.workspace, self._on_file_changed)
            self._observer = Observer()
            self._observer.schedule(handler, str(self.workspace), recursive=True)
            self._observer.start()
            self._running = True
            logger.debug("[CodeFileWatcher] démarré sur {}", self.workspace)
            return True
        except Exception as exc:
            logger.warning("[CodeFileWatcher] échec démarrage : {}", exc)
            return False

    def stop(self) -> None:
        if not self._running or self._observer is None:
            return
        try:
            self._observer.stop()
            self._observer.join(timeout=2.0)
        except Exception:
            pass
        self._running = False
        logger.debug("[CodeFileWatcher] arrêté pour {}", self.workspace)

    def _on_file_changed(self, path: Path) -> None:
        """Invalide les caches dépendants du workspace."""
        logger.debug("[CodeFileWatcher] changement détecté : {}", path.name)
        try:
            from src.context.code_index import clear_code_index_cache
            clear_code_index_cache(self.workspace)
        except Exception:
            pass
        try:
            from src.context.repo_map import clear_repo_map_cache
            clear_repo_map_cache(self.workspace)
        except Exception:
            pass

    @property
    def is_running(self) -> bool:
        return self._running


# Registre global des watchers actifs (workspace_key → watcher)
_active_watchers: dict[str, CodeFileWatcher] = {}


def start_code_watcher(workspace: Path) -> Optional[CodeFileWatcher]:
    """Démarre un watcher pour ce workspace (idempotent)."""
    key = str(Path(workspace).resolve())
    if key in _active_watchers and _active_watchers[key].is_running:
        return _active_watchers[key]
    watcher = CodeFileWatcher(workspace)
    if watcher.start():
        _active_watchers[key] = watcher
        return watcher
    return None


def stop_code_watcher(workspace: Path) -> None:
    """Arrête le watcher pour ce workspace."""
    key = str(Path(workspace).resolve())
    w = _active_watchers.pop(key, None)
    if w:
        w.stop()


__all__ = ["CodeFileWatcher", "start_code_watcher", "stop_code_watcher"]
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU Affero General Public License v3.0 (AGPL-3.0)
# ──────────────────────────────────────────────────────────────────────────────
