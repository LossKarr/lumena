"""Runtime settings for Document Studio capacity knobs.

Hard integrity ceilings remain owned by delivery receipts and bundles. These
settings may only reduce those ceilings, never weaken them.
"""
from __future__ import annotations

from dataclasses import dataclass
import os

from .delivery_receipt import MAX_DELIVERY_DOCUMENTS
from .document_delivery_bundle import MAX_BUNDLE_DOCUMENTS


def _bounded_env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(str(os.getenv(name, default)).strip())
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


@dataclass(frozen=True)
class DocumentSettings:
    batch_size: int
    workflow_max_documents: int


def get_document_settings() -> DocumentSettings:
    """Read hot-reloadable document capacity settings with hard safe caps."""
    return DocumentSettings(
        batch_size=_bounded_env_int(
            "LUMENA_DOCUMENT_BATCH_SIZE", MAX_DELIVERY_DOCUMENTS,
            1, MAX_DELIVERY_DOCUMENTS,
        ),
        workflow_max_documents=_bounded_env_int(
            "LUMENA_DOCUMENT_WORKFLOW_MAX_DOCUMENTS", MAX_BUNDLE_DOCUMENTS,
            1, MAX_BUNDLE_DOCUMENTS,
        ),
    )


__all__ = ["DocumentSettings", "get_document_settings"]
