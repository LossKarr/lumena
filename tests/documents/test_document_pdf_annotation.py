from __future__ import annotations

from pathlib import Path

from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas

from src.tools.document_hub import DocumentHub


def _make_pdf(path: Path, *, pages: int = 1) -> None:
    canvas = Canvas(str(path))
    for index in range(pages):
        canvas.drawString(72, 760, f"Source page {index + 1}")
        canvas.showPage()
    canvas.save()


def test_annotation_rejects_out_of_range_page_without_output(tmp_path):
    source = tmp_path / "source.pdf"
    output = tmp_path / "invalid.pdf"
    _make_pdf(source)

    result = DocumentHub(tmp_path / "workspace").annotate_pdf(
        str(source),
        [{"type": "text", "page": 1, "text": "SHOULD-NOT-EXIST"}],
        str(output),
    )

    assert result["success"] is False
    assert "hors limites" in result["error"]
    assert "zero-based" in result["error"]
    assert not output.exists()


def test_text_annotation_is_applied_and_extractable(tmp_path):
    source = tmp_path / "source.pdf"
    output = tmp_path / "annotated.pdf"
    _make_pdf(source)

    result = DocumentHub(tmp_path / "workspace").annotate_pdf(
        str(source),
        [{"type": "text", "page": 0, "text": "AGENCE-LYON-2042", "x": 72, "y": 700}],
        str(output),
    )

    assert result["success"] is True
    assert result["annotations_count"] == 1
    assert output.exists()
    text = "\n".join(page.extract_text() or "" for page in PdfReader(str(output)).pages)
    assert "AGENCE-LYON-2042" in text


def test_last_page_alias_applies_to_last_page(tmp_path):
    source = tmp_path / "source.pdf"
    output = tmp_path / "last-page.pdf"
    _make_pdf(source, pages=2)

    result = DocumentHub(tmp_path / "workspace").annotate_pdf(
        str(source),
        [{"type": "text", "page": -1, "text": "LAST-PAGE", "x": 72, "y": 700}],
        str(output),
    )

    assert result["success"] is True
    assert result["annotations_count"] == 1
    pages = PdfReader(str(output)).pages
    assert "LAST-PAGE" not in (pages[0].extract_text() or "")
    assert "LAST-PAGE" in (pages[1].extract_text() or "")


def test_annotation_rejects_unknown_type_before_writing(tmp_path):
    source = tmp_path / "source.pdf"
    output = tmp_path / "unknown.pdf"
    _make_pdf(source)

    result = DocumentHub(tmp_path / "workspace").annotate_pdf(
        str(source), [{"type": "drawing", "page": 0}], str(output),
    )

    assert result["success"] is False
    assert "non supporte" in result["error"]
    assert not output.exists()


def test_annotation_rejects_invalid_or_empty_payload(tmp_path):
    source = tmp_path / "source.pdf"
    _make_pdf(source)
    hub = DocumentHub(tmp_path / "workspace")

    invalid = hub.annotate_pdf(str(source), "not-json")
    empty = hub.annotate_pdf(str(source), [])

    assert invalid["success"] is False
    assert "liste JSON" in invalid["error"]
    assert empty["success"] is False
    assert "au moins une" in empty["error"]
