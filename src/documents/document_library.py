"""SQLite-backed document library and local content search."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import threading
import re
from typing import Any, Iterator

from .provenance import DocumentRecord, DocumentTransformation


def _fts_literal_query(value: str) -> str:
    """Build a literal FTS5 query; punctuation must never become FTS syntax."""
    tokens = re.findall(r"\w+", str(value or ""), flags=re.UNICODE)
    return " AND ".join(f'"{token.replace(chr(34), chr(34) * 2)}"' for token in tokens)


class DocumentLibrary:
    def __init__(self, database_path: Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._fts = False
        self._initialize()

    def upsert(self, record: DocumentRecord) -> DocumentRecord:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO documents (
                    id, sha256, filename, path, format, mime_type, size, source_kind,
                    source_uri, imported_at, title, content_text, parent_id, template_id,
                    mission_id, conversation_id, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    sha256=excluded.sha256, filename=excluded.filename, path=excluded.path,
                    format=excluded.format, mime_type=excluded.mime_type, size=excluded.size,
                    source_kind=excluded.source_kind, source_uri=excluded.source_uri,
                    imported_at=excluded.imported_at, title=excluded.title,
                    content_text=excluded.content_text, parent_id=excluded.parent_id,
                    template_id=excluded.template_id, mission_id=excluded.mission_id,
                    conversation_id=excluded.conversation_id, metadata_json=excluded.metadata_json
                """,
                self._record_values(record),
            )
            if self._fts:
                conn.execute("DELETE FROM documents_fts WHERE document_id = ?", (record.id,))
                conn.execute(
                    "INSERT INTO documents_fts(document_id, title, content_text) VALUES (?, ?, ?)",
                    (record.id, record.title, record.content_text),
                )
        return record

    def find_by_hash(self, sha256: str) -> DocumentRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE sha256 = ? ORDER BY imported_at LIMIT 1", (sha256,)).fetchone()
        return self._row_to_record(row) if row else None

    def get(self, document_id: str) -> DocumentRecord | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM documents WHERE id = ?", (document_id,)).fetchone()
        return self._row_to_record(row) if row else None

    def resolve_reference(
        self,
        reference: str,
        *,
        allow_search: bool = True,
    ) -> DocumentRecord | None:
        """Resolve an exact reference, optionally followed by one unambiguous search hit."""
        wanted = str(reference or "").strip()
        if not wanted:
            return None
        direct = self.get(wanted)
        if direct is not None:
            return direct
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM documents
                WHERE lower(filename)=lower(?) OR lower(path)=lower(?) OR lower(title)=lower(?)
                ORDER BY imported_at DESC
                """,
                (wanted, wanted, wanted),
            ).fetchall()
        if len(rows) == 1:
            return self._row_to_record(rows[0])
        if len(rows) > 1:
            raise ValueError(f"reference documentaire ambigue: {wanted}")
        if not allow_search:
            return None
        matches = self.search(wanted, limit=3)
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ValueError(f"reference documentaire ambigue: {wanted}")
        return None

    def list(self, *, limit: int = 100, offset: int = 0, format: str = "") -> list[DocumentRecord]:
        query = "SELECT * FROM documents"
        params: list[Any] = []
        if format:
            query += " WHERE format = ?"
            params.append(format.lower().lstrip("."))
        query += " ORDER BY imported_at DESC LIMIT ? OFFSET ?"
        params.extend([max(1, min(int(limit), 500)), max(0, int(offset))])
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def search(
        self, query: str, *, formats: list[str] | None = None, source: str = "",
        date_from: str = "", date_to: str = "", template_id: str = "",
        mission_id: str = "", limit: int = 50,
    ) -> list[DocumentRecord]:
        text = str(query or "").strip()
        formats = [f.lower().lstrip(".") for f in (formats or []) if f]
        def _statement(use_fts: bool) -> tuple[str, list[Any]]:
            if use_fts:
                sql = "SELECT d.* FROM documents_fts f JOIN documents d ON d.id=f.document_id WHERE documents_fts MATCH ?"
                params: list[Any] = [_fts_literal_query(text)]
            else:
                sql = "SELECT * FROM documents WHERE (title LIKE ? OR filename LIKE ? OR content_text LIKE ?)"
                like = f"%{text}%"
                params = [like, like, like]
            if formats:
                format_column = "d.format" if use_fts else "format"
                sql += f" AND {format_column} IN ({','.join('?' for _ in formats)})"
                params.extend(formats)
            prefix = "d." if use_fts else ""
            for column, value, operator in (
                ("source_kind", source, "="), ("imported_at", date_from, ">="),
                ("imported_at", date_to, "<="), ("template_id", template_id, "="),
                ("mission_id", mission_id, "="),
            ):
                if value:
                    sql += f" AND {prefix}{column} {operator} ?"
                    params.append(value)
            sql += " ORDER BY imported_at DESC LIMIT ?"
            params.append(max(1, min(int(limit), 200)))
            return sql, params

        use_fts = bool(text and self._fts and _fts_literal_query(text))
        with self._connect() as conn:
            sql, params = _statement(use_fts)
            try:
                rows = conn.execute(sql, params).fetchall()
            except sqlite3.OperationalError:
                if not use_fts:
                    raise
                sql, params = _statement(False)
                rows = conn.execute(sql, params).fetchall()
        return [self._row_to_record(row) for row in rows]

    def add_transformation(self, transformation: DocumentTransformation) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO transformations(id, document_id, operation, created_at, input_sha256, output_sha256, details_json) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    transformation.id,
                    transformation.document_id,
                    transformation.operation,
                    transformation.created_at,
                    transformation.input_sha256,
                    transformation.output_sha256,
                    json.dumps(transformation.details, ensure_ascii=False),
                ),
            )

    def list_transformations(self, document_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM transformations WHERE document_id = ? ORDER BY created_at DESC",
                (document_id,),
            ).fetchall()
        return [
            {
                "id": row["id"], "document_id": row["document_id"],
                "operation": row["operation"], "created_at": row["created_at"],
                "input_sha256": row["input_sha256"], "output_sha256": row["output_sha256"],
                "details": json.loads(row["details_json"] or "{}"),
            }
            for row in rows
        ]

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY, sha256 TEXT NOT NULL, filename TEXT NOT NULL,
                    path TEXT NOT NULL, format TEXT NOT NULL, mime_type TEXT NOT NULL,
                    size INTEGER NOT NULL, source_kind TEXT NOT NULL, source_uri TEXT NOT NULL,
                    imported_at TEXT NOT NULL, title TEXT NOT NULL, content_text TEXT NOT NULL,
                    parent_id TEXT NOT NULL, template_id TEXT NOT NULL, mission_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL, metadata_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents(sha256);
                CREATE INDEX IF NOT EXISTS idx_documents_format ON documents(format);
                CREATE TABLE IF NOT EXISTS transformations (
                    id TEXT PRIMARY KEY, document_id TEXT NOT NULL, operation TEXT NOT NULL,
                    created_at TEXT NOT NULL, input_sha256 TEXT NOT NULL,
                    output_sha256 TEXT NOT NULL, details_json TEXT NOT NULL
                );
                """
            )
            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS documents_fts USING fts5(document_id UNINDEXED, title, content_text)"
                )
                self._fts = True
            except sqlite3.OperationalError:
                self._fts = False

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.database_path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _record_values(record: DocumentRecord) -> tuple[Any, ...]:
        return (
            record.id, record.sha256, record.filename, record.path, record.format,
            record.mime_type, record.size, record.source_kind, record.source_uri,
            record.imported_at, record.title, record.content_text, record.parent_id,
            record.template_id, record.mission_id, record.conversation_id,
            json.dumps(record.metadata, ensure_ascii=False),
        )

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> DocumentRecord:
        return DocumentRecord(
            id=row["id"], sha256=row["sha256"], filename=row["filename"], path=row["path"],
            format=row["format"], mime_type=row["mime_type"], size=row["size"],
            source_kind=row["source_kind"], source_uri=row["source_uri"],
            imported_at=row["imported_at"], title=row["title"], content_text=row["content_text"],
            parent_id=row["parent_id"], template_id=row["template_id"], mission_id=row["mission_id"],
            conversation_id=row["conversation_id"], metadata=json.loads(row["metadata_json"] or "{}"),
        )
