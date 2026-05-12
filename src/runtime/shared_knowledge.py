"""Phase 10 Lot G - controlled shared knowledge.

This module is intentionally conservative:
- no automatic memory scraping
- no automatic import into local memory
- every record has origin metadata
- sharing is scoped to one peer in V1
"""
from __future__ import annotations

import json
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.utils.paths import DATA_DIR

SHARED_KNOWLEDGE_FILE = DATA_DIR / "shared_knowledge.json"
VALID_VISIBILITIES = {"private", "shared_with_peer"}
_STORE_LOCK = threading.Lock()
_MAX_TITLE_CHARS = 160
_MAX_SUMMARY_CHARS = 4000
_MAX_TAGS = 12
_MAX_REFS = 20
_MIN_IMPORT_CONFIDENCE = 0.5


def _fingerprint(title: str, summary: str) -> str:
    import hashlib

    text = f"{title or ''}\n{summary or ''}".lower()
    text = re.sub(r"\s+", " ", text).strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:24]


def _store_path(path: Optional[Path] = None) -> Path:
    return path or SHARED_KNOWLEDGE_FILE


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_text(value: str, *, max_chars: int, field: str) -> str:
    from src.runtime.peer_messages import has_secret_pattern

    text = (value or "").strip()
    if not text:
        raise ValueError(f"{field} is required.")
    if has_secret_pattern(text):
        raise ValueError(f"{field} contains a secret-like pattern.")
    if len(text) > max_chars:
        text = text[: max_chars - 1] + "..."
    return text


def _clean_list(values: Optional[List[str]], *, max_items: int, field: str) -> List[str]:
    from src.runtime.peer_messages import has_secret_pattern

    result: List[str] = []
    for raw in values or []:
        item = str(raw or "").strip()
        if not item:
            continue
        if has_secret_pattern(item):
            raise ValueError(f"{field} contains a secret-like pattern.")
        if item not in result:
            result.append(item[:120])
        if len(result) >= max_items:
            break
    return result


def _is_expired(record: dict) -> bool:
    expires_at = record.get("expires_at")
    if not expires_at:
        return False
    try:
        dt = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt <= datetime.now(timezone.utc)
    except Exception:
        return True


def load_shared_knowledge(path: Optional[Path] = None) -> Dict[str, dict]:
    path = _store_path(path)
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {}


def save_shared_knowledge(data: Dict[str, dict], path: Optional[Path] = None) -> None:
    path = _store_path(path)
    tmp = path.with_suffix(".tmp")
    with _STORE_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def create_knowledge_record(
    *,
    title: str,
    summary: str,
    owner_instance_id: str,
    origin_instance_id: str,
    origin_user_id: str = "local:owner",
    tags: Optional[List[str]] = None,
    confidence: float = 0.8,
    expires_at: Optional[str] = None,
    source_refs: Optional[List[str]] = None,
    visibility: str = "private",
    shared_with_peer_id: Optional[str] = None,
) -> dict:
    if visibility not in VALID_VISIBILITIES:
        raise ValueError(f"Invalid visibility {visibility!r}.")
    if visibility == "shared_with_peer" and not shared_with_peer_id:
        raise ValueError("shared_with_peer_id is required for shared_with_peer visibility.")
    if not owner_instance_id:
        raise ValueError("owner_instance_id is required.")
    if not origin_instance_id:
        raise ValueError("origin_instance_id is required.")

    conf = max(0.0, min(1.0, float(confidence)))
    return {
        "knowledge_id": uuid.uuid4().hex,
        "title": _clean_text(title, max_chars=_MAX_TITLE_CHARS, field="title"),
        "summary": _clean_text(summary, max_chars=_MAX_SUMMARY_CHARS, field="summary"),
        "owner_instance_id": owner_instance_id,
        "origin_instance_id": origin_instance_id,
        "origin_user_id": origin_user_id or "local:owner",
        "visibility": visibility,
        "shared_with_peer_id": shared_with_peer_id,
        "tags": _clean_list(tags, max_items=_MAX_TAGS, field="tags"),
        "confidence": conf,
        "expires_at": expires_at,
        "source_refs": _clean_list(source_refs, max_items=_MAX_REFS, field="source_refs"),
        "content_hash": _fingerprint(title, summary),
        "created_at": _now(),
        "updated_at": _now(),
        "revoked_at": None,
        "imported_memory_id": None,
        "imported_at": None,
        "dismissed_at": None,
        "dismiss_reason": None,
    }


def add_knowledge(record: dict, path: Optional[Path] = None) -> dict:
    data = load_shared_knowledge(path)
    data[record["knowledge_id"]] = record
    save_shared_knowledge(data, path)
    return record


def share_knowledge(knowledge_id: str, peer_id: str, path: Optional[Path] = None) -> dict:
    data = load_shared_knowledge(path)
    record = data.get(knowledge_id)
    if not record:
        raise KeyError(knowledge_id)
    if _is_expired(record):
        raise ValueError("Knowledge record is expired.")
    record["visibility"] = "shared_with_peer"
    record["shared_with_peer_id"] = peer_id
    record["revoked_at"] = None
    record["updated_at"] = _now()
    data[knowledge_id] = record
    save_shared_knowledge(data, path)
    return record


