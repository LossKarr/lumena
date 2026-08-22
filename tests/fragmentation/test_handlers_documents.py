"""
Tests unitaires pour handlers/documents.py — 5 handlers.

Convention: chaque handler reçoit un HandlerContext et retourne HandlerResult.
Le hub est mocké via ctx._document_hub.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.documents import (
    create_pdf_handler,
    create_docx_handler,
    create_xlsx_handler,
    create_pptx_handler,
    read_document_handler,
    get_documents_handler_defs,
    normalize_pdf_content,
)

# Note: create_invoice_pdf_handler also exists but is tested via handler_defs


@pytest.fixture
def ctx(tmp_path):
    c = HandlerContext.for_testing(lumena_root=tmp_path, runtime_root=tmp_path / "workspace")
    c._document_hub = MagicMock()
    return c


# ─── create_pdf ────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_pdf_success(ctx):
    ctx._document_hub.create_pdf.return_value = {
        "success": True, "filename": "test.pdf", "path": "/data/test.pdf", "pages": 1
    }
    r = await create_pdf_handler(ctx, filename="test.pdf", title="Mon Rapport", content="Hello World")
    assert r.success
    assert "test.pdf" in r.output


@pytest.mark.asyncio
async def test_create_pdf_failure(ctx):
    ctx._document_hub.create_pdf.return_value = {
        "success": False, "error": "encoding error"
    }
    r = await create_pdf_handler(ctx, filename="test.pdf", title="T", content="xyz")
    assert not r.success


def test_normalize_pdf_content_preserves_historical_text_exactly():
    content = "# Titre\n\nTexte deja formate."
    assert normalize_pdf_content(content) == content


def test_normalize_pdf_content_accepts_structured_blocks():
    content = [
        {"type": "heading", "level": 2, "text": "Synthese"},
        {"type": "paragraph", "text": "Resultats du mois."},
        {"type": "list", "items": ["Ventes: 12", "Marge: 30 %"]},
    ]
    assert normalize_pdf_content(content) == (
        "## Synthese\n\nResultats du mois.\n\n- Ventes: 12\n\n- Marge: 30 %"
    )


@pytest.mark.asyncio
async def test_create_pdf_structured_content_is_normalized_before_hub(ctx):
    ctx._document_hub.create_pdf.return_value = {
        "success": True, "filename": "test.pdf", "path": "/data/test.pdf", "pages": 1,
    }
    result = await create_pdf_handler(
        ctx,
        filename="test.pdf",
        title="Rapport",
        content=[
            {"type": "heading", "text": "Rapport"},
            {"type": "paragraph", "text": "OK"},
        ],
    )
    assert result.success
    assert ctx._document_hub.create_pdf.call_args.kwargs["content"] == "# Rapport\n\nOK"


# ─── create_docx ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_docx_success(ctx):
    ctx._document_hub.create_docx.return_value = {
        "success": True, "filename": "test.docx", "path": "/data/test.docx"
    }
    r = await create_docx_handler(ctx, filename="test.docx", title="Mon Doc", content="Hello")
    assert r.success
    assert "test.docx" in r.output


@pytest.mark.asyncio
async def test_create_docx_failure(ctx):
    ctx._document_hub.create_docx.return_value = {
        "success": False, "error": "write error"
    }
    r = await create_docx_handler(ctx, filename="test.docx", title="T", content="xyz")
    assert not r.success


# ─── create_xlsx ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_xlsx_success(ctx):
    ctx._document_hub.create_xlsx.return_value = {
        "success": True, "filename": "test.xlsx", "path": "/data/test.xlsx", "rows": 5
    }
    r = await create_xlsx_handler(ctx, filename="test.xlsx", sheets='[{"name":"Sheet1","headers":["A","B"],"rows":[[1,2]]}]')
    assert r.success
    assert "test.xlsx" in r.output


@pytest.mark.asyncio
async def test_create_xlsx_failure(ctx):
    ctx._document_hub.create_xlsx.return_value = {
        "success": False, "error": "data error"
    }
    r = await create_xlsx_handler(ctx, filename="test.xlsx", sheets='[]')
    assert not r.success


# ─── create_pptx ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_create_pptx_success(ctx):
    ctx._document_hub.create_pptx.return_value = {
        "success": True, "filename": "test.pptx", "path": "/data/test.pptx", "slides": 3
    }
    r = await create_pptx_handler(ctx, filename="test.pptx", title="Ma Présentation", slides='[{"title":"Intro","content":"Hi"}]')
    assert r.success
    assert "test.pptx" in r.output


@pytest.mark.asyncio
async def test_create_pptx_failure(ctx):
    ctx._document_hub.create_pptx.return_value = {
        "success": False, "error": "template error"
    }
    r = await create_pptx_handler(ctx, filename="test.pptx", title="T", slides='[]')
    assert not r.success


# ─── read_document ─────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_read_document_success(ctx):
    ctx._document_hub.read_document.return_value = {
        "success": True, "path": "/data/test.pdf",
        "type": "pdf", "content": "Page content here"
    }
    r = await read_document_handler(ctx, path="/data/test.pdf")
    assert r.success
    assert "Page content here" in r.output


@pytest.mark.asyncio
async def test_read_document_failure(ctx):
    ctx._document_hub.read_document.return_value = {
        "success": False, "error": "file not found"
    }
    r = await read_document_handler(ctx, path="/data/nope.pdf")
    assert not r.success


# ─── HandlerDefs ───────────────────────────────────────────────────────────

def test_handler_defs_count():
    defs = get_documents_handler_defs()
    assert len(defs) >= 8


def test_handler_defs_names():
    defs = get_documents_handler_defs()
    names = [d.name for d in defs]
    assert len(names) == len(set(names))


def test_handler_defs_expected_names():
    expected = {
        "create_pdf", "create_invoice_pdf", "create_docx", "create_xlsx",
        "create_pptx", "read_document", "generate_chart", "create_meeting_report",
    }
    defs = get_documents_handler_defs()
    actual = {d.name for d in defs}
    assert expected <= actual


def test_handler_defs_have_handlers():
    for d in get_documents_handler_defs():
        assert callable(d.handler), f"{d.name} handler not callable"
