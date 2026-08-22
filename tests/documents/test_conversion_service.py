from src.documents.conversion_service import CONVERSION_MATRIX, DocumentConversionService


def test_conversion_matrix_reports_fidelity_and_losses():
    capabilities = DocumentConversionService.capabilities()
    assert any(item["from"] == "docx" and item["to"] == "pdf" for item in capabilities)
    assert all("fidelity" in item and "losses" in item for item in capabilities)


def test_unknown_conversion_is_not_advertised():
    assert ("pdf", "xlsx") not in CONVERSION_MATRIX
