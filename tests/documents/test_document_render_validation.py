from __future__ import annotations

import asyncio

from PIL import Image
import pytest
from pypdf import PdfReader, PdfWriter

from src.documents.document_validation import validate_document_render
from src.documents.template_renderer import TemplateRenderer


@pytest.mark.asyncio
async def test_generated_pdf_with_real_thumbnail_is_render_verified(tmp_path):
    pdf = tmp_path / "document.pdf"
    await asyncio.to_thread(
        TemplateRenderer().render_pdf,
        "<h1>Facture professionnelle</h1><p>Montant total 420 euros</p>",
        pdf,
    )
    thumbnail = tmp_path / "document.webp"
    Image.new("RGB", (420, 594), "white").save(thumbnail)
    image = Image.open(thumbnail)
    pixels = image.load()
    for x in range(40, 380):
        for y in range(80, 120):
            pixels[x, y] = (20, 20, 20)
    image.save(thumbnail)
    image.close()

    proof = validate_document_render(
        pdf, thumbnail, output_format="pdf", logo_id="logo_1"
    )

    assert proof["verified"] is True
    assert proof["status"] == "render_verified"
    assert proof["page_count"] >= 1
    assert proof["text_chars"] >= 10
    assert proof["non_blank"] is True
    assert proof["logo_id"] == "logo_1"


def test_blank_thumbnail_is_not_certified(tmp_path):
    pdf = tmp_path / "broken.pdf"
    pdf.write_bytes(b"%PDF-invalid")
    thumbnail = tmp_path / "blank.webp"
    Image.new("RGB", (420, 594), "white").save(thumbnail)

    proof = validate_document_render(pdf, thumbnail, output_format="pdf")

    assert proof["verified"] is False
    assert proof["status"] == "render_failed"
    assert "thumbnail_blank" in proof["errors"]


@pytest.mark.asyncio
async def test_trailing_blank_pdf_page_is_not_counted_or_certified(tmp_path):
    first_page = tmp_path / "first-page.pdf"
    await asyncio.to_thread(
        TemplateRenderer().render_pdf,
        "<h1>Contrat professionnel</h1><p>Contenu substantiel et vérifiable.</p>",
        first_page,
    )
    source_reader = PdfReader(first_page)
    writer = PdfWriter()
    writer.add_page(source_reader.pages[0])
    writer.add_blank_page(
        width=source_reader.pages[0].mediabox.width,
        height=source_reader.pages[0].mediabox.height,
    )
    pdf = tmp_path / "trailing-blank.pdf"
    with pdf.open("wb") as stream:
        writer.write(stream)

    thumbnail = tmp_path / "thumbnail.webp"
    Image.new("RGB", (420, 594), "white").save(thumbnail)
    with Image.open(thumbnail) as image:
        pixels = image.load()
        for x in range(40, 380):
            for y in range(80, 120):
                pixels[x, y] = (20, 20, 20)
        image.save(thumbnail)

    proof = validate_document_render(pdf, thumbnail, output_format="pdf")

    assert proof["verified"] is False
    assert proof["physical_page_count"] == 2
    assert proof["page_count"] == 1
    assert proof["blank_pages"] == [2]
    assert "blank_pdf_pages:2" in proof["errors"]
