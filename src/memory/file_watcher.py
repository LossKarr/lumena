"""
🌟 LUMENA - File Watcher pour Mémoire

Surveille les fichiers mémoire (MEMORY.md, data/memory/, etc.)
et re-indexe automatiquement quand ils changent.

Inspiré de Moltbot file watching patterns
"""

from typing import List, Callable, Optional, Set
from pathlib import Path
import asyncio
import time
from loguru import logger

try:
    from watchdog.observers import Observer
    from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent
    WATCHDOG_AVAILABLE = True
except ImportError:
    WATCHDOG_AVAILABLE = False
    logger.warning("watchdog non installé. Installez avec: pip install watchdog")


class MemoryFileHandler(FileSystemEventHandler):
    """Handler pour les événements de fichiers."""
    
    def __init__(self, callback: Callable[[Path], None], extensions: Set[str]):
        super().__init__()
        self.callback = callback
        self.extensions = extensions
        self._last_events: dict = {}  # Debouncing
        self._debounce_seconds = 1.0
    
    def _should_process(self, path: Path) -> bool:
        """Vérifie si le fichier doit être traité."""
        # Vérifier l'extension
        if self.extensions and path.suffix.lower() not in self.extensions:
            return False
        
        # Ignorer les fichiers système et temporaires
        if path.name.startswith('.') or path.name.startswith('~'):
            return False
        if path.suffix in ['.tmp', '.temp', '.swp', '.bak']:
            return False
        
        # Debouncing: ignorer les événements trop rapprochés
        now = time.time()
        last_time = self._last_events.get(str(path), 0)
        if now - last_time < self._debounce_seconds:
            return False
        
        self._last_events[str(path)] = now
        return True
    
    def on_modified(self, event):
        """Appelé quand un fichier est modifié."""
        if event.is_directory:
            return
        
        path = Path(event.src_path)
        if self._should_process(path):
            logger.debug(f"Fichier modifié détecté: {path.name}")
            self.callback(path)
    
    def on_created(self, event):
        """Appelé quand un fichier est créé."""
        if event.is_directory:
            return
        
        path = Path(event.src_path)
        if self._should_process(path):
            logger.debug(f"Nouveau fichier détecté: {path.name}")
            self.callback(path)


class MemoryFileWatcher:
    """
    Surveille les fichiers mémoire et déclenche des callbacks.
    
    Utile pour :
    - Re-indexer automatiquement quand MEMORY.md change
    - Synchroniser les fichiers du dossier data/memory/
    - Surveiller les fichiers de configuration
    """
    
    def __init__(
        self,
        watch_paths: List[Path],
        on_change: Callable[[Path], None],
        extensions: Optional[Set[str]] = None
    ):
        """
        Initialise le file watcher.
        
        Args:
            watch_paths: Chemins à surveiller (fichiers ou dossiers)
            on_change: Callback appelé quand un fichier change
            extensions: Extensions à surveiller (ex: {'.md', '.txt', '.json'})
        """
        self.watch_paths = watch_paths
        self.on_change = on_change
        self.extensions = extensions or {'.md', '.txt', '.json'}
        self.observer: Optional[Observer] = None
        self._running = False
    
    def start(self):
        """Démarre la surveillance des fichiers."""
        if not WATCHDOG_AVAILABLE:
            logger.warning("MemoryFileWatcher: watchdog non disponible")
            return
        
        if self._running:
            return
        
        try:
            self.observer = Observer()
            handler = MemoryFileHandler(self.on_change, self.extensions)
            
            for path in self.watch_paths:
                if path.exists():
                    if path.is_dir():
                        self.observer.schedule(handler, str(path), recursive=True)
                        logger.info(f"Surveillance dossier: {path}")
                    else:
                        # Pour les fichiers, surveiller le dossier parent
                        self.observer.schedule(handler, str(path.parent), recursive=False)
                        logger.info(f"Surveillance fichier: {path.name}")
                else:
                    logger.warning(f"Chemin non trouvé pour surveillance: {path}")
            
            self.observer.start()
            self._running = True
            logger.info(f"MemoryFileWatcher démarré ({len(self.watch_paths)} chemins)")
            
        except Exception as e:
            logger.error(f"Erreur démarrage MemoryFileWatcher: {e}")
    
    def stop(self):
        """Arrête la surveillance."""
        if not self._running or not self.observer:
            return
        
        try:
            self.observer.stop()
            self.observer.join(timeout=2.0)
            self._running = False
            logger.info("MemoryFileWatcher arrêté")
        except Exception as e:
            logger.error(f"Erreur arrêt MemoryFileWatcher: {e}")
    
    @property
    def is_running(self) -> bool:
        """Retourne True si le watcher est actif."""
        return self._running


