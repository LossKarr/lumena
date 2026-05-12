"""Persistent user conversation sessions for the web/API surfaces."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List, Optional
import json
import sqlite3

from src.utils.paths import SESSIONS_SQLITE


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: Any, limit: int = 500) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _json_dump(value: Optional[Dict[str, Any]]) -> str:
    try:
        return json.dumps(dict(value or {}), ensure_ascii=False, separators=(",", ":"))
    except Exception:
        return "{}"


def _json_load(value: Any) -> Dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _title_from_message(message: str) -> str:
    text = " ".join(str(message or "").strip().split())
    if not text:
        return "Nouvelle conversation"
    return _clean_text(text, 80)


class SessionStore:
    """Small SQLite-backed store for conversation history.

    It is intentionally independent from TaskOrchestrator and telemetry:
    chat should keep working even if this store is unavailable.
    """

    def __init__(self, db_path: Optional[str | Path] = None) -> None:
        self.db_path = Path(db_path or SESSIONS_SQLITE).expanduser().resolve()
        self._lock = RLock()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            with self._connect() as conn:
                conn.execute("PRAGMA journal_mode=WAL")
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS sessions (
                        conversation_id TEXT PRIMARY KEY,
                        user_id TEXT NOT NULL DEFAULT 'local:owner',
                        owner_user_id TEXT,
                        profile_id TEXT,
                        title TEXT NOT NULL,
                        channel TEXT NOT NULL DEFAULT 'web',
                        client TEXT NOT NULL DEFAULT 'unknown',
                        status TEXT NOT NULL DEFAULT 'running',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        archived INTEGER NOT NULL DEFAULT 0,
                        message_count INTEGER NOT NULL DEFAULT 0,
                        last_message_preview TEXT,
                        last_response_preview TEXT,
                        last_model TEXT,
                        last_provider TEXT,
                        last_task_id TEXT,
                        last_trace_id TEXT,
                        workspace_path TEXT,
                        metadata_json TEXT NOT NULL DEFAULT '{}'
                    );

                    CREATE TABLE IF NOT EXISTS session_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        conversation_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        ts TEXT NOT NULL,
                        request_id TEXT,
                        message_id TEXT,
                        task_id TEXT,
                        trace_id TEXT,
                        model_used TEXT,
                        provider_used TEXT,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        FOREIGN KEY(conversation_id) REFERENCES sessions(conversation_id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS session_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        conversation_id TEXT NOT NULL,
                        type TEXT NOT NULL,
                        status TEXT,
                        summary TEXT,
                        ts TEXT NOT NULL,
                        request_id TEXT,
                        task_id TEXT,
                        trace_id TEXT,
                        metadata_json TEXT NOT NULL DEFAULT '{}',
                        FOREIGN KEY(conversation_id) REFERENCES sessions(conversation_id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
                    CREATE INDEX IF NOT EXISTS idx_sessions_channel ON sessions(channel);
                    CREATE INDEX IF NOT EXISTS idx_messages_conversation ON session_messages(conversation_id, id);
                    CREATE INDEX IF NOT EXISTS idx_events_conversation ON session_events(conversation_id, id);
                    """
                )

    def upsert_session(
        self,
        *,
        conversation_id: str,
        channel: str = "web",
        client: str = "unknown",
        user_id: str = "local:owner",
        owner_user_id: Optional[str] = None,
        profile_id: Optional[str] = None,
        title: Optional[str] = None,
        status: str = "running",
        message_preview: Optional[str] = None,
        response_preview: Optional[str] = None,
        model_used: Optional[str] = None,
        provider_used: Optional[str] = None,
        task_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        workspace_path: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        conv_id = str(conversation_id or "").strip()
        if not conv_id:
            raise ValueError("conversation_id is required")

        now = _now_iso()
        with self._lock:
            with self._connect() as conn:
                existing = conn.execute(
                    "SELECT * FROM sessions WHERE conversation_id=?",
                    (conv_id,),
                ).fetchone()
                if existing is None:
                    session_title = title or _title_from_message(message_preview or response_preview or "")
                    conn.execute(
                        """
                        INSERT INTO sessions (
                            conversation_id, user_id, owner_user_id, profile_id, title,
                            channel, client, status, created_at, updated_at, archived,
                            message_count, last_message_preview, last_response_preview,
                            last_model, last_provider, last_task_id, last_trace_id,
                            workspace_path, metadata_json
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            conv_id,
                            user_id or "local:owner",
                            owner_user_id,
                            profile_id,
                            session_title,
                            channel or "web",
                            client or "unknown",
                            status or "running",
                            now,
                            now,
                            _clean_text(message_preview, 500) if message_preview else None,
                            _clean_text(response_preview, 500) if response_preview else None,
                            model_used,
                            provider_used,
                            task_id,
                            trace_id,
                            workspace_path,
                            _json_dump(metadata),
                        ),
                    )
                else:
                    merged_meta = _json_load(existing["metadata_json"])
                    if metadata:
                        merged_meta.update(metadata)
                    conn.execute(
                        """
                        UPDATE sessions SET
                            user_id=?,
                            owner_user_id=COALESCE(?, owner_user_id),
                            profile_id=COALESCE(?, profile_id),
                            title=CASE WHEN title='' OR title='Nouvelle conversation' THEN ? ELSE title END,
                            channel=?,
                            client=?,
                            status=?,
                            updated_at=?,
                            last_message_preview=COALESCE(?, last_message_preview),
                            last_response_preview=COALESCE(?, last_response_preview),
                            last_model=COALESCE(?, last_model),
                            last_provider=COALESCE(?, last_provider),
                            last_task_id=COALESCE(?, last_task_id),
                            last_trace_id=COALESCE(?, last_trace_id),
                            workspace_path=COALESCE(?, workspace_path),
                            metadata_json=?
                        WHERE conversation_id=?
                        """,
                        (
                            user_id or existing["user_id"] or "local:owner",
                            owner_user_id,
                            profile_id,
                            title or existing["title"],
                            channel or existing["channel"] or "web",
                            client or existing["client"] or "unknown",
                            status or existing["status"] or "running",
                            now,
                            _clean_text(message_preview, 500) if message_preview else None,
                            _clean_text(response_preview, 500) if response_preview else None,
                            model_used,
                            provider_used,
                            task_id,
                            trace_id,
                            workspace_path,
                            _json_dump(merged_meta),
                            conv_id,
                        ),
                    )
                row = conn.execute(
                    "SELECT * FROM sessions WHERE conversation_id=?",
                    (conv_id,),
                ).fetchone()
                return self._session_row(row)

    def record_message(
        self,
        *,
        conversation_id: str,
        role: str,
        content: str,
        channel: str = "web",
        client: str = "unknown",
        user_id: str = "local:owner",
        owner_user_id: Optional[str] = None,
        profile_id: Optional[str] = None,
        request_id: Optional[str] = None,
        message_id: Optional[str] = None,
        task_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        model_used: Optional[str] = None,
        provider_used: Optional[str] = None,
        workspace_path: Optional[str] = None,
        status: str = "running",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        normalized_role = str(role or "").strip().lower() or "user"
        preview = _clean_text(content, 500)
        title = _title_from_message(content) if normalized_role == "user" else None
        self.upsert_session(
            conversation_id=conversation_id,
            channel=channel,
            client=client,
            user_id=user_id,
            owner_user_id=owner_user_id,
            profile_id=profile_id,
            title=title,
            status=status,
            message_preview=preview if normalized_role == "user" else None,
            response_preview=preview if normalized_role == "assistant" else None,
            model_used=model_used,
            provider_used=provider_used,
            task_id=task_id,
            trace_id=trace_id,
            workspace_path=workspace_path,
            metadata=metadata,
        )
        with self._lock:
            with self._connect() as conn:
                ts = _now_iso()
                conn.execute(
                    """
                    INSERT INTO session_messages (
                        conversation_id, role, content, ts, request_id, message_id,
                        task_id, trace_id, model_used, provider_used, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        conversation_id,
                        normalized_role,
                        str(content or ""),
                        ts,
                        request_id,
                        message_id,
                        task_id,
                        trace_id,
                        model_used,
                        provider_used,
                        _json_dump(metadata),
                    ),
                )
                conn.execute(
                    "UPDATE sessions SET message_count=message_count+1, updated_at=? WHERE conversation_id=?",
                    (ts, conversation_id),
                )
        return self.get_session(conversation_id) or {}

    def record_event(
        self,
        *,
        conversation_id: str,
        event_type: str,
        status: Optional[str] = None,
        summary: Optional[str] = None,
        channel: str = "web",
        client: str = "unknown",
        user_id: str = "local:owner",
        request_id: Optional[str] = None,
        task_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        workspace_path: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.upsert_session(
            conversation_id=conversation_id,
            channel=channel,
            client=client,
            user_id=user_id,
            status=status or "running",
            task_id=task_id,
            trace_id=trace_id,
            workspace_path=workspace_path,
            metadata=metadata,
        )
        with self._lock:
            with self._connect() as conn:
                ts = _now_iso()
                conn.execute(
                    """
                    INSERT INTO session_events (
                        conversation_id, type, status, summary, ts,
                        request_id, task_id, trace_id, metadata_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        conversation_id,
                        str(event_type or "event"),
                        status,
                        _clean_text(summary, 1000) if summary else None,
                        ts,
                        request_id,
                        task_id,
                        trace_id,
                        _json_dump(metadata),
                    ),
                )

    def update_status(
        self,
        conversation_id: str,
        status: str,
        *,
        channel: str = "web",
        client: str = "unknown",
        user_id: str = "local:owner",
        task_id: Optional[str] = None,
        trace_id: Optional[str] = None,
        workspace_path: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.upsert_session(
            conversation_id=conversation_id,
            channel=channel,
            client=client,
            user_id=user_id,
            status=status,
            task_id=task_id,
            trace_id=trace_id,
            workspace_path=workspace_path,
            metadata=metadata,
        )

    def list_sessions(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
        status: str = "",
        channel: str = "",
        query: str = "",
        include_archived: bool = False,
    ) -> Dict[str, Any]:
        bounded_limit = max(1, min(int(limit), 500))
        bounded_offset = max(0, int(offset))
        clauses: List[str] = []
        params: List[Any] = []
        if not include_archived:
            clauses.append("archived=0")
        if status:
            clauses.append("status=?")
            params.append(status)
        if channel:
            clauses.append("channel=?")
            params.append(channel)
        if query:
            clauses.append(
                "(conversation_id LIKE ? OR title LIKE ? OR last_message_preview LIKE ? OR last_response_preview LIKE ?)"
            )
            needle = f"%{query}%"
            params.extend([needle, needle, needle, needle])
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            with self._connect() as conn:
                total = conn.execute(
                    f"SELECT COUNT(*) AS total FROM sessions {where}",
                    params,
                ).fetchone()["total"]
                rows = conn.execute(
                    f"""
                    SELECT
                        s.*,
                        (SELECT COUNT(*) FROM session_events e WHERE e.conversation_id=s.conversation_id) AS event_count
                    FROM sessions s
                    {where}
                    ORDER BY updated_at DESC
                    LIMIT ? OFFSET ?
                    """,
                    [*params, bounded_limit, bounded_offset],
                ).fetchall()
        return {
            "success": True,
            "sessions": [self._session_row(row) for row in rows],
            "total": int(total or 0),
            "limit": bounded_limit,
            "offset": bounded_offset,
        }

    def get_session(
        self,
        conversation_id: str,
        *,
        message_limit: int = 200,
        event_limit: int = 200,
    ) -> Optional[Dict[str, Any]]:
        conv_id = str(conversation_id or "").strip()
        if not conv_id:
            return None
        msg_limit = max(1, min(int(message_limit), 1000))
        evt_limit = max(1, min(int(event_limit), 1000))
        with self._lock:
            with self._connect() as conn:
                session = conn.execute(
                    "SELECT * FROM sessions WHERE conversation_id=?",
                    (conv_id,),
                ).fetchone()
                if session is None:
                    return None
                messages = conn.execute(
                    """
                    SELECT * FROM (
                        SELECT * FROM session_messages
                        WHERE conversation_id=?
                        ORDER BY id DESC
                        LIMIT ?
                    ) ORDER BY id ASC
                    """,
                    (conv_id, msg_limit),
                ).fetchall()
                events = conn.execute(
                    """
                    SELECT * FROM (
                        SELECT * FROM session_events
                        WHERE conversation_id=?
                        ORDER BY id DESC
                        LIMIT ?
                    ) ORDER BY id ASC
                    """,
                    (conv_id, evt_limit),
                ).fetchall()
        return {
            "success": True,
            "session": self._session_row(session),
            "messages": [self._message_row(row) for row in messages],
            "events": [self._event_row(row) for row in events],
        }

    def archive_session(self, conversation_id: str, archived: bool = True) -> bool:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    "UPDATE sessions SET archived=?, updated_at=? WHERE conversation_id=?",
                    (1 if archived else 0, _now_iso(), conversation_id),
                )
                return cur.rowcount > 0

    def delete_session(self, conversation_id: str) -> bool:
        with self._lock:
            with self._connect() as conn:
                cur = conn.execute(
                    "DELETE FROM sessions WHERE conversation_id=?",
                    (conversation_id,),
                )
                return cur.rowcount > 0

    def stats(self) -> Dict[str, Any]:
        with self._lock:
            with self._connect() as conn:
                row = conn.execute(
                    """
                    SELECT
                        COUNT(*) AS total,
                        SUM(CASE WHEN archived=1 THEN 1 ELSE 0 END) AS archived,
                        SUM(CASE WHEN status IN ('running','waiting_io','checkpointed') THEN 1 ELSE 0 END) AS active
                    FROM sessions
                    """
                ).fetchone()
        return {
            "total": int(row["total"] or 0),
            "archived": int(row["archived"] or 0),
            "active": int(row["active"] or 0),
        }

    @staticmethod
    def _session_row(row: sqlite3.Row) -> Dict[str, Any]:
        payload = dict(row)
        payload["archived"] = bool(payload.get("archived"))
        payload["metadata"] = _json_load(payload.pop("metadata_json", "{}"))
        return payload

    @staticmethod
    def _message_row(row: sqlite3.Row) -> Dict[str, Any]:
        payload = dict(row)
        payload["metadata"] = _json_load(payload.pop("metadata_json", "{}"))
        return payload

    @staticmethod
    def _event_row(row: sqlite3.Row) -> Dict[str, Any]:
        payload = dict(row)
        payload["metadata"] = _json_load(payload.pop("metadata_json", "{}"))
        return payload
