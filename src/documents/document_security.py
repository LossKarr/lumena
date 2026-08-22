"""Security checks for untrusted office and document files."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import mimetypes
from pathlib import Path, PurePosixPath
import re
import zipfile


class DocumentSecurityError(ValueError):
    pass


ALLOWED_EXTENSIONS = frozenset(
    {".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".txt", ".md", ".html", ".htm", ".rtf", ".odt", ".ods"}
)
MACRO_EXTENSIONS = frozenset({".docm", ".xlsm", ".pptm", ".dotm", ".xltm", ".potm"})
MIME_BY_FORMAT = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "odt": "application/vnd.oasis.opendocument.text",
    "ods": "application/vnd.oasis.opendocument.spreadsheet",
    "rtf": "application/rtf",
    "csv": "text/csv",
    "txt": "text/plain",
    "md": "text/markdown",
    "html": "text/html",
}


@dataclass(frozen=True)
class SecurityReport:
    path: str
    format: str
    mime_type: str
    size: int
    sha256: str
    warnings: tuple[str, ...] = field(default_factory=tuple)


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def sanitize_document_filename(filename: str) -> str:
    name = Path(str(filename or "document")).name
    name = re.sub(r"[^\w.()\- ]", "_", name, flags=re.UNICODE).strip(" .")
    if not name:
        name = "document"
    return name[:180]


def inspect_document(
    path: Path,
    *,
    max_bytes: int = 100 * 1024 * 1024,
    max_entries: int = 10_000,
    max_uncompressed_bytes: int = 300 * 1024 * 1024,
) -> SecurityReport:
    target = Path(path)
    if not target.is_file():
        raise DocumentSecurityError("document not found")
    ext = target.suffix.lower()
    if ext in MACRO_EXTENSIONS:
        raise DocumentSecurityError("macro-enabled Office documents are quarantined")
    if ext not in ALLOWED_EXTENSIONS:
        raise DocumentSecurityError(f"unsupported document extension: {ext or '(none)'}")
    size = target.stat().st_size
    if size <= 0:
        raise DocumentSecurityError("empty document")
    if size > max_bytes:
        raise DocumentSecurityError(f"document exceeds {max_bytes} bytes")

    head = target.read_bytes()[:8192]
    detected = _detect_format(target, head)
    expected = "html" if ext in {".html", ".htm"} else ext.lstrip(".")
    if detected != expected:
        raise DocumentSecurityError(f"extension/content mismatch: expected {expected}, detected {detected}")

    warnings: list[str] = []
    if zipfile.is_zipfile(target):
        warnings.extend(
            _inspect_archive(
                target,
                max_entries=max_entries,
                max_uncompressed_bytes=max_uncompressed_bytes,
            )
        )
    if detected == "pdf":
        try:
            from pypdf import PdfReader

            if PdfReader(str(target)).is_encrypted:
                warnings.append("encrypted_pdf")
        except Exception as exc:
            raise DocumentSecurityError(f"invalid PDF: {exc}") from exc
    mime = MIME_BY_FORMAT.get(detected) or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    return SecurityReport(
        path=str(target),
        format=detected,
        mime_type=mime,
        size=size,
        sha256=sha256_file(target),
        warnings=tuple(sorted(set(warnings))),
    )


def _detect_format(path: Path, head: bytes) -> str:
    if head.startswith(b"%PDF-"):
        return "pdf"
    if head.startswith(b"{\\rtf"):
        return "rtf"
    if head.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(path) as archive:
                names = set(archive.namelist())
                if "word/document.xml" in names:
                    return "docx"
                if "xl/workbook.xml" in names:
                    return "xlsx"
                if "ppt/presentation.xml" in names:
                    return "pptx"
                if "mimetype" in names:
                    value = archive.read("mimetype").decode("ascii", errors="ignore")
                    if value.endswith("opendocument.text"):
                        return "odt"
                    if value.endswith("opendocument.spreadsheet"):
                        return "ods"
        except Exception as exc:
            raise DocumentSecurityError(f"invalid document archive: {exc}") from exc
        return "zip"
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n").lower()
    if stripped.startswith((b"<!doctype html", b"<html", b"<head", b"<body")):
        return "html"
    ext = path.suffix.lower()
    if ext == ".csv":
        return "csv"
    if ext == ".md":
        return "md"
    if ext == ".txt":
        return "txt"
    return "unknown"


def _inspect_archive(path: Path, *, max_entries: int, max_uncompressed_bytes: int) -> list[str]:
    warnings: list[str] = []
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        if len(infos) > max_entries:
            raise DocumentSecurityError("document archive has too many entries")
        total = 0
        for info in infos:
            pure = PurePosixPath(info.filename)
            if pure.is_absolute() or ".." in pure.parts:
                raise DocumentSecurityError("document archive contains path traversal")
            total += max(0, info.file_size)
            if info.file_size > 10 * 1024 * 1024 and info.compress_size > 0:
                if info.file_size / info.compress_size > 200:
                    raise DocumentSecurityError("suspicious archive compression ratio")
            lower = info.filename.lower()
            if lower.endswith("vbaproject.bin") or "/macros/" in lower:
                raise DocumentSecurityError("Office macros are not allowed")
            if lower.endswith(".rels"):
                content = archive.read(info).decode("utf-8", errors="ignore")
                if 'TargetMode="External"' in content or "TargetMode='External'" in content:
                    warnings.append("external_relationships")
        if total > max_uncompressed_bytes:
            raise DocumentSecurityError("document archive expands beyond the safe limit")
    return warnings

