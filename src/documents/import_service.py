"""Unified import boundary for local, uploaded and downloaded documents."""
from __future__ import annotations

import os
from pathlib import Path
import re
import shutil
import tempfile
from typing import Any
import zipfile

from .document_library import DocumentLibrary
from .document_security import SecurityReport, inspect_document, sanitize_document_filename
from .provenance import DocumentRecord


class DocumentImportService:
    def __init__(self, library_root: Path, library: DocumentLibrary):
        self.library_root = Path(library_root)
        self.files_root = self.library_root / "files"
        self.files_root.mkdir(parents=True, exist_ok=True)
        self.library = library

    def import_file(
        self,
        source: Path,
        *,
        source_kind: str,
        source_uri: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[DocumentRecord, bool]:
        report = inspect_document(Path(source))
        existing = self.library.find_by_hash(report.sha256)
        if existing:
            return existing, True
        safe_name = sanitize_document_filename(Path(source).name)
        target_dir = self.files_root / report.sha256[:2] / report.sha256[2:14]
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / safe_name
        self._copy_atomic(Path(source), target)
        content = self._extract_text(target, report)
        record = DocumentRecord.create(
            sha256=report.sha256,
            filename=safe_name,
            path=str(target),
            format=report.format,
            mime_type=report.mime_type,
            size=report.size,
            source_kind=str(source_kind or "local"),
            source_uri=str(source_uri or ""),
            title=Path(safe_name).stem.replace("_", " "),
            content_text=content,
            metadata={**(metadata or {}), "security_warnings": list(report.warnings)},
        )
        self.library.upsert(record)
        return record, False

    def import_directory(
        self, directory: Path, *, recursive: bool = False, max_files: int = 500,
        source_kind: str = "historical_scan",
    ) -> dict[str, Any]:
        root = Path(directory).resolve()
        if not root.is_dir():
            raise NotADirectoryError(root)
        maximum = max(1, min(int(max_files), 5000))
        iterator = root.rglob("*") if recursive else root.glob("*")
        imported = []
        skipped = []
        errors = []
        for path in iterator:
            if not path.is_file() or path.is_symlink():
                continue
            if len(imported) + len(skipped) + len(errors) >= maximum:
                break
            try:
                record, duplicate = self.import_file(
                    path, source_kind=source_kind, source_uri=str(path)
                )
                (skipped if duplicate else imported).append(record.id)
            except Exception as exc:
                errors.append({"path": str(path), "error": str(exc)})
        return {"imported": imported, "duplicates": skipped, "errors": errors, "limit": maximum}

    @staticmethod
    def _copy_atomic(source: Path, target: Path) -> None:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".part", dir=str(target.parent))
        os.close(fd)
        tmp = Path(tmp_name)
        try:
            shutil.copyfile(source, tmp)
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)

    @staticmethod
    def _extract_text(path: Path, report: SecurityReport, *, max_chars: int = 2_000_000) -> str:
        if report.format in {"txt", "md", "csv", "html"}:
            return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
        if report.format in {"odt", "ods"}:
            try:
                import xml.etree.ElementTree as ET

                with zipfile.ZipFile(path) as archive:
                    root = ET.fromstring(archive.read("content.xml"))
                text = " ".join(part.strip() for part in root.itertext() if part.strip())
                return text[:max_chars]
            except Exception:
                return ""
        if report.format == "rtf":
            raw = path.read_text(encoding="latin-1", errors="replace")
            raw = re.sub(r"\\'[0-9a-fA-F]{2}", " ", raw)
            raw = re.sub(r"\\[a-zA-Z]+-?\d* ?", " ", raw)
            return re.sub(r"[{}]", "", raw)[:max_chars].strip()
        try:
            from src.perception.document_reader import DocumentReader

            chunks = DocumentReader().read(path)
            content = "\n\n".join(chunk.content for chunk in chunks)[:max_chars]
            return content
        except Exception:
            return ""
