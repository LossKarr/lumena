from __future__ import annotations

import hashlib
import json

from src.training.personal.experience_store import ExperienceStore
from src.training.personal.migration import HistoricalTrainingMigrator


def test_historical_import_supports_both_formats_and_is_idempotent(tmp_path) -> None:
    source = tmp_path / "legacy.jsonl"
    rows = [
        {"conversations": [{"role": "user", "content": "question a"}, {"role": "assistant", "content": "answer a"}], "metadata": {"provider": "deepseek"}},
        {"conversations": [{"from": "human", "value": "question b"}, {"from": "gpt", "value": "answer b"}], "metadata": {"judge_score": 8}},
    ]
    original = "".join(json.dumps(row) + "\n" for row in rows)
    source.write_text(original, encoding="utf-8")
    original_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    migrator = HistoricalTrainingMigrator(ExperienceStore(tmp_path / "target"))

    first = migrator.migrate([source])
    second = migrator.migrate([source])

    assert first.imported == 2
    assert second.imported == 0
    assert second.duplicates == 2
    assert hashlib.sha256(source.read_bytes()).hexdigest() == original_hash


def test_historical_import_redacts_and_reports_invalid_lines(tmp_path) -> None:
    source = tmp_path / "legacy.jsonl"
    source.write_text(
        json.dumps({"conversations": [{"role": "user", "content": "api_key=SECRET_MIGRATION_LEAK_123456"}, {"role": "assistant", "content": "ok"}]}) + "\nnot-json\n",
        encoding="utf-8",
    )
    store = ExperienceStore(tmp_path / "target")
    report = HistoricalTrainingMigrator(store).migrate([source])
    events = store._paths("owner:local")["events"].read_text(encoding="utf-8")
    assert report.imported == 1
    assert report.invalid == 1
    assert "SECRET_MIGRATION_LEAK" not in events


def test_migration_preview_writes_nothing_to_target_and_matches_counts(tmp_path) -> None:
    source = tmp_path / "legacy.jsonl"
    source.write_text(json.dumps({"conversations": [{"role": "user", "content": "question"}, {"role": "assistant", "content": "answer"}]}) + "\n", encoding="utf-8")
    target = tmp_path / "target"
    report = HistoricalTrainingMigrator.preview([source])
    assert report.imported == 1
    assert not target.exists()
