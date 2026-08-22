"""Serializable provenance records for imported and generated documents."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
import uuid


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class DocumentRecord:
    id: str
    sha256: str
    filename: str
    path: str
    format: str
    mime_type: str
    size: int
    source_kind: str
    source_uri: str = ""
    imported_at: str = field(default_factory=utc_now_iso)
    title: str = ""
    content_text: str = ""
    parent_id: str = ""
    template_id: str = ""
    mission_id: str = ""
    conversation_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, **kwargs: Any) -> "DocumentRecord":
        kwargs.setdefault("id", f"doc_{uuid.uuid4().hex[:16]}")
        return cls(**kwargs)

    def to_dict(self, *, include_content: bool = True) -> dict[str, Any]:
        data = asdict(self)
        if not include_content:
            data.pop("content_text", None)
        return data


@dataclass(frozen=True)
class DocumentTransformation:
    id: str
    document_id: str
    operation: str
    created_at: str
    input_sha256: str
    output_sha256: str
    details: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, **kwargs: Any) -> "DocumentTransformation":
        kwargs.setdefault("id", f"tx_{uuid.uuid4().hex[:16]}")
        kwargs.setdefault("created_at", utc_now_iso())
        return cls(**kwargs)

