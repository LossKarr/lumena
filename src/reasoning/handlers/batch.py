"""
batch.py — Outils batch + cache pour parallélisation industrielle.

Fournit 3 handlers V2 natifs qui compressent N itérations ReAct en 1 :

- read_files_batch(paths=[...])        → lit plusieurs fichiers en parallèle
- grep_batch(patterns=[...], paths=[]) → grep multi-pattern multi-fichier
- apply_patches(patches=[{...}])       → applique N edits atomiquement + rollback

Intègre un cache LRU de lecture de fichiers (TTL 60s, invalidé sur write)
exposé via le singleton `FileReadCache.get()`.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from threading import RLock

from loguru import logger

from .context import HandlerContext
from .contracts import HandlerResult
from .registry_v2 import HandlerDef


# ─── Cache de lecture fichiers (Levier 5) ──────────────────────────────────


class FileReadCache:
    """Cache LRU thread-safe pour lectures de fichiers (TTL + mtime invalidation).

    Invalidation :
    - TTL absolu (défaut 60s)
    - mtime change → miss forcé
    - `invalidate(path)` → appelé depuis write_file/edit_file/apply_patch
    """

    _instance: Optional["FileReadCache"] = None
    _lock = RLock()

    def __init__(self, max_entries: int = 128, ttl_seconds: float = 60.0) -> None:
        self._store: Dict[str, Tuple[str, float, float]] = {}  # path → (content, mtime, ts)
        self._max = max_entries
        self._ttl = ttl_seconds
        self._rlock = RLock()
        self.hits = 0
        self.misses = 0

    @classmethod
    def get(cls) -> "FileReadCache":
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    def _key(self, path: Path) -> str:
        try:
            return str(path.resolve())
        except Exception:
            return str(path)

    def read(self, path: Path) -> Optional[str]:
        """Retourne le contenu si cache valide, sinon None."""
        if not path.exists() or not path.is_file():
            return None
        key = self._key(path)
        try:
            current_mtime = path.stat().st_mtime
        except OSError:
            return None
        with self._rlock:
            entry = self._store.get(key)
            if entry is None:
                self.misses += 1
                return None
            content, cached_mtime, ts = entry
            if current_mtime != cached_mtime or (time.time() - ts) > self._ttl:
                # Invalidation implicite
                self._store.pop(key, None)
                self.misses += 1
                return None
            # LRU touch : réinsère en fin
            self._store.pop(key)
            self._store[key] = entry
            self.hits += 1
            return content

    def store(self, path: Path, content: str) -> None:
        """Stocke le contenu avec son mtime actuel."""
        if not path.exists() or not path.is_file():
            return
        key = self._key(path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            return
        with self._rlock:
            self._store[key] = (content, mtime, time.time())
            # Éviction LRU
            while len(self._store) > self._max:
                oldest = next(iter(self._store))
                self._store.pop(oldest, None)

    def invalidate(self, path: Path) -> None:
        """Invalide une entrée (à appeler après write/edit/delete)."""
        key = self._key(path)
        with self._rlock:
            self._store.pop(key, None)

    def clear(self) -> None:
        with self._rlock:
            self._store.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> Dict[str, Any]:
        with self._rlock:
            total = self.hits + self.misses
            return {
                "entries": len(self._store),
                "max": self._max,
                "ttl_seconds": self._ttl,
                "hits": self.hits,
                "misses": self.misses,
                "hit_rate": (self.hits / total) if total else 0.0,
            }


def _read_text_cached(path: Path) -> str:
    """Lecture de fichier avec cache transparent. Retourne "" si erreur."""
    cache = FileReadCache.get()
    cached = cache.read(path)
    if cached is not None:
        return cached
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.debug("[batch] read failed {}: {}", path, exc)
        return ""
    cache.store(path, content)
    return content


def invalidate_file_cache(path: Path) -> None:
    """API publique : à appeler depuis les handlers qui modifient un fichier."""
    try:
        FileReadCache.get().invalidate(path)
    except Exception:
        pass


# ─── Handler: read_files_batch (Levier 4) ──────────────────────────────────


async def read_files_batch_handler(
    ctx: HandlerContext,
    paths: Any = None,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
    max_chars_per_file: int = 6000,
) -> HandlerResult:
    """Lit plusieurs fichiers en parallèle (via asyncio.to_thread + cache).

    Args:
        paths: liste de chemins (ou string CSV).
        start_line, end_line: range commune appliquée à tous (optionnel).
        max_chars_per_file: tronque chaque fichier à N chars pour bornage prompt.
    """
    # Normalisation entrée
    if isinstance(paths, str):
        paths = [p.strip() for p in re.split(r"[,\n]", paths) if p.strip()]
    if not isinstance(paths, list) or not paths:
        return HandlerResult.fail(
            "read_files_batch: paths doit être une liste non vide (ou string CSV).",
            handler_name="read_files_batch",
        )
    if len(paths) > 20:
        return HandlerResult.fail(
            f"read_files_batch: max 20 fichiers par batch (reçu {len(paths)}).",
            handler_name="read_files_batch",
        )

    async def _one(p: str) -> Tuple[str, str, bool]:
        try:
            resolved = ctx.resolve_path(p)
        except Exception as exc:
            return (p, f"❌ Résolution chemin échouée: {exc}", False)
        if not resolved.exists() or not resolved.is_file():
            return (p, f"❌ Fichier non trouvé: {p}", False)
        content = await asyncio.to_thread(_read_text_cached, resolved)
        if not content:
            return (p, "(fichier vide ou illisible)", True)

        lines = content.splitlines()
        total = len(lines)
        # Normalise start_line/end_line : accepte int, str numérique, ou liste (prend premier élément)
        def _to_int_or_none(v: Any) -> Optional[int]:
            if v is None:
                return None
            if isinstance(v, list):
                v = v[0] if v else None
                if v is None:
                    return None
            try:
                return int(v)
            except (ValueError, TypeError):
                return None
        _sl = _to_int_or_none(start_line)
        _el = _to_int_or_none(end_line)
        s = max(1, _sl) if _sl is not None else 1
        e = min(total, _el) if _el is not None else total
        if s > total:
            s = total
        if e < s:
            e = s
        body = "\n".join(lines[s - 1:e])
        if len(body) > max_chars_per_file:
            body = body[:max_chars_per_file] + f"\n[...tronqué à {max_chars_per_file} chars]"
        header = f"📄 {p} (lignes {s}-{e}/{total})"
        return (p, f"{header}\n{body}", True)

    results = await asyncio.gather(*[_one(p) for p in paths], return_exceptions=False)

    # Assemblage
    parts: List[str] = []
    ok_count = 0
    for (p, text, ok) in results:
        parts.append(f"=== {p} ===\n{text}")
        if ok:
            ok_count += 1
    summary = f"read_files_batch: {ok_count}/{len(paths)} lu(s)"
    stats = FileReadCache.get().stats()
    summary += f" | cache hit_rate={stats['hit_rate']:.0%} ({stats['hits']}h/{stats['misses']}m)"
    return HandlerResult.ok(f"{summary}\n\n" + "\n\n".join(parts), handler_name="read_files_batch")


# ─── Handler: grep_batch (Levier 4) ────────────────────────────────────────


async def grep_batch_handler(
    ctx: HandlerContext,
    patterns: Any = None,
    paths: Any = None,
    max_results_per_pattern: int = 50,
    case_insensitive: bool = True,
) -> HandlerResult:
    """Grep multi-pattern multi-fichier, un seul appel.

    Args:
        patterns: liste de regex (ou string CSV, ou un seul pattern).
        paths: liste de fichiers OU un répertoire (rglob *.* si dir).
        max_results_per_pattern: borne par pattern pour éviter explosion.
    """
    # Normalisation patterns
    if isinstance(patterns, str):
        patterns = [patterns]
    if not isinstance(patterns, list) or not patterns:
        return HandlerResult.fail(
            "grep_batch: patterns doit être une liste non vide.",
            handler_name="grep_batch",
        )
    patterns = [str(p) for p in patterns if str(p).strip()]
    if len(patterns) > 10:
        return HandlerResult.fail(
            f"grep_batch: max 10 patterns par batch (reçu {len(patterns)}).",
            handler_name="grep_batch",
        )

    # Normalisation paths
    if isinstance(paths, str):
        paths = [p.strip() for p in re.split(r"[,\n]", paths) if p.strip()]
    if not isinstance(paths, list) or not paths:
        return HandlerResult.fail(
            "grep_batch: paths requis (liste de fichiers ou dossier).",
            handler_name="grep_batch",
        )

    # Compile regex
    flags = re.IGNORECASE if case_insensitive else 0
    compiled: List[Tuple[str, re.Pattern]] = []
    for p in patterns:
        try:
            compiled.append((p, re.compile(p, flags)))
        except re.error as exc:
            return HandlerResult.fail(
                f"grep_batch: regex invalide '{p}': {exc}",
                handler_name="grep_batch",
            )

    # Expansion paths → fichiers
    files: List[Path] = []
    for raw in paths:
        try:
            resolved = ctx.resolve_path(raw, want_dir=False)
        except Exception:
            continue
        if resolved.is_dir():
            # Limite à fichiers texte communs
            for ext in ("*.py", "*.html", "*.css", "*.js", "*.ts", "*.tsx", "*.jsx",
                        "*.md", "*.txt", "*.json", "*.yaml", "*.yml", "*.toml"):
                for f in resolved.rglob(ext):
                    if f.is_file():
                        files.append(f)
                        if len(files) >= 200:
                            break
                if len(files) >= 200:
                    break
        elif resolved.is_file():
            files.append(resolved)

    if not files:
        return HandlerResult.ok("grep_batch: aucun fichier à scanner.", handler_name="grep_batch")
    if len(files) > 200:
        files = files[:200]

    # Exécution parallèle (bornée par le GIL mais to_thread aide pour I/O)
    async def _scan_one(fp: Path) -> List[Tuple[str, str, int, str]]:
        """Retourne [(pattern, file, lineno, line_text), ...]."""
        content = await asyncio.to_thread(_read_text_cached, fp)
        if not content:
            return []
        hits: List[Tuple[str, str, int, str]] = []
        lines = content.splitlines()
        counts: Dict[str, int] = {p: 0 for p, _ in compiled}
        for lineno, line in enumerate(lines, start=1):
            for patt_str, patt_re in compiled:
                if counts[patt_str] >= max_results_per_pattern:
                    continue
                if patt_re.search(line):
                    hits.append((patt_str, str(fp), lineno, line[:300]))
                    counts[patt_str] += 1
        return hits

    all_hits = await asyncio.gather(*[_scan_one(f) for f in files])

    # Groupage par pattern
    by_pattern: Dict[str, List[Tuple[str, int, str]]] = {p: [] for p, _ in compiled}
    for file_hits in all_hits:
        for (patt, fpath, lineno, text) in file_hits:
            if len(by_pattern[patt]) < max_results_per_pattern:
                by_pattern[patt].append((fpath, lineno, text))

    # Formatage
    out_lines = [f"grep_batch: {len(files)} fichier(s) scanné(s), {len(patterns)} pattern(s)"]
    for patt in patterns:
        matches = by_pattern.get(patt, [])
        out_lines.append(f"\n── Pattern `{patt}` ({len(matches)} match(s)) ──")
        if not matches:
            out_lines.append("  (aucun)")
            continue
        for (fpath, lineno, text) in matches[:max_results_per_pattern]:
            # Path relatif si possible
            try:
                rel = Path(fpath).relative_to(ctx.runtime_root).as_posix()
            except Exception:
                rel = fpath
            out_lines.append(f"  {rel}:{lineno}: {text}")
    return HandlerResult.ok("\n".join(out_lines), handler_name="grep_batch")


# ─── Handler: apply_patches (Levier 4) ─────────────────────────────────────


async def apply_patches_handler(
    ctx: HandlerContext,
    patches: Any = None,
) -> HandlerResult:
    """Applique N patches atomiquement avec rollback sur échec.

    Chaque patch = {file, old, new} ou {file_path, old_content, new_content}.
    Si UN patch échoue (old introuvable, fichier non trouvé) → rollback total.
    """
    if not isinstance(patches, list) or not patches:
        return HandlerResult.fail(
            "apply_patches: patches doit être une liste non vide "
            "[{file, old, new}, ...].",
            handler_name="apply_patches",
        )
    if len(patches) > 50:
        return HandlerResult.fail(
            f"apply_patches: max 50 patches par batch (reçu {len(patches)}).",
            handler_name="apply_patches",
        )

    # Normalisation + validation
    norm: List[Dict[str, str]] = []
    for i, p in enumerate(patches):
        if not isinstance(p, dict):
            return HandlerResult.fail(
                f"apply_patches: patch #{i} n'est pas un objet.",
                handler_name="apply_patches",
            )
        fp = p.get("file") or p.get("file_path") or p.get("path")
        old = p.get("old") if "old" in p else p.get("old_content", "")
        new = p.get("new") if "new" in p else p.get("new_content", "")
        if not fp:
            return HandlerResult.fail(
                f"apply_patches: patch #{i} sans `file`.",
                handler_name="apply_patches",
            )
        if old is None or new is None:
            return HandlerResult.fail(
                f"apply_patches: patch #{i} sans `old`/`new`.",
                handler_name="apply_patches",
            )
        norm.append({"file": str(fp), "old": str(old), "new": str(new)})

    # Phase 1 : snapshot + validation (pas d'écriture)
    snapshots: Dict[str, Optional[str]] = {}  # path_str → content_before (None si créé)
    resolved_map: Dict[str, Path] = {}
    per_file_patches: Dict[str, List[Dict[str, str]]] = {}
    for p in norm:
        try:
            resolved = ctx.resolve_path(p["file"])
        except Exception as exc:
            return HandlerResult.fail(
                f"apply_patches: résolution `{p['file']}` échouée: {exc}",
                handler_name="apply_patches",
            )
        key = str(resolved)
        resolved_map[p["file"]] = resolved
        if key not in snapshots:
            snapshots[key] = (
                resolved.read_text(encoding="utf-8", errors="replace")
                if resolved.exists() and resolved.is_file()
                else None
            )
        per_file_patches.setdefault(key, []).append(p)

    # Vérification : toutes les strings `old` existent dans le fichier ?
    for key, group in per_file_patches.items():
        before = snapshots.get(key)
        if before is None:
            # Fichier absent → seul old="" (création) est valide
            for p in group:
                if p["old"]:
                    return HandlerResult.fail(
                        f"apply_patches: fichier absent `{p['file']}` mais `old` non vide.",
                        handler_name="apply_patches",
                    )
        else:
            # Simulation séquentielle de la chaîne de replace pour vérifier la faisabilité
            simulated = before
            for idx, p in enumerate(group):
                if p["old"] and p["old"] not in simulated:
                    return HandlerResult.fail(
                        f"apply_patches: patch #{idx} sur `{p['file']}` — `old` introuvable "
                        f"(extrait: {p['old'][:80]!r}).",
                        handler_name="apply_patches",
                    )
                if p["old"] and simulated.count(p["old"]) > 1:
                    return HandlerResult.fail(
                        f"apply_patches: patch #{idx} sur `{p['file']}` — `old` ambigu "
                        f"({simulated.count(p['old'])} occurrences). Ajoute du contexte.",
                        handler_name="apply_patches",
                    )
                simulated = simulated.replace(p["old"], p["new"], 1) if p["old"] else p["new"]

    # Phase 2 : écriture atomique (tout ou rien via rollback)
    written: List[Path] = []
    try:
        for key, group in per_file_patches.items():
            resolved = Path(key)
            before = snapshots.get(key)
            final = before if before is not None else ""
            for p in group:
                final = final.replace(p["old"], p["new"], 1) if p["old"] else p["new"]
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(final, encoding="utf-8")
            invalidate_file_cache(resolved)
            written.append(resolved)
    except Exception as exc:
        # Rollback complet
        for w in written:
            try:
                before = snapshots.get(str(w))
                if before is None:
                    if w.exists():
                        w.unlink()
                else:
                    w.write_text(before, encoding="utf-8")
                invalidate_file_cache(w)
            except Exception as roll_exc:
                logger.error("[apply_patches] rollback failed on {}: {}", w, roll_exc)
        return HandlerResult.fail(
            f"apply_patches: écriture échouée, rollback effectué: {exc}",
            handler_name="apply_patches",
        )

    summary = f"✅ apply_patches: {len(norm)} patch(es) appliqué(s) sur {len(per_file_patches)} fichier(s)"
    lines = [summary]
    for key, group in per_file_patches.items():
        try:
            rel = Path(key).relative_to(ctx.runtime_root).as_posix()
        except Exception:
            rel = key
        lines.append(f"  • {rel}: {len(group)} patch(es)")
    return HandlerResult.ok("\n".join(lines), handler_name="apply_patches")


# ─── Handler: fanout_tasks (Levier 3) ──────────────────────────────────────


async def fanout_tasks_handler(
    ctx: HandlerContext,
    tasks: Any = None,
    max_concurrent: int = 3,
) -> HandlerResult:
    """Dispatche N tâches à des sous-agents en parallèle via SubAgentOrchestrator.

    Args:
        tasks: liste de dicts {description, agent_type?, context?}.
        max_concurrent: nombre max d'agents simultanés (1-10).
    """
    if not isinstance(tasks, list) or not tasks:
        return HandlerResult.fail("fanout_tasks: 'tasks' doit être une liste non vide")
    if len(tasks) > 8:
        return HandlerResult.fail(f"fanout_tasks: max 8 tâches (reçu {len(tasks)})")

    try:
        from src.agents.sub_agent import (
            AgentTask, AgentType, get_orchestrator,
        )
    except Exception as exc:
        return HandlerResult.fail(f"fanout_tasks: import orchestrator échoué: {exc}")

    orchestrator = get_orchestrator()
    agent_tasks: List[Any] = []
    from datetime import datetime as _dt
    for idx, t in enumerate(tasks):
        if not isinstance(t, dict):
            continue
        desc = str(t.get("description") or "").strip()
        if not desc:
            continue
        _raw_type = str(t.get("agent_type") or "general").lower().strip()
        try:
            atype = AgentType(_raw_type)
        except Exception:
            atype = AgentType.GENERAL
        _tctx = t.get("context") or {}
        if not isinstance(_tctx, dict):
            _tctx = {}
        task_id = f"fanout_{_dt.now().strftime('%H%M%S%f')}_{idx}"
        agent_tasks.append(AgentTask(
            task_id=task_id,
            description=desc,
            agent_type=atype,
            context=_tctx,
        ))

    if not agent_tasks:
        return HandlerResult.fail("fanout_tasks: aucune tâche valide (chaque tâche requiert 'description')")

    try:
        results = await orchestrator.dispatch_parallel(
            agent_tasks,
            max_concurrent=int(max_concurrent or 3),
        )
    except Exception as exc:
        return HandlerResult.fail(f"fanout_tasks: dispatch échoué: {exc}")

    # Synthèse
    lines: List[str] = []
    n_ok = 0
    for r in results:
        _ok = bool(getattr(r, "success", False))
        if _ok:
            n_ok += 1
        _out = str(getattr(r, "output", "") or "")
        _head = _out.split("\n", 1)[0][:200]
        lines.append(f"[{'✅' if _ok else '❌'} {r.task_id}] {_head}")
    summary = f"Fanout terminé : {n_ok}/{len(results)} succès\n" + "\n".join(lines)
    return HandlerResult.ok(summary, handler_name="fanout_tasks")


# ─── Registration ──────────────────────────────────────────────────────────


def get_batch_handler_defs() -> List[HandlerDef]:
    """Handlers batch V2 (Leviers 4 + 5 + support pour 2)."""
    return [
        HandlerDef(
            name="read_files_batch",
            description=(
                "Lit PLUSIEURS fichiers en un seul appel (parallèle + cache). "
                "Utilise cet outil dès que tu dois lire 2+ fichiers : 10× plus rapide "
                "que read_file appelé en boucle. "
                "Args: paths=['a.py','b.py',...] (max 20), start_line/end_line optionnels."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Liste des chemins de fichiers à lire (max 20).",
                    },
                    "start_line": {"type": "integer", "description": "Ligne de début (optionnel)."},
                    "end_line": {"type": "integer", "description": "Ligne de fin (optionnel)."},
                    "max_chars_per_file": {
                        "type": "integer",
                        "description": "Max chars par fichier (défaut 6000).",
                    },
                },
                "required": ["paths"],
            },
            handler=read_files_batch_handler,
            category="files",
            source_module="handlers.batch",
        ),
        HandlerDef(
            name="grep_batch",
            description=(
                "Cherche PLUSIEURS regex dans PLUSIEURS fichiers en un seul appel. "
                "Remplace N greps séquentiels par un seul. "
                "Args: patterns=['regex1','regex2',...], paths=['file.py','dossier/',...]."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "patterns": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Liste des regex (max 10).",
                    },
                    "paths": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Fichiers ou dossiers à scanner.",
                    },
                    "max_results_per_pattern": {
                        "type": "integer",
                        "description": "Max matches par pattern (défaut 50).",
                    },
                    "case_insensitive": {
                        "type": "boolean",
                        "description": "Recherche insensible à la casse (défaut true).",
                    },
                },
                "required": ["patterns", "paths"],
            },
            handler=grep_batch_handler,
            category="files",
            source_module="handlers.batch",
        ),
        HandlerDef(
            name="apply_patches",
            description=(
                "Applique PLUSIEURS edits (replace old→new) ATOMIQUEMENT sur un ou "
                "plusieurs fichiers : si UN patch échoue, TOUT est rollback. "
                "Remplace N edit_file séquentiels par un seul appel sûr. "
                "Args: patches=[{file,old,new},...] (max 50)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "patches": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "file": {"type": "string"},
                                "old": {"type": "string"},
                                "new": {"type": "string"},
                            },
                            "required": ["file", "old", "new"],
                        },
                        "description": "Liste des patches à appliquer (max 50).",
                    },
                },
                "required": ["patches"],
            },
            handler=apply_patches_handler,
            category="files",
            source_module="handlers.batch",
        ),
        HandlerDef(
            name="fanout_tasks",
            description=(
                "Dispatche N tâches indépendantes à des sous-agents EN PARALLÈLE "
                "(Semaphore, max_concurrent). Utilise pour tâches non-dépendantes "
                "(ex: 'crée 3 pages HTML indépendantes'). "
                "Args: tasks=[{description, agent_type?, context?},...], max_concurrent=3."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "description": {"type": "string"},
                                "agent_type": {"type": "string", "description": "code|research|file|general"},
                                "context": {"type": "object"},
                            },
                            "required": ["description"],
                        },
                        "description": "Liste des tâches à exécuter en parallèle (max 8).",
                    },
                    "max_concurrent": {
                        "type": "integer",
                        "description": "Nombre d'agents simultanés (défaut 3, max 10).",
                    },
                },
                "required": ["tasks"],
            },
            handler=fanout_tasks_handler,
            category="agents",
            source_module="handlers.batch",
        ),
    ]