class AsyncMemoryFileWatcher:
    """
    Version async du file watcher.
    
    Intègre les callbacks avec une queue asyncio.
    """
    
    def __init__(
        self,
        watch_paths: List[Path],
        extensions: Optional[Set[str]] = None
    ):
        self.watch_paths = watch_paths
        self.extensions = extensions or {'.md', '.txt', '.json'}
        self._queue: asyncio.Queue = None
        self._watcher: Optional[MemoryFileWatcher] = None
        self._task: Optional[asyncio.Task] = None
    
    async def start(self, callback: Callable[[Path], None]):
        """
        Démarre la surveillance async.
        
        Args:
            callback: Fonction async appelée quand un fichier change
        """
        self._queue = asyncio.Queue()
        _captured_loop = asyncio.get_running_loop()

        def sync_callback(path: Path):
            # Ajouter à la queue de façon thread-safe
            try:
                _captured_loop.call_soon_threadsafe(
                    self._queue.put_nowait, path
                )
            except Exception:
                pass  # queue put best-effort
        
        self._watcher = MemoryFileWatcher(
            self.watch_paths,
            sync_callback,
            self.extensions
        )
        self._watcher.start()
        
        # Tâche de traitement de la queue
        async def process_queue():
            while True:
                try:
                    path = await self._queue.get()
                    await callback(path)
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"Erreur traitement fichier: {e}")
        
        self._task = asyncio.create_task(process_queue())
    
    async def stop(self):
        """Arrête la surveillance async."""
        if self._watcher:
            self._watcher.stop()
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


# Helper function
def create_memory_watcher(
    lumena_root: Path,
    on_change: Callable[[Path], None]
) -> MemoryFileWatcher:
    """
    Crée un watcher configuré pour les fichiers mémoire de Lumena.
    
    Args:
        lumena_root: Racine du projet Lumena
        on_change: Callback quand un fichier change
    """
    from src.utils.paths import MEMORY_MD, MEMORY_DIR, VECTOR_DIR
    watch_paths = [
        MEMORY_MD,
        MEMORY_DIR,
        VECTOR_DIR,
    ]
    
    return MemoryFileWatcher(
        watch_paths=[p for p in watch_paths if p.exists() or p.parent.exists()],
        on_change=on_change,
        extensions={'.md', '.txt', '.json'}
    )


# ═══════════════════════════════════════════════════════════════
# CodeFileWatcher — surveille le code source et réindexe
# ═══════════════════════════════════════════════════════════════

_CODE_EXTENSIONS = {'.py', '.js', '.ts', '.jsx', '.tsx', '.css', '.html', '.json', '.yaml', '.yml'}
_IGNORE_DIRS = {'node_modules', '__pycache__', 'venv', '.venv', 'dist', 'build', '.git', 'backups', 'data'}


