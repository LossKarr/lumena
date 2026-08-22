"""Explicit conversion matrix with fidelity and loss reporting."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from .document_library import DocumentLibrary
from .import_service import DocumentImportService
from .provenance import DocumentTransformation


CONVERSION_MATRIX: dict[tuple[str, str], dict[str, Any]] = {
    ("docx", "pdf"): {"fidelity": "medium", "losses": ["interactive fields", "tracked changes may be flattened"]},
    ("docx", "html"): {"fidelity": "medium", "losses": ["page layout", "headers and footers"]},
    ("xlsx", "csv"): {"fidelity": "data-only", "losses": ["styles", "charts", "formulas", "all sheets except active"]},
    ("csv", "xlsx"): {"fidelity": "data", "losses": []},
    ("html", "pdf"): {"fidelity": "high", "losses": ["interactive behavior"]},
    ("md", "pdf"): {"fidelity": "semantic", "losses": ["unsupported markdown extensions"]},
    ("md", "docx"): {"fidelity": "semantic", "losses": ["unsupported markdown extensions"]},
    ("odt", "docx"): {"fidelity": "high-with-libreoffice", "losses": ["advanced OpenDocument features"]},
    ("ods", "xlsx"): {"fidelity": "high-with-libreoffice", "losses": ["advanced OpenDocument formulas"]},
    ("pptx", "pdf"): {"fidelity": "high-with-libreoffice", "losses": ["animations", "speaker notes"]},
}


class DocumentConversionService:
    def __init__(self, *, library: DocumentLibrary, importer: DocumentImportService, document_hub: Any):
        self.library = library
        self.importer = importer
        self.document_hub = document_hub

    @staticmethod
    def capabilities() -> list[dict[str, Any]]:
        return [
            {"from": source, "to": target, **details}
            for (source, target), details in sorted(CONVERSION_MATRIX.items())
        ]

    def convert(self, document_id: str, output_format: str) -> dict[str, Any]:
        record = self.library.get(document_id)
        if record is None:
            raise KeyError(document_id)
        target_format = str(output_format).lower().lstrip(".")
        key = (record.format, target_format)
        if key not in CONVERSION_MATRIX:
            raise ValueError(f"Conversion {record.format}->{target_format} not supported")
        source = Path(record.path)
        result = self._convert_with_libreoffice(source, target_format)
        engine = "libreoffice"
        if result is None:
            result = self.document_hub.convert_document(str(source), target_format)
            engine = "native"
        if not result.get("success"):
            raise RuntimeError(result.get("error", "conversion failed"))
        output = Path(result["path"])
        child, _ = self.importer.import_file(
            output, source_kind="converted", source_uri=f"document:{record.id}",
            metadata={"parent_id": record.id, "engine": engine, "conversion": f"{record.format}->{target_format}"},
        )
        child.parent_id = record.id
        self.library.upsert(child)
        transformation = DocumentTransformation.create(
            document_id=record.id, operation="convert", input_sha256=record.sha256,
            output_sha256=child.sha256, details={"engine": engine, "to": target_format, **CONVERSION_MATRIX[key]},
        )
        self.library.add_transformation(transformation)
        return {
            "record": child.to_dict(include_content=False), "engine": engine,
            "guarantees": CONVERSION_MATRIX[key], "transformation": asdict(transformation),
        }

    @staticmethod
    def _convert_with_libreoffice(source: Path, output_format: str) -> dict[str, Any] | None:
        executable = shutil.which("soffice") or shutil.which("libreoffice")
        if not executable or (source.suffix.lower().lstrip("."), output_format) not in {
            ("odt", "docx"), ("ods", "xlsx"), ("pptx", "pdf"), ("docx", "pdf")
        }:
            return None
        out_dir = Path(tempfile.mkdtemp(prefix="lumena-convert-"))
        proc = subprocess.run(
            [executable, "--headless", "--convert-to", output_format, "--outdir", str(out_dir), str(source)],
            capture_output=True, text=True, timeout=120, check=False,
        )
        output = out_dir / f"{source.stem}.{output_format}"
        if proc.returncode != 0 or not output.is_file():
            return {"success": False, "error": (proc.stderr or proc.stdout or "LibreOffice conversion failed").strip()}
        return {"success": True, "path": str(output), "filename": output.name}
