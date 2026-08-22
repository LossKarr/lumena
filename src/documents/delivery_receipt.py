"""Persistent, content-addressed receipts for exact document deliveries."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

from src.utils.persistence import atomic_write_json

from .delivery_manifest import DocumentDeliveryProof


_SCHEMA = "lumena.document-delivery.v1"
_RECEIPT_RE = re.compile(r"^doclot_[0-9a-f]{24}$")
MAX_DELIVERY_DOCUMENTS = 30


def _receipt_body(
    proofs: Iterable[DocumentDeliveryProof], *, requested_count: int,
) -> dict[str, Any]:
    rows = [proof.to_dict() for proof in proofs]
    if not rows or len(rows) > MAX_DELIVERY_DOCUMENTS:
        raise ValueError(
            f"a delivery receipt must contain between 1 and {MAX_DELIVERY_DOCUMENTS} documents"
        )
    if requested_count < len(rows) or requested_count > MAX_DELIVERY_DOCUMENTS:
        raise ValueError("requested_count is inconsistent with the delivered documents")
    return {
        "schema": _SCHEMA,
        "requested_count": int(requested_count),
        "documents": rows,
    }


def _receipt_id(body: dict[str, Any]) -> str:
    canonical = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return "doclot_" + hashlib.sha256(canonical).hexdigest()[:24]


def save_delivery_receipt(
    directory: Path,
    proofs: Iterable[DocumentDeliveryProof],
    *,
    requested_count: int,
) -> dict[str, Any]:
    """Persist an idempotent receipt and return its verified payload."""
    body = _receipt_body(proofs, requested_count=requested_count)
    receipt_id = _receipt_id(body)
    path = Path(directory) / f"{receipt_id}.json"
    if path.exists():
        return load_delivery_receipt(directory, receipt_id)
    payload = {
        **body,
        "id": receipt_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    atomic_write_json(path, payload)
    return payload


def load_delivery_receipt(directory: Path, receipt_id: str) -> dict[str, Any]:
    """Load a receipt and reject traversal, corruption, or content tampering."""
    wanted = str(receipt_id or "").strip().lower()
    if not _RECEIPT_RE.fullmatch(wanted):
        raise ValueError("invalid document delivery receipt id")
    path = Path(directory) / f"{wanted}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise KeyError(f"document delivery receipt not found: {wanted}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable document delivery receipt: {wanted}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("documents"), list):
        raise ValueError("malformed document delivery receipt")
    body = {
        "schema": raw.get("schema"),
        "requested_count": raw.get("requested_count"),
        "documents": raw.get("documents"),
    }
    if body["schema"] != _SCHEMA or _receipt_id(body) != wanted:
        raise ValueError("document delivery receipt integrity check failed")
    if len(body["documents"]) < 1 or len(body["documents"]) > MAX_DELIVERY_DOCUMENTS:
        raise ValueError("invalid document count in delivery receipt")
    return raw


def build_open_delivery_final(payload: dict[str, Any]) -> str:
    """Build a deterministic user result from the exact open payload."""
    requested = int(payload.get("requested") or 0)
    opened = int(payload.get("opened") or 0)
    failed = int(payload.get("failed") or 0)
    receipt_id = str(payload.get("receipt_id") or "")
    lines = [
        f"C'est ouvert. {opened}/{requested} document(s) du lot `{receipt_id}` ont ete ouverts."
    ]
    for item in payload.get("files") or []:
        filename = item.get("filename") or Path(str(item.get("path") or "")).name
        path = str(item.get("path") or "").strip()
        try:
            page_count = max(0, int(item.get("page_count") or 0))
        except (TypeError, ValueError):
            page_count = 0
        pages = f" - {page_count} page(s)" if page_count else ""
        location = f" - `{path}`" if path else ""
        lines.append(f"- `{filename}`{pages}{location}")
    if failed:
        lines.append(
            f"Attention: {failed} fichier(s) n'ont pas pu etre ouverts ou ont change depuis la livraison."
        )
    return "\n".join(lines)


__all__ = [
    "MAX_DELIVERY_DOCUMENTS",
    "build_open_delivery_final",
    "load_delivery_receipt",
    "save_delivery_receipt",
]
