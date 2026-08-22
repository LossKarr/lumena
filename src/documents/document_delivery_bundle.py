"""Tamper-evident delivery bundles spanning several V1 document receipts."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

from src.utils.persistence import atomic_write_json

from .delivery_manifest import DocumentDeliveryProof
from .delivery_receipt import (
    MAX_DELIVERY_DOCUMENTS,
    load_delivery_receipt,
    save_delivery_receipt,
)


_SCHEMA = "lumena.document-delivery-bundle.v1"
_BUNDLE_RE = re.compile(r"^docbundle_[0-9a-f]{24}$")
MAX_BUNDLE_DOCUMENTS = 100


def _canonical_id(body: dict[str, Any]) -> str:
    canonical = json.dumps(
        body, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return "docbundle_" + hashlib.sha256(canonical).hexdigest()[:24]


def _document_identity(row: dict[str, Any]) -> str:
    return str(row.get("document_id") or row.get("sha256") or row.get("path") or "").strip()


def save_delivery_bundle(
    directory: Path,
    receipt_directory: Path,
    receipt_ids: Iterable[str],
    *,
    requested_count: int,
) -> dict[str, Any]:
    """Persist an exact ordered bundle referencing verified V1 receipts."""
    ids = tuple(str(value or "").strip().lower() for value in receipt_ids if str(value or "").strip())
    if not ids:
        raise ValueError("a delivery bundle requires at least one receipt")
    documents: list[dict[str, Any]] = []
    seen: set[str] = set()
    for receipt_id in ids:
        receipt = load_delivery_receipt(receipt_directory, receipt_id)
        for row in receipt["documents"]:
            identity = _document_identity(row)
            if not identity or identity in seen:
                continue
            seen.add(identity)
            documents.append(dict(row))
    if not 1 <= len(documents) <= MAX_BUNDLE_DOCUMENTS:
        raise ValueError(
            f"a delivery bundle must contain between 1 and {MAX_BUNDLE_DOCUMENTS} documents"
        )
    if requested_count != len(documents):
        raise ValueError(
            f"bundle is incomplete: {len(documents)}/{requested_count} unique documents"
        )
    body = {
        "schema": _SCHEMA,
        "requested_count": int(requested_count),
        "receipt_ids": list(ids),
        "documents": documents,
    }
    bundle_id = _canonical_id(body)
    path = Path(directory) / f"{bundle_id}.json"
    if path.exists():
        return load_delivery_bundle(directory, receipt_directory, bundle_id)
    payload = {**body, "id": bundle_id, "created_at": datetime.now(timezone.utc).isoformat()}
    atomic_write_json(path, payload)
    return payload


def load_delivery_bundle(
    directory: Path, receipt_directory: Path, bundle_id: str,
) -> dict[str, Any]:
    """Load a bundle and revalidate both its body and every child receipt."""
    wanted = str(bundle_id or "").strip().lower()
    if not _BUNDLE_RE.fullmatch(wanted):
        raise ValueError("invalid document delivery bundle id")
    path = Path(directory) / f"{wanted}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise KeyError(f"document delivery bundle not found: {wanted}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"unreadable document delivery bundle: {wanted}") from exc
    if not isinstance(raw, dict):
        raise ValueError("malformed document delivery bundle")
    body = {
        "schema": raw.get("schema"),
        "requested_count": raw.get("requested_count"),
        "receipt_ids": raw.get("receipt_ids"),
        "documents": raw.get("documents"),
    }
    if body["schema"] != _SCHEMA or _canonical_id(body) != wanted:
        raise ValueError("document delivery bundle integrity check failed")
    if not isinstance(body["receipt_ids"], list) or not isinstance(body["documents"], list):
        raise ValueError("malformed document delivery bundle")
    rebuilt: list[dict[str, Any]] = []
    seen: set[str] = set()
    for receipt_id in body["receipt_ids"]:
        receipt = load_delivery_receipt(receipt_directory, str(receipt_id))
        for row in receipt["documents"]:
            identity = _document_identity(row)
            if identity and identity not in seen:
                seen.add(identity)
                rebuilt.append(dict(row))
    if rebuilt != body["documents"] or len(rebuilt) != int(body["requested_count"] or 0):
        raise ValueError("document delivery bundle child receipt check failed")
    return raw


def save_delivery_reference(
    root: Path,
    proofs: Iterable[DocumentDeliveryProof],
    *,
    requested_count: int,
) -> dict[str, Any]:
    """Persist one V1 receipt or a bundle of V1 receipts for an exact manifest."""
    rows = list(proofs)
    if len(rows) != requested_count:
        raise ValueError(f"delivery is incomplete: {len(rows)}/{requested_count}")
    receipt_dir = Path(root) / "delivery_receipts"
    if requested_count <= MAX_DELIVERY_DOCUMENTS:
        return save_delivery_receipt(receipt_dir, rows, requested_count=requested_count)
    receipt_ids = []
    for offset in range(0, len(rows), MAX_DELIVERY_DOCUMENTS):
        chunk = rows[offset:offset + MAX_DELIVERY_DOCUMENTS]
        receipt = save_delivery_receipt(receipt_dir, chunk, requested_count=len(chunk))
        receipt_ids.append(str(receipt["id"]))
    return save_delivery_bundle(
        Path(root) / "delivery_bundles",
        receipt_dir,
        receipt_ids,
        requested_count=requested_count,
    )


def load_delivery_reference(root: Path, reference_id: str) -> dict[str, Any]:
    """Load either a historical V1 receipt or a multi-receipt bundle."""
    wanted = str(reference_id or "").strip().lower()
    if wanted.startswith("docbundle_"):
        return load_delivery_bundle(
            Path(root) / "delivery_bundles",
            Path(root) / "delivery_receipts",
            wanted,
        )
    return load_delivery_receipt(Path(root) / "delivery_receipts", wanted)


__all__ = [
    "MAX_BUNDLE_DOCUMENTS",
    "load_delivery_bundle",
    "load_delivery_reference",
    "save_delivery_bundle",
    "save_delivery_reference",
]
