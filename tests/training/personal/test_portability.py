from pathlib import Path
import zipfile

import pytest

from src.training.personal.portability import PersonalModelArchive


def test_archive_round_trip_verifies_hashes_and_excludes_tickets(tmp_path) -> None:
    source = tmp_path / "source"
    (source / "lineages").mkdir(parents=True)
    (source / "lineages" / "registry.json").write_text('{"schema_version":1}', encoding="utf-8")
    (source / "approvals").mkdir()
    (source / "approvals" / "tickets.json").write_text("secret", encoding="utf-8")
    archive = tmp_path / "backup.lumena-model.zip"
    created = PersonalModelArchive(source).create(archive, approval_id="approved")
    assert created["file_count"] == 1
    target = tmp_path / "target"
    restored = PersonalModelArchive(target).restore(archive, approval_id="approved")
    assert restored["restored"] == 1
    assert (target / "lineages" / "registry.json").is_file()
    assert not (target / "approvals").exists()


def test_restore_rejects_path_traversal(tmp_path) -> None:
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("manifest.json", '{"schema_version":1,"files":[{"path":"../escape","sha256":"x","size":1}]}')
        handle.writestr("payload/../escape", "x")
    with pytest.raises(ValueError, match="unsafe"):
        PersonalModelArchive(tmp_path / "target").restore(archive, approval_id="approved")


def test_archive_round_trip_streams_payload_files(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source"
    payload = source / "versions" / "adapter.bin"
    payload.parent.mkdir(parents=True)
    payload.write_bytes(b"model-chunk" * 200_000)
    archive = tmp_path / "streamed.lumena-model.zip"

    def refuse_bulk_reads(self):
        raise AssertionError(f"bulk_read_forbidden:{self}")

    monkeypatch.setattr(Path, "read_bytes", refuse_bulk_reads)
    PersonalModelArchive(source).create(archive, approval_id="approved")
    target = tmp_path / "target"
    restored = PersonalModelArchive(target).restore(archive, approval_id="approved")

    assert restored["restored"] == 1
    assert (target / "versions" / "adapter.bin").stat().st_size == payload.stat().st_size


def test_restore_rejects_manifest_size_mismatch_before_writing(tmp_path) -> None:
    archive = tmp_path / "wrong-size.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("manifest.json", '{"schema_version":1,"files":[{"path":"lineages/model.json","sha256":"x","size":99}]}')
        handle.writestr("payload/lineages/model.json", "x")
    target = tmp_path / "target"

    with pytest.raises(ValueError, match="size_mismatch"):
        PersonalModelArchive(target).restore(archive, approval_id="approved")
    assert not (target / "lineages" / "model.json").exists()
