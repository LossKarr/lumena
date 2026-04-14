"""Tests unitaires pour src/learning/reflection.py"""
import pytest
import json
from pathlib import Path

from src.learning.reflection import SelfReflection, ReflectionEntry


class TestReflectionEntry:
    def test_to_dict(self):
        entry = ReflectionEntry(
            timestamp="2026-01-01T00:00:00",
            type="action",
            content="Did something",
            mood="happy",
        )
        d = entry.to_dict()
        assert d["type"] == "action"
        assert d["mood"] == "happy"

    def test_from_dict(self):
        data = {
            "timestamp": "2026-01-01T00:00:00",
            "type": "learning",
            "content": "Learned X",
            "context": {},
            "insights": [],
            "mood": "curious",
        }
        entry = ReflectionEntry.from_dict(data)
        assert entry.type == "learning"
        assert entry.mood == "curious"

    def test_roundtrip(self):
        entry = ReflectionEntry(
            timestamp="2026-01-01T00:00:00",
            type="goal",
            content="Accomplish Y",
        )
        entry2 = ReflectionEntry.from_dict(entry.to_dict())
        assert entry.content == entry2.content


class TestSelfReflection:
    @pytest.fixture
    def sr(self, tmp_path):
        return SelfReflection(data_dir=tmp_path)

    def test_init_empty(self, sr):
        assert sr.entries == []
        assert sr.insights == []

    def test_log_action_increases_count(self, sr):
        sr.log_action("Did something important", result="OK", success=True)
        assert len(sr.entries) == 1

    def test_log_action_with_write_entry(self, sr):
        entry = sr.write_entry("Feeling great today!", entry_type="emotion", mood="happy")
        assert sr.entries[-1].mood == "happy"

    def test_write_entry(self, sr):
        sr.write_entry("Learned something new today!", entry_type="learning")
        assert len(sr.entries) == 1

    def test_save_and_reload(self, tmp_path):
        sr1 = SelfReflection(data_dir=tmp_path)
        sr1.write_entry("Did something very significant today", entry_type="action")
        sr1._save()

        sr2 = SelfReflection(data_dir=tmp_path)
        assert len(sr2.entries) == 1
        assert "significant" in sr2.entries[0].content

    def test_get_recent_entries(self, sr):
        sr.write_entry("First entry content", entry_type="action")
        sr.write_entry("Second entry content", entry_type="learning")
        # get_recent_entries takes hours parameter, not n
        recent = sr.get_recent_entries(hours=9999)
        assert len(recent) == 2

    def test_get_daily_summary_returns_string(self, sr):
        sr.write_entry("Done X successfully", entry_type="action")
        summary = sr.get_daily_summary()
        assert isinstance(summary, str)