class CodeFileWatcher:
    """
    Surveille les fichiers code source et met à jour le CodeIndex en temps réel.

    Utilise watchdog pour détecter les modifications de fichiers .py, .js, .ts, etc.
    et re-chunk + réindexe uniquement les fichiers modifiés (incrémental).
    """

    def __init__(self, project_root: Path):
        self.project_root = Path(project_root).resolve()
        self._watcher: Optional[MemoryFileWatcher] = None
        self._pending: set = set()
        self._debounce_task: Optional[asyncio.Task] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _on_file_changed(self, path: Path) -> None:
        """Callback synchrone depuis watchdog — accumule les changements."""
        rel = path.relative_to(self.project_root) if path.is_relative_to(self.project_root) else path
        # Ignorer les dossiers exclus
        if any(part in _IGNORE_DIRS for part in rel.parts):
            return
        self._pending.add(str(path))
        # Planifier le flush debounced via l'event loop
        if self._loop:
            try:
                self._loop.call_soon_threadsafe(self._schedule_flush)
            except RuntimeError:
                pass  # event loop fermé

    def _schedule_flush(self) -> None:
        """Planifie un flush debounced (2s après le dernier changement)."""
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
        self._debounce_task = asyncio.ensure_future(self._flush_after_delay())

    async def _flush_after_delay(self) -> None:
        """Attend 2s puis réindexe les fichiers modifiés."""
        await asyncio.sleep(2.0)
        if not self._pending:
            return

        files = list(self._pending)
        self._pending.clear()

        try:
            from ..context.code_index import get_code_index
            index = get_code_index(self.project_root)
            if not index.collection:
                return

            from ..context.code_chunker import get_code_chunker
            chunker = get_code_chunker(self.project_root)

            for file_path in files:
                try:
                    p = Path(file_path)
                    if not p.exists() or p.suffix.lower() not in _CODE_EXTENSIONS:
                        continue

                    # Supprimer les anciens chunks de ce fichier
                    rel_path = str(p.relative_to(self.project_root))
                    existing = index.collection.get(
                        where={"file_path": rel_path},
                        include=[],
                    )
                    if existing and existing["ids"]:
                        index.collection.delete(ids=existing["ids"])

                    # Re-chunker et réindexer
                    chunks = chunker.chunk_file(p)
                    if chunks:
                        ids = [c.id for c in chunks]
                        docs = [f"{c.symbol_type}: {c.symbol_name or 'module'}\nFile: {c.file_path}\n{c.content}" for c in chunks]
                        metas = [{
                            "file_path": c.file_path,
                            "symbol_name": c.symbol_name or "",
                            "symbol_type": c.symbol_type,
                            "line_start": c.line_start,
                            "line_end": c.line_end,
                            "language": c.language,
                        } for c in chunks]
                        index.collection.add(ids=ids, documents=docs, metadatas=metas)

                    logger.debug(f"CodeFileWatcher: réindexé {rel_path} ({len(chunks) if chunks else 0} chunks)")
                except Exception as e:
                    logger.warning(f"CodeFileWatcher: erreur réindexation {file_path}: {e}")

        except Exception as e:
            logger.warning(f"CodeFileWatcher: erreur flush: {e}")

    def start(self) -> None:
        """Démarre la surveillance du code source."""
        if not WATCHDOG_AVAILABLE:
            logger.warning("CodeFileWatcher: watchdog non disponible — pip install watchdog")
            return

        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            self._loop = None  # pas d'event loop

        watch_paths = [self.project_root / "src"]
        # Ajouter d'autres dossiers de code s'ils existent
        for extra in ["scripts", "skills", "tests", "web"]:
            p = self.project_root / extra
            if p.is_dir():
                watch_paths.append(p)

        self._watcher = MemoryFileWatcher(
            watch_paths=[p for p in watch_paths if p.exists()],
            on_change=self._on_file_changed,
            extensions=_CODE_EXTENSIONS,
        )
        self._watcher.start()
        logger.info(f"CodeFileWatcher démarré ({len(watch_paths)} dossiers)")

    def stop(self) -> None:
        """Arrête la surveillance."""
        if self._watcher:
            self._watcher.stop()
        if self._debounce_task and not self._debounce_task.done():
            self._debounce_task.cancel()
        logger.info("CodeFileWatcher arrêté")

    @property
    def is_running(self) -> bool:
        return self._watcher is not None and self._watcher.is_running
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
