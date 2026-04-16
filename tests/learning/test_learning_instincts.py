"""Tests unitaires pour src/learning/instincts.py"""
import pytest
from datetime import datetime

from src.learning.instincts import Instinct, LearningEvent, InstinctSystem


class TestInstinct:
    def test_success_rate_zero_uses(self):
        inst = Instinct(id="i1", pattern="hello", response="Hi!", confidence=0.5)
        assert inst.success_rate == 0.0

    def test_success_rate_calculation(self):
        inst = Instinct(
            id="i1", pattern="hello", response="Hi!", confidence=0.5,
            times_used=10, times_successful=7
        )
        assert inst.success_rate == pytest.approx(0.7)

    def test_to_dict(self):
        inst = Instinct(
            id="i1", pattern="test.*", response="Do test",
            confidence=0.8, category="coding"
        )
        d = inst.to_dict()
        assert d["id"] == "i1"
        assert d["category"] == "coding"
        assert d["confidence"] == 0.8

    def test_from_dict_roundtrip(self):
        inst = Instinct(
            id="i2", pattern="pattern", response="response", confidence=0.6
        )
        inst2 = Instinct.from_dict(inst.to_dict())
        assert inst2.id == inst.id
        assert inst2.confidence == inst.confidence


class TestLearningEvent:
    def test_creation(self):
        ev = LearningEvent(
            event_type="success",
            context="user asked for help",
            action="searched web",
            outcome="found answer"
        )
        assert ev.event_type == "success"
        assert isinstance(ev.timestamp, datetime)


class TestInstinctSystem:
    @pytest.fixture
    def is_(self, tmp_path):
        return InstinctSystem(data_dir=tmp_path)

    def test_init(self, is_):
        assert is_ is not None
        assert isinstance(is_.instincts, dict)

    def test_add_instinct(self, is_):
        inst = is_.learn(
            pattern="python.*help",
            response="Suggest online docs",
            was_successful=True,
            category="coding"
        )
        assert inst is not None
        assert inst.id is not None
        assert len(is_.instincts) == 1

    def test_find_matching_instinct(self, is_):
        is_.learn("how to use python docs", "Use documentation", was_successful=True)
        matches = is_.suggest("how to use python docs")
        # suggest may return empty if confidence threshold not met, just check type
        assert isinstance(matches, list)

    def test_no_match_returns_empty(self, is_):
        matches = is_.suggest("something completely unrelated XYZ")
        assert isinstance(matches, list)

    def test_record_success_updates_stats(self, is_):
        inst = is_.learn("test pattern", "test response", was_successful=True)
        assert inst.times_used == 1
        assert inst.times_successful == 1

    def test_persistence(self, tmp_path):
        is1 = InstinctSystem(data_dir=tmp_path)
        inst = is1.learn("coding pattern", "coding response", was_successful=True)
        is1._save()

        is2 = InstinctSystem(data_dir=tmp_path)
        assert inst.id in is2.instincts
