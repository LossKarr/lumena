"""
📁 LUMENA - Crawler de fichiers locaux (campagnes persistantes)

Permet de scanner de très gros dossiers avec reprise, status et export index.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import fnmatch
import json
import re
import threading
import uuid
from ..utils.persistence import atomic_write_json, safe_read_json

from loguru import logger



class LocalFileCrawler:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.campaigns_dir = self.data_dir / "file_campaigns"
        self.campaigns_dir.mkdir(parents=True, exist_ok=True)
        # Guard anti-double-run par campaign_id (in-memory, reset au redémarrage)
        self._running_campaigns: set = set()
        self._campaigns_lock = threading.Lock()

    def _campaign_dir(self, campaign_id: str) -> Path:
        return self.campaigns_dir / campaign_id

    def _campaign_state_path(self, campaign_id: str) -> Path:
        return self._campaign_dir(campaign_id) / "state.json"

    def _workspace_root(self) -> Path:
        from ..utils.paths import WORKSPACE_DIR
        root = WORKSPACE_DIR.resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _reports_archive_dir(self, campaign_id: str) -> Path:
        day_key = datetime.utcnow().strftime("%Y-%m-%d")
        base = self._workspace_root() / day_key / "reports" / "local_file_crawler" / campaign_id
        base.mkdir(parents=True, exist_ok=True)
        return base

    @staticmethod
    def _default_campaign_id() -> str:
        return datetime.now(tz=__import__('datetime').timezone.utc).strftime("filecrawl_%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8]

    @staticmethod
    def _utcnow_iso() -> str:
        return datetime.now(tz=__import__('datetime').timezone.utc).isoformat().replace("+00:00", "Z")

    @staticmethod
    def _normalize_globs(patterns: Any) -> List[str]:
        if patterns is None:
            return []

        raw_items: List[str] = []
        if isinstance(patterns, str):
            raw_items = patterns.split(",")
        elif isinstance(patterns, (list, tuple, set)):
            for item in patterns:
                if item is None:
                    continue
                if isinstance(item, str):
                    raw_items.extend(item.split(","))
                else:
                    raw_items.append(str(item))
        else:
            raw_items = str(patterns).split(",")

        return [p.strip().lower() for p in raw_items if p and p.strip()]

    @staticmethod
    def _is_allowed_by_patterns(path_value: str, includes: List[str], excludes: List[str]) -> bool:
        lowered = path_value.lower().replace("\\", "/")
        if includes and not any(fnmatch.fnmatch(lowered, pat) for pat in includes):
            return False
        if excludes and any(fnmatch.fnmatch(lowered, pat) for pat in excludes):
            return False
        return True

    def _save_campaign_state(self, campaign_id: str, state: Dict[str, Any]) -> None:
        state_path = self._campaign_state_path(campaign_id)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(state_path, state)

    def _load_campaign_state(self, campaign_id: str) -> Optional[Dict[str, Any]]:
        state_path = self._campaign_state_path(campaign_id)
        return safe_read_json(state_path, default=None) or None

    def _append_run_report(self, campaign_id: str, run_payload: Dict[str, Any]) -> Path:
        runs_dir = self._campaign_dir(campaign_id) / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        run_id = run_payload.get("run_id") or datetime.utcnow().strftime("run_%Y%m%d_%H%M%S")
        report_path = runs_dir / f"{run_id}.json"
        atomic_write_json(report_path, run_payload)

        archive_dir = self._reports_archive_dir(campaign_id)
        archive_path = archive_dir / f"{run_id}.json"
        atomic_write_json(archive_path, run_payload)
        return report_path

    @staticmethod
    def _extract_text_excerpt(file_path: Path, max_chars: int = 12000) -> Tuple[str, str, Optional[str]]:
        try:
            data = file_path.read_bytes()
        except Exception as exc:
            return "", "", f"lecture impossible: {exc}"

        if b"\x00" in data[:4096]:
            return "", "", "fichier binaire détecté"

        text = ""
        for enc in ("utf-8", "latin-1"):
            try:
                text = data.decode(enc, errors="replace")
                break
            except Exception:
                continue

        text = text.strip()
        if not text:
            return "", "", "contenu vide"

        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) > max_chars:
            text = text[:max_chars]

        title = file_path.name
        return text, title, None

    @staticmethod
    def _score_content(path_value: str, text: str, keyword_hint: str) -> float:
        score = 0.0
        lowered_path = path_value.lower()
        lowered_text = text.lower()

        ext_boost = {
            ".md": 0.8,
            ".txt": 0.7,
            ".json": 0.6,
            ".csv": 0.5,
            ".html": 0.6,
            ".htm": 0.6,
            ".xml": 0.5,
            ".py": 0.7,
            ".ts": 0.6,
            ".js": 0.6,
        }

        for ext, boost in ext_boost.items():
            if lowered_path.endswith(ext):
                score += boost
                break

        if len(text) >= 4000:
            score += 2.0
        elif len(text) >= 1200:
            score += 1.2
        elif len(text) >= 500:
            score += 0.6

        quality_markers = ["report", "analysis", "plan", "spec", "readme", "guide", "checklist"]
        for marker in quality_markers:
            if marker in lowered_path:
                score += 0.5

        if keyword_hint:
            tokens = [t.strip().lower() for t in re.split(r"[,\s]+", keyword_hint) if t.strip()]
            for token in tokens[:16]:
                if token in lowered_path:
                    score += 1.0
                if token in lowered_text:
                    score += 0.5

        return round(score, 2)

    async def crawl_campaign(
        self,
        *,
        root_path: str,
        campaign_id: str = "",
        files_per_run: int = 500,
        max_total_files: int = 1_000_000,
        max_depth: int = 8,
        keyword_hint: str = "",
        include_patterns: Any = "",
        exclude_patterns: Any = "",
        max_file_size_mb: float = 8.0,
    ) -> Dict[str, Any]:
        root = Path(str(root_path)).expanduser().resolve()
        if not root.exists() or not root.is_dir():
            return {"success": False, "error": f"Dossier invalide: {root_path}"}

        files_per_run = max(1, min(int(files_per_run), 5000))
        max_total_files = max(1, min(int(max_total_files), 2_000_000))
        max_depth = max(0, min(int(max_depth), 30))
        max_file_size_mb = max(0.1, min(float(max_file_size_mb), 200.0))
        max_file_size_bytes = int(max_file_size_mb * 1024 * 1024)

        include_globs = self._normalize_globs(include_patterns)
        exclude_globs = self._normalize_globs(exclude_patterns)

        if not campaign_id.strip():
            campaign_id = self._default_campaign_id()
        campaign_id = campaign_id.strip()

        with self._campaigns_lock:
            if campaign_id in self._running_campaigns:
                return {"success": False, "error": f"Campagne '{campaign_id}' déjà en cours d'exécution."}
            self._running_campaigns.add(campaign_id)

        try:
            return await self._run_campaign_inner(
                root=root, campaign_id=campaign_id,
                files_per_run=files_per_run, max_total_files=max_total_files,
                max_depth=max_depth, keyword_hint=keyword_hint,
                max_file_size_bytes=max_file_size_bytes,
                include_globs=include_globs, exclude_globs=exclude_globs,
            )
        finally:
            with self._campaigns_lock:
                self._running_campaigns.discard(campaign_id)

    async def _run_campaign_inner(
        self, *, root, campaign_id, files_per_run, max_total_files,
        max_depth, keyword_hint, max_file_size_bytes,
        include_globs, exclude_globs,
    ) -> Dict[str, Any]:
        state = self._load_campaign_state(campaign_id)
        if state is None:
            state = {
                "campaign_id": campaign_id,
                "created_at": self._utcnow_iso(),
                "updated_at": self._utcnow_iso(),
                "root_path": str(root),
                "options": {
                    "max_depth": max_depth,
                    "max_file_size_mb": max_file_size_bytes,
                    "include_patterns": include_globs,
                    "exclude_patterns": exclude_globs,
                    "keyword_hint": keyword_hint,
                },
                "limits": {
                    "max_total_files": max_total_files,
                },
                "stats": {
                    "runs": 0,
                    "dirs_processed_total": 0,
                    "files_scanned_total": 0,
                    "errors_total": 0,
                    "interesting_total": 0,
                },
                "queue_dirs": [{"path": str(root), "depth": 0}],
                "visited_dirs": [],
                "seen_files": [],
                "interesting": [],
                "last_run": None,
            }
        else:
            root = Path(state.get("root_path", str(root))).expanduser().resolve()

        visited_dirs = set(state.get("visited_dirs", []))
        seen_files = set(state.get("seen_files", []))
        queue_dirs: List[Tuple[str, int]] = []
        for item in state.get("queue_dirs", []):
            try:
                queue_dirs.append((str(item.get("path", "")), int(item.get("depth", 0))))
            except Exception:
                continue

        if not queue_dirs:
            queue_dirs.append((str(root), 0))

        stats = state.get("stats", {})
        files_scanned_total = int(stats.get("files_scanned_total", 0))
        if files_scanned_total >= max_total_files:
            return {
                "success": True,
                "campaign_id": campaign_id,
                "message": "Campagne déjà au maximum configuré",
                "done": True,
                "files_scanned_total": files_scanned_total,
                "max_total_files": max_total_files,
            }

        started_at = datetime.utcnow()
        run_id = started_at.strftime("run_%Y%m%d_%H%M%S")
        run_files: List[Dict[str, Any]] = []
        run_scanned = 0
        run_interesting = 0
        run_errors = 0
        run_dirs = 0

        interesting_map = {item.get("path"): item for item in state.get("interesting", []) if item.get("path")}

        logger.info(
            "FileCrawler campaign start: campaign_id={} root={} files_per_run={} max_total_files={}",
            campaign_id,
            str(root),
            files_per_run,
            max_total_files,
        )

        while queue_dirs and run_scanned < files_per_run and files_scanned_total < max_total_files:
            current_dir_raw, depth = queue_dirs.pop(0)
            current_dir = Path(current_dir_raw)
            current_dir_key = str(current_dir)

            if current_dir_key in visited_dirs:
                continue
            if depth > max_depth:
                continue
            if not current_dir.exists() or not current_dir.is_dir():
                continue

            visited_dirs.add(current_dir_key)
            run_dirs += 1

            try:
                children = list(current_dir.iterdir())
            except Exception as exc:
                run_errors += 1
                run_files.append(
                    {
                        "path": current_dir_key,
                        "type": "dir",
                        "depth": depth,
                        "error": f"lecture dossier impossible: {exc}",
                    }
                )
                continue

            for child in children:
                if child.is_dir():
                    if depth + 1 <= max_depth:
                        queue_dirs.append((str(child), depth + 1))
                    continue

                if not child.is_file():
                    continue

                file_key = str(child)
                if file_key in seen_files:
                    continue

                normalized_path = file_key.replace("\\", "/")
                if not self._is_allowed_by_patterns(normalized_path, include_globs, exclude_globs):
                    seen_files.add(file_key)
                    continue

                seen_files.add(file_key)
                run_scanned += 1
                files_scanned_total += 1

                size_bytes = 0
                try:
                    size_bytes = child.stat().st_size
                except Exception:
                    pass  # permission denied ou lien cassé

                if size_bytes > max_file_size_bytes:
                    run_files.append(
                        {
                            "path": file_key,
                            "type": "file",
                            "depth": depth,
                            "size_bytes": size_bytes,
                            "score": 0.0,
                            "interesting": False,
                            "error": f"fichier trop volumineux > {max_file_size_bytes}B",
                        }
                    )
                    run_errors += 1
                    if run_scanned >= files_per_run or files_scanned_total >= max_total_files:
                        break
                    continue

                text, title, error = self._extract_text_excerpt(child)
                score = 0.0
                interesting = False
                excerpt = ""

                if error is None:
                    score = self._score_content(file_key, text, keyword_hint)
                    interesting = score >= 2.2
                    excerpt = text[:700].replace("\n", " ").strip()
                else:
                    run_errors += 1

                item = {
                    "path": file_key,
                    "type": "file",
                    "title": title or child.name,
                    "depth": depth,
                    "size_bytes": size_bytes,
                    "score": score,
                    "interesting": interesting,
                    "excerpt": excerpt,
                    "error": error,
                    "updated_at": self._utcnow_iso(),
                }
                run_files.append(item)

                if interesting:
                    run_interesting += 1
                    existing = interesting_map.get(file_key)
                    if (existing is None) or float(item.get("score", 0.0)) >= float(existing.get("score", 0.0)):
                        interesting_map[file_key] = item

                if run_scanned >= files_per_run or files_scanned_total >= max_total_files:
                    break

        interesting_list = sorted(
            interesting_map.values(),
            key=lambda x: float(x.get("score", 0.0)),
            reverse=True,
        )

        finished = (not queue_dirs) or (files_scanned_total >= max_total_files)
        ended_at = datetime.utcnow()
        duration_sec = max((ended_at - started_at).total_seconds(), 0.0)

        state["updated_at"] = self._utcnow_iso()
        state["root_path"] = str(root)
        state["options"] = {
            "max_depth": max_depth,
            "max_file_size_mb": max_file_size_bytes,
            "include_patterns": include_globs,
            "exclude_patterns": exclude_globs,
            "keyword_hint": keyword_hint,
        }
        state["limits"] = {
            "max_total_files": max_total_files,
        }

        stats = state.get("stats", {})
        stats["runs"] = int(stats.get("runs", 0)) + 1
        stats["dirs_processed_total"] = int(stats.get("dirs_processed_total", 0)) + run_dirs
        stats["files_scanned_total"] = files_scanned_total
        stats["errors_total"] = int(stats.get("errors_total", 0)) + run_errors
        stats["interesting_total"] = len(interesting_list)
        state["stats"] = stats

        state["queue_dirs"] = [{"path": p, "depth": d} for p, d in queue_dirs]
        state["visited_dirs"] = list(visited_dirs)
        state["seen_files"] = list(seen_files)
        state["interesting"] = interesting_list
        state["last_run"] = {
            "run_id": run_id,
            "started_at": started_at.isoformat() + "Z",
            "ended_at": ended_at.isoformat() + "Z",
            "duration_sec": round(duration_sec, 2),
            "run_dirs": run_dirs,
            "run_scanned": run_scanned,
            "run_interesting": run_interesting,
            "run_errors": run_errors,
            "done": finished,
        }

        self._save_campaign_state(campaign_id, state)

        run_payload = {
            "run_id": run_id,
            "campaign_id": campaign_id,
            "root_path": str(root),
            "started_at": started_at.isoformat() + "Z",
            "ended_at": ended_at.isoformat() + "Z",
            "duration_sec": round(duration_sec, 2),
            "run_dirs": run_dirs,
            "run_scanned": run_scanned,
            "run_interesting": run_interesting,
            "run_errors": run_errors,
            "files_scanned_total": files_scanned_total,
            "max_total_files": max_total_files,
            "queue_remaining": len(queue_dirs),
            "done": finished,
            "files": run_files,
        }
        run_report_path = self._append_run_report(campaign_id, run_payload)

        return {
            "success": True,
            "campaign_id": campaign_id,
            "run_id": run_id,
            "run_dirs": run_dirs,
            "run_scanned": run_scanned,
            "run_interesting": run_interesting,
            "run_errors": run_errors,
            "files_scanned_total": files_scanned_total,
            "max_total_files": max_total_files,
            "interesting_total": len(interesting_list),
            "queue_remaining": len(queue_dirs),
            "done": finished,
            "state_file": str(self._campaign_state_path(campaign_id)),
            "run_report": str(run_report_path),
            "next": "Relancer file_crawl_campaign avec le même campaign_id" if not finished else "",
        }

    def campaign_status(self, campaign_id: str) -> Dict[str, Any]:
        campaign_id = (campaign_id or "").strip()
        if not campaign_id:
            return {"success": False, "error": "campaign_id requis"}

        state = self._load_campaign_state(campaign_id)
        if state is None:
            return {"success": False, "error": f"Campagne introuvable: {campaign_id}"}

        stats = state.get("stats", {})
        limits = state.get("limits", {})
        queue_dirs = state.get("queue_dirs", [])
        top = sorted(
            state.get("interesting", []),
            key=lambda x: float(x.get("score", 0.0)),
            reverse=True,
        )[:10]

        files_scanned_total = int(stats.get("files_scanned_total", 0))
        max_total_files = int(limits.get("max_total_files", 0))
        done = (len(queue_dirs) == 0) or (max_total_files > 0 and files_scanned_total >= max_total_files)

        return {
            "success": True,
            "campaign_id": campaign_id,
            "root_path": state.get("root_path"),
            "runs": int(stats.get("runs", 0)),
            "dirs_processed_total": int(stats.get("dirs_processed_total", 0)),
            "files_scanned_total": files_scanned_total,
            "max_total_files": max_total_files,
            "interesting_total": int(stats.get("interesting_total", 0)),
            "errors_total": int(stats.get("errors_total", 0)),
            "queue_remaining": len(queue_dirs),
            "done": done,
            "updated_at": state.get("updated_at"),
            "top": top,
            "state_file": str(self._campaign_state_path(campaign_id)),
        }

    def campaign_export_index(self, campaign_id: str, top_n: int = 1000) -> Dict[str, Any]:
        campaign_id = (campaign_id or "").strip()
        if not campaign_id:
            return {"success": False, "error": "campaign_id requis"}

        state = self._load_campaign_state(campaign_id)
        if state is None:
            return {"success": False, "error": f"Campagne introuvable: {campaign_id}"}

        top_n = max(1, min(int(top_n), 50000))
        interesting = sorted(
            state.get("interesting", []),
            key=lambda x: float(x.get("score", 0.0)),
            reverse=True,
        )[:top_n]

        campaign_dir = self._campaign_dir(campaign_id)
        campaign_dir.mkdir(parents=True, exist_ok=True)

        index_json = campaign_dir / "index_interesting.json"
        index_md = campaign_dir / "index_interesting.md"

        json_payload = {
            "campaign_id": campaign_id,
            "root_path": state.get("root_path"),
            "generated_at": self._utcnow_iso(),
            "count": len(interesting),
            "items": interesting,
        }
        atomic_write_json(index_json, json_payload)

        lines = [
            f"# Index fichiers intéressants - {campaign_id}",
            "",
            f"Généré: {self._utcnow_iso()}",
            f"Root path: {state.get('root_path')}",
            f"Total: {len(interesting)}",
            "",
        ]

        for idx, item in enumerate(interesting, start=1):
            lines.append(f"## {idx}. [{item.get('score')}] {item.get('title')}")
            lines.append(f"- Path: {item.get('path')}")
            if item.get("size_bytes") is not None:
                lines.append(f"- Size bytes: {item.get('size_bytes')}")
            lines.append(f"- Updated: {item.get('updated_at')}")
            excerpt = (item.get("excerpt") or "").strip()
            if excerpt:
                lines.append(f"- Extrait: {excerpt[:300]}")
            lines.append("")

        index_md_content = "\n".join(lines)
        index_md.write_text(index_md_content, encoding="utf-8")

        archive_dir = self._reports_archive_dir(campaign_id)
        ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
        index_json_archive = archive_dir / f"index_interesting_{ts}.json"
        index_md_archive = archive_dir / f"index_interesting_{ts}.md"
        atomic_write_json(index_json_archive, json_payload)
        index_md_archive.write_text(index_md_content, encoding="utf-8")

        return {
            "success": True,
            "campaign_id": campaign_id,
            "count": len(interesting),
            "index_json": str(index_json),
            "index_md": str(index_md),
            "index_json_archive": str(index_json_archive),
            "index_md_archive": str(index_md_archive),
        }
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the Apache License, Version 2.0
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
