"""Fail-safe bridge from existing intake channels to Document Studio."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def index_received_document(
    path: str | Path,
    *,
    source_kind: str,
    source_uri: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Index an already-saved attachment without changing channel semantics."""
    try:
        from .studio import get_document_studio

        record, duplicate = get_document_studio().importer.import_file(
            Path(path),
            source_kind=source_kind,
            source_uri=source_uri or str(path),
            metadata=metadata,
        )
        return {"indexed": True, "document_id": record.id, "duplicate": duplicate}
    except Exception as exc:
        return {"indexed": False, "error": str(exc)}
