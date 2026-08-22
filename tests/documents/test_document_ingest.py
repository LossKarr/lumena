from pathlib import Path

from src.documents import ingest


def test_index_received_document_is_fail_safe(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "src.documents.studio.get_document_studio",
        lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    result = ingest.index_received_document(tmp_path / "missing.pdf", source_kind="chat")
    assert result["indexed"] is False
    assert "offline" in result["error"]


def test_index_received_document_preserves_channel_metadata(tmp_path, monkeypatch):
    source = tmp_path / "note.txt"
    source.write_text("canal documentaire", encoding="utf-8")

    class Importer:
        def import_file(self, path, **kwargs):
            assert Path(path) == source
            assert kwargs["source_kind"] == "telegram"
            assert kwargs["metadata"]["chat_id"] == "42"
            return type("Record", (), {"id": "doc-1"})(), False

    monkeypatch.setattr(
        "src.documents.studio.get_document_studio",
        lambda: type("Studio", (), {"importer": Importer()})(),
    )
    result = ingest.index_received_document(
        source,
        source_kind="telegram",
        source_uri="telegram:42",
        metadata={"chat_id": "42"},
    )
    assert result == {"indexed": True, "document_id": "doc-1", "duplicate": False}
