from openpyxl import Workbook

from web.routes.chat import _extract_file_content
from web.routes.system import _UPLOAD_ALLOWED_EXTS


def test_chat_upload_accepts_document_studio_formats():
    assert {".xlsx", ".pptx", ".rtf", ".odt", ".ods"} <= _UPLOAD_ALLOWED_EXTS


def test_chat_extracts_xlsx_with_existing_document_reader(tmp_path):
    path = tmp_path / "budget.xlsx"
    workbook = Workbook()
    sheet = workbook.active
    sheet["A1"] = "Budget annuel"
    sheet["B1"] = 42000
    workbook.save(path)
    content = _extract_file_content(
        str(path),
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    assert content is not None
    assert "Budget annuel" in content
    assert "42000" in content