def revoke_knowledge(knowledge_id: str, path: Optional[Path] = None) -> dict:
    data = load_shared_knowledge(path)
    record = data.get(knowledge_id)
    if not record:
        raise KeyError(knowledge_id)
    record["visibility"] = "private"
    record["shared_with_peer_id"] = None
    record["revoked_at"] = _now()
    record["updated_at"] = _now()
    data[knowledge_id] = record
    save_shared_knowledge(data, path)
    return record


def list_knowledge_for_peer(peer_id: str, path: Optional[Path] = None) -> List[dict]:
    data = load_shared_knowledge(path)
    return [
        public_knowledge_view(record)
        for record in data.values()
        if record.get("visibility") == "shared_with_peer"
        and record.get("shared_with_peer_id") == peer_id
        and not record.get("revoked_at")
        and not _is_expired(record)
    ]


def public_knowledge_view(record: dict) -> dict:
    allowed = {
        "knowledge_id",
        "title",
        "summary",
        "owner_instance_id",
        "origin_instance_id",
        "origin_user_id",
        "visibility",
        "shared_with_peer_id",
        "tags",
        "confidence",
        "expires_at",
        "source_refs",
        "content_hash",
        "created_at",
        "updated_at",
        "revoked_at",
        "imported_memory_id",
        "imported_at",
        "dismissed_at",
        "dismiss_reason",
    }
    return {k: v for k, v in record.items() if k in allowed}


def assess_import_candidate(
    record: dict,
    *,
    existing_records: Optional[Dict[str, dict]] = None,
    min_confidence: float = _MIN_IMPORT_CONFIDENCE,
) -> dict:
    """Return a conservative import decision for one shared-knowledge record."""
    reasons: List[str] = []
    duplicate_of: Optional[str] = None

    if record.get("imported_memory_id"):
        reasons.append("already_imported")
    if record.get("dismissed_at"):
        reasons.append("dismissed")
    if record.get("revoked_at"):
        reasons.append("revoked")
    if _is_expired(record):
        reasons.append("expired")

    try:
        confidence = float(record.get("confidence") or 0.0)
    except (TypeError, ValueError):
        confidence = 0.0
    if confidence < min_confidence:
        reasons.append("low_confidence")

    current_hash = record.get("content_hash") or _fingerprint(
        str(record.get("title") or ""),
        str(record.get("summary") or ""),
    )
    for other_id, other in (existing_records or {}).items():
        if other_id == record.get("knowledge_id"):
            continue
        other_hash = other.get("content_hash") or _fingerprint(
            str(other.get("title") or ""),
            str(other.get("summary") or ""),
        )
        if current_hash and current_hash == other_hash:
            duplicate_of = other.get("knowledge_id") or other_id
            reasons.append("duplicate")
            break

    return {
        "knowledge_id": record.get("knowledge_id"),
        "import_recommended": not reasons,
        "score": round(confidence, 3),
        "reasons": reasons,
        "duplicate_of": duplicate_of,
    }


def list_import_candidates(path: Optional[Path] = None) -> List[dict]:
    """List peer-origin records that can be reviewed for explicit import."""
    data = load_shared_knowledge(path)
    items: List[dict] = []
    for record in data.values():
        if record.get("origin_instance_id") == record.get("owner_instance_id"):
            continue
        view = public_knowledge_view(record)
        view["assessment"] = assess_import_candidate(record, existing_records=data)
        items.append(view)
    items.sort(key=lambda r: (r.get("imported_at") or "", r.get("created_at") or ""), reverse=True)
    return items


def import_knowledge_to_memory(memory: Any, record: dict) -> Optional[str]:
    if _is_expired(record):
        raise ValueError("Knowledge record is expired.")
    content = (
        "[Shared knowledge]\n"
        f"title: {record.get('title')}\n"
        f"origin_instance_id: {record.get('origin_instance_id')}\n"
        f"origin_user_id: {record.get('origin_user_id')}\n"
        f"confidence: {record.get('confidence')}\n"
        f"tags: {', '.join(record.get('tags') or [])}\n\n"
        f"{record.get('summary')}"
    )
    metadata = {
        "source": "peer",
        "knowledge_id": record.get("knowledge_id"),
        "origin_instance_id": record.get("origin_instance_id"),
        "origin_user_id": record.get("origin_user_id"),
        "confidence": record.get("confidence"),
        "visibility": "private",
    }
    if getattr(memory, "vector_store", None) is not None:
        return memory.vector_store.add(
            content,
            memory_type="semantic",
            importance=max(0.5, float(record.get("confidence") or 0.5)),
            metadata=metadata,
        )
    return memory.remember(content, memory_type="semantic", importance=0.7)


def mark_imported(
    knowledge_id: str,
    memory_id: Optional[str],
    path: Optional[Path] = None,
) -> dict:
    data = load_shared_knowledge(path)
    record = data.get(knowledge_id)
    if not record:
        raise KeyError(knowledge_id)
    record["imported_memory_id"] = memory_id
    record["imported_at"] = _now()
    record["updated_at"] = _now()
    data[knowledge_id] = record
    save_shared_knowledge(data, path)
    return record


def dismiss_knowledge(
    knowledge_id: str,
    reason: str = "",
    path: Optional[Path] = None,
) -> dict:
    data = load_shared_knowledge(path)
    record = data.get(knowledge_id)
    if not record:
        raise KeyError(knowledge_id)
    record["dismissed_at"] = _now()
    record["dismiss_reason"] = str(reason or "")[:240]
    record["updated_at"] = _now()
    data[knowledge_id] = record
    save_shared_knowledge(data, path)
    return record
