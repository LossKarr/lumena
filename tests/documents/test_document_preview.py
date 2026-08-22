from pathlib import Path
import asyncio

import pytest

from src.documents.document_library import DocumentLibrary
from src.documents.document_preview import DocumentPreviewService
from src.documents.import_service import DocumentImportService
from src.documents.template_renderer import TemplateRenderer


@pytest.mark.asyncio
async def test_pdf_thumbnail_is_real_webp(tmp_path):
    source = tmp_path / "sample.pdf"
    await asyncio.to_thread(TemplateRenderer().render_pdf, "<h1>Document réel</h1><p>Preview</p>", source)
    library = DocumentLibrary(tmp_path / "documents.sqlite")
    importer = DocumentImportService(tmp_path / "library", library)
    record, _ = importer.import_file(source, source_kind="generated")
    result = await DocumentPreviewService(tmp_path / "previews", library).thumbnail(record.id)
    assert result and result.read_bytes().startswith(b"RIFF")


@pytest.mark.asyncio
async def test_unsupported_format_has_no_fake_preview(tmp_path):
    source = tmp_path / "notes.txt"; source.write_text("texte", encoding="utf-8")
    library = DocumentLibrary(tmp_path / "documents.sqlite")
    importer = DocumentImportService(tmp_path / "library", library)
    record, _ = importer.import_file(source, source_kind="manual")
    assert await DocumentPreviewService(tmp_path / "previews", library).thumbnail(record.id) is None
