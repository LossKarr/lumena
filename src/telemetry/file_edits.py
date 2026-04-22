from __future__ import annotations

import difflib
import os
import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Tuple

from loguru import logger


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except Exception:
        return default  # parsing int échoué


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_text(value: Any, max_len: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_len:
        return text
    if max_len <= 3:
        return text[:max_len]
    return text[: max_len - 3] + "..."


def _clip_optional_text(value: Optional[str], max_len: int) -> Tuple[Optional[str], bool]:
    if value is None:
        return None, False
    text = str(value)
    if len(text) <= max_len:
        return text, False
    if max_len <= 3:
        return text[:max_len], True
    return text[: max_len - 3] + "...", True


def _build_diff_preview(
    before_content: Optional[str],
    after_content: Optional[str],
    max_lines: int,
    max_line_len: int,
) -> Tuple[List[str], int, int]:
    before_lines = (before_content or "").splitlines()
    after_lines = (after_content or "").splitlines()
    diff_lines = list(
        difflib.unified_diff(
            before_lines,
            after_lines,
            fromfile="before",
            tofile="after",
            lineterm="",
        )
    )

    preview: List[str] = []
    additions = 0
    deletions = 0
    for line in diff_lines:
        if line.startswith("+++ ") or line.startswith("--- "):
            continue
        if line.startswith("+"):
            additions += 1
        elif line.startswith("-"):
            deletions += 1

        # Include context lines (space prefix) and @@ hunk headers
        preview.append(_safe_text(line, max_line_len))
        if len(preview) >= max_lines:
            break

    if not preview:
        preview = ["(no diff preview available)"]

    return preview, additions, deletions


@dataclass
class _FileSnapshot:
    path: Path
    existed_before: bool
    before_content: Optional[str]
    edit_id: str


@dataclass
class _EditSession:
    session_id: str
    trace_ids: set[str] = field(default_factory=set)
    turn_ids: set[str] = field(default_factory=set)
    created_at: str = field(default_factory=_now_iso)
    edits: List[Dict[str, Any]] = field(default_factory=list)
    snapshots: List[_FileSnapshot] = field(default_factory=list)
    last_activity_at: str = field(default_factory=_now_iso)


class FileEditsStore:
    def __init__(
        self,
        *,
        enabled: bool = True,
        undo_enabled: bool = True,
        preview_lines: int = 80,
        preview_line_max: int = 240,
        payload_text_max: int = 200_000,
        max_sessions: int = 120,
    ) -> None:
        self.enabled = bool(enabled)
        self.undo_enabled = bool(undo_enabled)
        self.preview_lines = max(1, int(preview_lines))
        self.preview_line_max = max(40, int(preview_line_max))
        self.payload_text_max = max(2_000, int(payload_text_max))
        self.max_sessions = max(10, int(max_sessions))

        self._lock = threading.Lock()
        self._trace_to_session: Dict[str, str] = {}
        self._trace_consume_cursor: Dict[str, int] = {}
        self._sessions: Dict[str, _EditSession] = {}
        self._session_order: Deque[str] = deque()

    def _evict_old_sessions_if_needed(self) -> None:
        while len(self._session_order) > self.max_sessions:
            stale = self._session_order.popleft()
            self._sessions.pop(stale, None)
            for trace_id, mapped in list(self._trace_to_session.items()):
                if mapped == stale:
                    self._trace_to_session.pop(trace_id, None)
                    self._trace_consume_cursor.pop(trace_id, None)

    def _ensure_session_locked(self, trace_id: str, turn_id: Optional[str]) -> _EditSession:
        session_id = self._trace_to_session.get(trace_id)
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
            if turn_id:
                session.turn_ids.add(turn_id)
            session.trace_ids.add(trace_id)
            return session

        session_id = uuid.uuid4().hex
        session = _EditSession(session_id=session_id)
        session.trace_ids.add(trace_id)
        if turn_id:
            session.turn_ids.add(turn_id)
        self._sessions[session_id] = session
        self._session_order.append(session_id)
        self._trace_to_session[trace_id] = session_id
        self._trace_consume_cursor[trace_id] = 0
        self._evict_old_sessions_if_needed()
        return session

    def start_edit_session(
        self,
        *,
        trace_id: str,
        turn_id: Optional[str] = None,
    ) -> Optional[str]:
        if not self.enabled or not trace_id:
            return None
        with self._lock:
            session = self._ensure_session_locked(trace_id, turn_id)
            session.last_activity_at = _now_iso()
            return session.session_id

    def get_session_id_for_trace(self, trace_id: Optional[str]) -> Optional[str]:
        if not trace_id:
            return None
        with self._lock:
            session_id = self._trace_to_session.get(trace_id)
            if session_id and session_id in self._sessions:
                return session_id
            return None

    def has_undo_for_trace(self, trace_id: Optional[str]) -> bool:
        if not self.undo_enabled:
            return False
        session_id = self.get_session_id_for_trace(trace_id)
        if not session_id:
            return False
        with self._lock:
            session = self._sessions.get(session_id)
            return bool(session and session.snapshots)

    def record_edit(
        self,
        *,
        trace_id: str,
        turn_id: Optional[str],
        task_id: Optional[str] = None,
        tool_name: str,
        action: str,
        file_path: str,
        workspace_relative: Optional[str] = None,
        before_content: Optional[str] = None,
        after_content: Optional[str] = None,
        existed_before: bool = True,
        summary: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        if not self.enabled or not trace_id or not file_path:
            return None

        with self._lock:
            session = self._ensure_session_locked(trace_id, turn_id)
            session.last_activity_at = _now_iso()

            edit_id = uuid.uuid4().hex
            preview, additions, deletions = _build_diff_preview(
                before_content=before_content,
                after_content=after_content,
                max_lines=self.preview_lines,
                max_line_len=self.preview_line_max,
            )
            before_payload, before_truncated = _clip_optional_text(before_content, self.payload_text_max)
            after_payload, after_truncated = _clip_optional_text(after_content, self.payload_text_max)

            item = {
                "id": edit_id,
                "trace_id": trace_id,
                "turn_id": turn_id or "",
                "task_id": (task_id or "").strip() or None,
                "session_id": session.session_id,
                "tool_name": tool_name,
                "action": action,
                "file_path": file_path,
                "workspace_relative": workspace_relative,
                "additions": int(additions),
                "deletions": int(deletions),
                "summary": _safe_text(summary or f"{action}: {file_path}", 180),
                "diff_preview": preview,
                "before_content": before_payload,
                "after_content": after_payload,
                "before_truncated": bool(before_truncated),
                "after_truncated": bool(after_truncated),
            }
            session.edits.append(item)

            if self.undo_enabled:
                session.snapshots.append(
                    _FileSnapshot(
                        path=Path(file_path),
                        existed_before=bool(existed_before),
                        before_content=before_content,
                        edit_id=edit_id,
                    )
                )

            return dict(item)

    def peek_session_edits(self, trace_id: str, after_index: int = 0) -> List[Dict[str, Any]]:
        if not self.enabled or not trace_id:
            return []
        with self._lock:
            session_id = self._trace_to_session.get(trace_id)
            if not session_id:
                return []
            session = self._sessions.get(session_id)
            if not session:
                return []
            index = max(0, int(after_index))
            return [dict(item) for item in session.edits[index:]]

    def consume_session_edits(self, trace_id: str) -> List[Dict[str, Any]]:
        if not self.enabled or not trace_id:
            return []
        with self._lock:
            session_id = self._trace_to_session.get(trace_id)
            if not session_id:
                return []
            session = self._sessions.get(session_id)
            if not session:
                return []
            cursor = self._trace_consume_cursor.get(trace_id, 0)
            cursor = max(0, int(cursor))
            out = [dict(item) for item in session.edits[cursor:]]
            self._trace_consume_cursor[trace_id] = len(session.edits)
            return out

    def _restore_snapshot(self, snapshot: _FileSnapshot) -> Tuple[bool, str]:
        path = snapshot.path
        try:
            if snapshot.existed_before:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(snapshot.before_content or "", encoding="utf-8")
                return True, f"restored:{path}"

            if path.exists():
                path.unlink()
            return True, f"deleted:{path}"
        except Exception as exc:
            return False, f"{path}: {exc}"

    def undo_session(self, session_id: str) -> Dict[str, Any]:
        if not self.undo_enabled:
            return {"success": False, "message": "undo disabled"}
        if not session_id:
            return {"success": False, "message": "missing session_id"}

        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return {"success": False, "message": "session not found"}
            snapshots = list(session.snapshots)

        if not snapshots:
            return {"success": False, "message": "no undo snapshot available"}

        restored = 0
        errors: List[str] = []
        for snapshot in reversed(snapshots):
            ok, msg = self._restore_snapshot(snapshot)
            if ok:
                restored += 1
            else:
                errors.append(msg)

        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.snapshots.clear()

        return {
            "success": len(errors) == 0,
            "session_id": session_id,
            "restored": restored,
            "errors": errors,
        }

    def undo_file(self, session_id: str, file_path: str) -> Dict[str, Any]:
        if not self.undo_enabled:
            return {"success": False, "message": "undo disabled"}
        if not session_id or not file_path:
            return {"success": False, "message": "missing parameters"}

        target = str(Path(file_path))
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return {"success": False, "message": "session not found"}
            file_snaps = [s for s in session.snapshots if str(s.path) == target]
            if not file_snaps:
                return {"success": False, "message": "file snapshot not found"}
            first = file_snaps[0]

        ok, msg = self._restore_snapshot(first)

        with self._lock:
            session = self._sessions.get(session_id)
            if session is not None:
                session.snapshots = [s for s in session.snapshots if str(s.path) != target]

        return {
            "success": ok,
            "session_id": session_id,
            "file_path": target,
            "message": msg,
        }

    def clear_for_tests(self) -> None:
        with self._lock:
            self._trace_to_session.clear()
            self._trace_consume_cursor.clear()
            self._sessions.clear()
            self._session_order.clear()


# Singleton avec lock thread-safe (Phase 2.1)
_file_edits_store: Optional[FileEditsStore] = None
_file_edits_store_lock = threading.Lock()


def get_file_edits_store() -> FileEditsStore:
    """Retourne l'instance singleton du FileEditsStore (thread-safe)."""
    global _file_edits_store
    
    # Double-check locking pattern
    if _file_edits_store is None:
        with _file_edits_store_lock:
            if _file_edits_store is None:
                _file_edits_store = FileEditsStore(
                    enabled=_env_flag("LUMENA_CHAT_FILE_CARDS", True),
                    undo_enabled=_env_flag("LUMENA_UNDO_ENABLED", True),
                    preview_lines=_env_int("LUMENA_FILE_EDIT_PREVIEW_LINES", 12),
                    preview_line_max=_env_int("LUMENA_FILE_EDIT_PREVIEW_LINE_MAX", 240),
                    payload_text_max=_env_int("LUMENA_FILE_EDIT_PAYLOAD_TEXT_MAX", 200_000),
                    max_sessions=_env_int("LUMENA_FILE_EDIT_MAX_SESSIONS", 120),
                )
    return _file_edits_store


def reset_file_edits_store_for_tests() -> None:
    """Reset le FileEditsStore pour les tests (thread-safe)."""
    global _file_edits_store
    with _file_edits_store_lock:
        _file_edits_store = None


def compute_workspace_relative(file_path: Path, workspace_root: Path) -> Optional[str]:
    try:
        return str(file_path.resolve().relative_to(workspace_root.resolve())).replace("\\", "/")
    except Exception:
        return None  # chemin non relativisable


def read_text_if_exists(file_path: Path) -> Tuple[bool, Optional[str]]:
    try:
        if not file_path.exists() or not file_path.is_file():
            return False, None
        return True, file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        logger.debug("Cannot read file for edit snapshot {}: {}", file_path, exc)
        return file_path.exists(), None
# ──────────────────────────────────────────────────────────────────────────────
# © 2025-2026 LossKarr — Lumena Project
# Licensed under the GNU General Public License v3.0 (GPL-3.0)
# https://github.com/Losskarr/lumena
# ──────────────────────────────────────────────────────────────────────────────
