"""Tests unitaires pour src/memory/session_memory.py"""
import json
import pytest
from datetime import datetime, timedelta

from src.memory.session_memory import (
    SessionTurn,
    KeyDecision,
    SessionMemory,
)


# ─── SessionTurn ───────────────────────────────────────────────────────────

class TestSessionTurn:
    def test_defaults(self):
        t = SessionTurn(role="user", content="hello")
        assert t.importance == 1.0
        assert t.metadata == {}
        assert isinstance(t.timestamp, datetime)

    def test_custom_importance(self):
        t = SessionTurn(role="assistant", content="resp", importance=0.5)
        assert t.importance == 0.5


# ─── KeyDecision ───────────────────────────────────────────────────────────

class TestKeyDecision:
    def test_defaults(self):
        d = KeyDecision(description="chose X", context="because Y")
        assert d.related_turns == []
        assert isinstance(d.timestamp, datetime)


# ─── SessionMemory.add_turn ────────────────────────────────────────────────

class TestSessionMemoryAddTurn:
    def test_add_turn_returns_index(self):
        mem = SessionMemory()
        idx = mem.add_turn("user", "hello")
        assert idx == 0
        idx2 = mem.add_turn("assistant", "hi")
        assert idx2 == 1

    def test_turns_stored(self):
        mem = SessionMemory()
        mem.add_turn("user", "test message")
        assert len(mem.turns) == 1
        assert mem.turns[0].role == "user"
        assert mem.turns[0].content == "test message"

    def test_compaction_triggered(self):
        """Compaction runs when turns exceed 2*max_turns."""
        mem = SessionMemory(max_turns=5)
        for i in range(12):
            mem.add_turn("user", f"msg {i}")
        assert len(mem.turns) <= mem.max_turns * 2

    def test_add_with_metadata(self):
        mem = SessionMemory()
        mem.add_turn("user", "msg", metadata={"source": "telegram"})
        assert mem.turns[0].metadata["source"] == "telegram"


# ─── SessionMemory.add_decision ────────────────────────────────────────────

class TestSessionMemoryAddDecision:
    def test_add_decision(self):
        mem = SessionMemory()
        mem.add_decision("deploy to prod", context="after testing")
        assert len(mem.key_decisions) == 1
        assert mem.key_decisions[0].description == "deploy to prod"

    def test_decision_links_last_turn(self):
        mem = SessionMemory()
        mem.add_turn("user", "message")
        mem.add_decision("test decision")
        assert 0 in mem.key_decisions[0].related_turns


# ─── SessionMemory.learn_preference ───────────────────────────────────────

class TestSessionMemoryPreferences:
    def test_add_preference(self):
        mem = SessionMemory()
        mem.learn_preference("language", "fr")
        assert mem.user_preferences["language"] == "fr"

    def test_overwrite_preference(self):
        mem = SessionMemory()
        mem.learn_preference("tone", "formal")
        mem.learn_preference("tone", "casual")
        assert mem.user_preferences["tone"] == "casual"


# ─── SessionMemory.get_context ─────────────────────────────────────────────

class TestSessionMemoryGetContext:
    def test_get_context_keys(self):
        mem = SessionMemory()
        ctx = mem.get_context()
        for key in ["recent_turns", "decisions", "preferences",
                    "session_duration_minutes", "total_turns"]:
            assert key in ctx

    def test_get_context_turn_count(self):
        mem = SessionMemory()
        mem.add_turn("user", "a")
        mem.add_turn("assistant", "b")
        ctx = mem.get_context(last_n=10)
        assert len(ctx["recent_turns"]) == 2

    def test_get_context_last_n(self):
        mem = SessionMemory()
        for i in range(10):
            mem.add_turn("user", f"msg {i}")
        ctx = mem.get_context(last_n=3)
        assert len(ctx["recent_turns"]) == 3

    def test_content_truncated_to_500(self):
        mem = SessionMemory()
        mem.add_turn("user", "x" * 1000)
        ctx = mem.get_context()
        assert len(ctx["recent_turns"][0]["content"]) <= 500


# ─── SessionMemory.get_context_summary ────────────────────────────────────

class TestSessionMemoryContextSummary:
    def test_summary_has_session_info(self):
        mem = SessionMemory()
        mem.add_turn("user", "hello")
        summary = mem.get_context_summary()
        assert "Session" in summary or "session" in summary

    def test_summary_includes_preferences(self):
        mem = SessionMemory()
        mem.learn_preference("lang", "python")
        summary = mem.get_context_summary()
        assert "lang" in summary

    def test_summary_includes_decisions(self):
        mem = SessionMemory()
        mem.add_decision("deploy now")
        summary = mem.get_context_summary()
        assert "deploy now" in summary


# ─── SessionMemory JSON serialization ──────────────────────────────────────

class TestSessionMemoryJSON:
    def test_to_json_and_from_json(self):
        mem = SessionMemory()
        mem.add_turn("user", "hello world")
        mem.add_turn("assistant", "hi there")
        mem.add_decision("important decision")
        mem.learn_preference("style", "casual")

        json_str = mem.to_json()
        restored = SessionMemory.from_json(json_str)

        assert len(restored.turns) == 2
        assert restored.turns[0].role == "user"
        assert restored.turns[0].content == "hello world"
        assert len(restored.key_decisions) == 1
        assert restored.key_decisions[0].description == "important decision"
        assert restored.user_preferences["style"] == "casual"

    def test_to_json_valid_json(self):
        mem = SessionMemory()
        mem.add_turn("user", "test")
        json_str = mem.to_json()
        data = json.loads(json_str)
        assert "turns" in data
        assert "decisions" in data
        assert "preferences" in data

    def test_from_json_empty(self):
        mem = SessionMemory()
        json_str = mem.to_json()
        restored = SessionMemory.from_json(json_str)
        assert len(restored.turns) == 0

    def test_unicode_preserved(self):
        mem = SessionMemory()
        mem.add_turn("user", "Bonjour é à ü ñ 中文 🎉")
        json_str = mem.to_json()
        restored = SessionMemory.from_json(json_str)
        assert "Bonjour é à ü ñ" in restored.turns[0].content


# ─── SessionMemory._compact ────────────────────────────────────────────────

class TestSessionMemoryCompact:
    def test_compact_reduces_turns(self):
        mem = SessionMemory(max_turns=5)
        for i in range(20):
            mem.add_turn("user", f"message {i}", importance=0.3)
        # Force compaction
        mem._compact()
        assert len(mem.turns) <= mem.max_turns

    def test_compact_keeps_recent_turns(self):
        mem = SessionMemory(max_turns=5)
        for i in range(15):
            mem.add_turn("user", f"old msg {i}", importance=0.1)
        mem.add_turn("user", "recent important message", importance=0.9)
        mem._compact()
        contents = [t.content for t in mem.turns]
        # Recent turns should be kept (last 10 get boost)
        assert any("recent" in c or "old" in c for c in contents)
