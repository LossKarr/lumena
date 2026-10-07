from __future__ import annotations

from src.training.personal.data_management import PersonalDataManager


def test_lists_verified_backups_without_exposing_contents(tmp_path) -> None:
    root = tmp_path / "data" / "personal_model"
    backup = root / "backups" / "personal-model-test.lumena-model.zip"
    backup.parent.mkdir(parents=True)
    backup.write_bytes(b"archive")

    listed = PersonalDataManager(root).list_backups()

    assert listed[0]["name"] == backup.name
    assert listed[0]["size_bytes"] == 7
    assert len(listed[0]["sha256"]) == 64
    assert "archive" not in listed[0]


def test_discovers_only_bounded_legacy_sources(tmp_path) -> None:
    data_root = tmp_path / "data"
    root = data_root / "personal_model"
    legacy = data_root / "training_pool" / "history.jsonl"
    ignored = data_root / "unrelated" / "private.jsonl"
    current = root / "experiences" / "events.jsonl"
    for path in (legacy, ignored, current):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")

    discovered = PersonalDataManager(root).discover_migration_sources()

    assert [item["path"] for item in discovered] == ["training_pool/history.jsonl"]


def test_delete_learning_data_preserves_versions_config_and_backups(tmp_path) -> None:
    root = tmp_path / "data" / "personal_model"
    for name in ("experiences", "index", "datasets", "quarantine", "judge"):
        target = root / name / "record.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")
    for name in ("versions", "runs", "config", "backups", "audit"):
        target = root / name / "keep.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")

    result = PersonalDataManager(root).delete_learning_data("owner:local", approval_id="approval_test")

    assert result == {"deleted_files": 5, "deleted_bytes": 10, "remaining": []}
    for name in ("experiences", "index", "datasets", "quarantine", "judge"):
        assert not (root / name).exists()
    for name in ("versions", "runs", "config", "backups", "audit"):
        assert (root / name / "keep.json").is_file()
