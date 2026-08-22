"""Transactional edits around historical DocumentHub operations."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from typing import Any

from .document_library import DocumentLibrary
from .document_security import inspect_document
from .import_service import DocumentImportService
from .provenance import DocumentTransformation


SUPPORTED_EDIT_OPERATIONS = {
    "docx": {"replace_text", "add_paragraph", "delete_paragraph", "add_image", "set_header", "set_footer", "replace_in_table"},
    "xlsx": {"set_cell", "set_formula", "add_row", "delete_row", "add_sheet", "rename_sheet"},
    "pptx": {"replace_text", "add_slide", "delete_slide", "add_image"},
}


@dataclass(frozen=True)
class EditPreview:
    document_id: str
    format: str
    operations: tuple[dict[str, Any], ...]
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class DocumentEditService:
    def __init__(
        self,
        versions_root: Path,
        *,
        library: DocumentLibrary,
        importer: DocumentImportService,
        document_hub: Any,
    ):
        self.versions_root = Path(versions_root)
        self.versions_root.mkdir(parents=True, exist_ok=True)
        self.library = library
        self.importer = importer
        self.document_hub = document_hub

    def preview(self, document_id: str, operations: list[dict[str, Any]]) -> EditPreview:
        record = self._record(document_id)
        allowed = SUPPORTED_EDIT_OPERATIONS.get(record.format)
        if allowed is None:
            raise ValueError(f"Transactional editing is not supported for {record.format}")
        if not isinstance(operations, list) or not operations:
            raise ValueError("operations must be a non-empty list")
        normalized = []
        warnings = []
        for index, operation in enumerate(operations, 1):
            if not isinstance(operation, dict):
                raise ValueError(f"operation {index} must be an object")
            name = str(operation.get("op", ""))
            if name not in allowed:
                raise ValueError(f"operation {name or index} is not supported for {record.format}")
            normalized.append(dict(operation))
            if name in {"delete_paragraph", "delete_row", "delete_slide"}:
                warnings.append(f"operation {index} removes content")
        return EditPreview(record.id, record.format, tuple(normalized), tuple(warnings))

    def apply(self, document_id: str, operations: list[dict[str, Any]]) -> dict[str, Any]:
        preview = self.preview(document_id, operations)
        record = self._record(document_id)
        source = Path(record.path)
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        target_dir = self.versions_root / record.id
        target_dir.mkdir(parents=True, exist_ok=True)
        output = target_dir / f"{timestamp}{source.suffix.lower()}"
        method = getattr(self.document_hub, f"edit_{record.format}")
        result = method(str(source), list(preview.operations), str(output))
        if not result.get("success"):
            output.unlink(missing_ok=True)
            raise RuntimeError(result.get("error", "document edit failed"))
        report = inspect_document(output)
        if report.size <= 0:
            output.unlink(missing_ok=True)
            raise RuntimeError("edited document is empty")
        child, _ = self.importer.import_file(
            output,
            source_kind="edited",
            source_uri=f"document:{record.id}",
            metadata={"parent_id": record.id, "operations": list(preview.operations)},
        )
        child.parent_id = record.id
        self.library.upsert(child)
        transformation = DocumentTransformation.create(
            document_id=record.id,
            operation="edit",
            input_sha256=record.sha256,
            output_sha256=child.sha256,
            details={"operations": list(preview.operations), "output_document_id": child.id},
        )
        self.library.add_transformation(transformation)
        return {
            "preview": preview.to_dict(),
            "record": child.to_dict(include_content=False),
            "transformation": asdict(transformation),
            "operations_applied": int(result.get("operations_applied", 0)),
        }

    def _record(self, document_id: str):
        record = self.library.get(document_id)
        if record is None:
            raise KeyError(document_id)
        if not Path(record.path).is_file():
            raise FileNotFoundError(record.path)
        return record
