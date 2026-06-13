"""Tests Phase F — `curated_cache_writer.append_curated_entry`."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.mcp.curated_cache_writer import (
    append_curated_entry,
    read_curated_entries,
)
from src.mcp.server_catalog import ServerEntry, ServerStatus


def _entry(
    server_id="srv-a",
    package_spec="npm:mcp-foo",
    version="latest",
    semantic_category=None,
    decision_source="",
):
    return ServerEntry(
        server_id=server_id,
        display_name="Display",
        package_spec=package_spec,
        version=version,
        owner_profile="alice",
        trust_score=80,
        status=ServerStatus.INSTALLED,
        added_at="2026-01-01T00:00:00+00:00",
        updated_at="2026-01-01T00:00:00+00:00",
        last_active_at=None,
        notes=None,
        semantic_category=semantic_category,
        category_decision_source=decision_source,
        prefer_over_native=False,
    )


class TestAppendBasic:
    def test_append_creates_dir_and_file(self, tmp_path):
        added = append_curated_entry(tmp_path, _entry())
        assert added is True
        cache_path = tmp_path / "mcp_curated" / "curated_mcp_catalog.json"
        assert cache_path.exists()
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["package_spec"] == "npm:mcp-foo"

    def test_append_returns_payload_fields(self, tmp_path):
        append_curated_entry(tmp_path, _entry(
            semantic_category="mail",
            decision_source="static",
        ))
        entries = read_curated_entries(tmp_path)
        assert entries[0]["semantic_category"] == "mail"
        assert entries[0]["category_decision_source"] == "static"
        # PII non incluse : pas de notes, pas de timestamps.
        assert "notes" not in entries[0]
        assert "added_at" not in entries[0]


class TestIdempotency:
    def test_duplicate_same_package_version_returns_false(self, tmp_path):
        e = _entry()
        first = append_curated_entry(tmp_path, e)
        second = append_curated_entry(tmp_path, e)
        assert first is True
        assert second is False
        entries = read_curated_entries(tmp_path)
        assert len(entries) == 1

    def test_same_package_different_version_appends(self, tmp_path):
        append_curated_entry(tmp_path, _entry(version="1.0.0"))
        append_curated_entry(tmp_path, _entry(version="2.0.0"))
        entries = read_curated_entries(tmp_path)
        assert len(entries) == 2

    def test_different_package_appends(self, tmp_path):
        append_curated_entry(tmp_path, _entry(package_spec="npm:foo"))
        append_curated_entry(tmp_path, _entry(package_spec="npm:bar"))
        entries = read_curated_entries(tmp_path)
        assert len(entries) == 2


class TestMalformedFile:
    def test_malformed_json_is_skipped_then_overwritten(self, tmp_path):
        cache_path = tmp_path / "mcp_curated" / "curated_mcp_catalog.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text("{invalid json", encoding="utf-8")
        # Append doit reussir : le contenu malformé est ignoré → fichier
        # réécrit avec une liste propre [entry].
        added = append_curated_entry(tmp_path, _entry())
        assert added is True
        entries = read_curated_entries(tmp_path)
        assert len(entries) == 1

    def test_non_list_root_is_skipped(self, tmp_path):
        cache_path = tmp_path / "mcp_curated" / "curated_mcp_catalog.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text('{"oops": "dict"}', encoding="utf-8")
        added = append_curated_entry(tmp_path, _entry())
        assert added is True
        assert len(read_curated_entries(tmp_path)) == 1


class TestInputValidation:
    def test_non_path_data_dir_returns_false(self):
        assert append_curated_entry("not a path", _entry()) is False  # type: ignore[arg-type]

    def test_non_serverentry_returns_false(self, tmp_path):
        assert append_curated_entry(tmp_path, {"not": "entry"}) is False  # type: ignore[arg-type]


class TestRead:
    def test_read_absent_file_returns_empty(self, tmp_path):
        assert read_curated_entries(tmp_path) == []

    def test_read_filters_non_dict_entries(self, tmp_path):
        cache_path = tmp_path / "mcp_curated" / "curated_mcp_catalog.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(
            json.dumps([{"a": 1}, "trash", 42, {"b": 2}]),
            encoding="utf-8",
        )
        entries = read_curated_entries(tmp_path)
        assert entries == [{"a": 1}, {"b": 2}]
