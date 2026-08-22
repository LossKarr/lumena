from pathlib import Path

import pytest

from src.documents.delivery_service import DocumentDeliveryService
from src.documents.document_library import DocumentLibrary
from src.documents.import_service import DocumentImportService


def make_services(tmp_path):
    library = DocumentLibrary(tmp_path / "documents.sqlite")
    importer = DocumentImportService(tmp_path / "library", library)
    delivery = DocumentDeliveryService(tmp_path / "exports", library)
    return importer, library, delivery


def test_bounded_historical_scan_and_provenance_filter(tmp_path):
    importer, library, _ = make_services(tmp_path)
    source = tmp_path / "history"; source.mkdir()
    for index in range(3):
        (source / f"note-{index}.txt").write_text(f"archive documentaire {index}", encoding="utf-8")
    result = importer.import_directory(source, max_files=2)
    assert len(result["imported"]) == 2
    assert len(library.search("archive", source="historical_scan")) == 2
    assert library.search("archive", source="upload") == []


def test_delivery_requires_connector_proof(tmp_path):
    importer, library, delivery = make_services(tmp_path)
    source = tmp_path / "note.txt"; source.write_text("preuve", encoding="utf-8")
    record, _ = importer.import_file(source, source_kind="manual")
    delivery.register_connector("broken", lambda **_kwargs: {"success": True})
    with pytest.raises(RuntimeError, match="proof"):
        delivery.deliver(record.id, "broken")


def test_delivery_connector_success_is_recorded(tmp_path):
    importer, library, delivery = make_services(tmp_path)
    source = tmp_path / "note.txt"; source.write_text("preuve", encoding="utf-8")
    record, _ = importer.import_file(source, source_kind="manual")
    delivery.register_connector(
        "simulated", lambda **_kwargs: {"success": True, "proof": "provider-message-42"}
    )
    result = delivery.deliver(record.id, "simulated")
    history = library.list_transformations(record.id)
    assert result["proof"] == "provider-message-42"
    assert history[0]["operation"] == "deliver"
    assert history[0]["details"]["connector"] == "simulated"


def test_local_export_copies_and_records_proof(tmp_path):
    importer, _, delivery = make_services(tmp_path)
    source = tmp_path / "note.txt"; source.write_text("preuve", encoding="utf-8")
    record, _ = importer.import_file(source, source_kind="manual")
    result = delivery.export_local(record.id, "copie.txt")
    assert result["success"] is True
    assert Path(result["proof"]).read_text(encoding="utf-8") == "preuve"


def test_local_export_never_overwrites_existing_file(tmp_path):
    importer, _, delivery = make_services(tmp_path)
    source = tmp_path / "note.txt"; source.write_text("preuve", encoding="utf-8")
    record, _ = importer.import_file(source, source_kind="manual")
    first = delivery.export_local(record.id, "copie.txt")
    second = delivery.export_local(record.id, "copie.txt")
    assert first["proof"] != second["proof"]
    assert Path(first["proof"]).read_text(encoding="utf-8") == "preuve"
    assert Path(second["proof"]).read_text(encoding="utf-8") == "preuve"
