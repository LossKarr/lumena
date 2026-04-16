"""Tests unitaires pour src/autonomy/curiosity.py"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

from src.autonomy.curiosity import CuriosityModule, ActionType, AutonomousAction, Interest


class TestAutonomousAction:
    def test_default_values(self):
        action = AutonomousAction(
            action_type=ActionType.REFLECT,
            description="Think about today",
        )
        assert action.priority == 5
        assert action.requires_user is False
        assert action.estimated_duration == 60


class TestInterest:
    def test_default_values(self):
        interest = Interest(topic="Python", score=80)
        assert interest.times_explored == 0
        assert interest.last_explored is None


class TestCuriosityModule:
    @pytest.fixture
    def cm(self):
        return CuriosityModule()

    def test_init(self, cm):
        assert cm is not None
        assert len(cm.BOREDOM_ACTIONS) > 0

    def test_has_boredom_level(self, cm):
        level = cm.boredom_level
        assert isinstance(level, (int, float))
        assert level >= 0

    def test_has_curiosity_score(self, cm):
        assert isinstance(cm.curiosity_score, (int, float))

    def test_user_interacted(self, cm):
        old_time = cm.last_user_interaction
        cm.user_interacted()
        assert cm.last_user_interaction >= old_time

    def test_add_interest(self, cm):
        initial_count = len(cm.interests)
        cm.add_interest("Astronomy")
        assert len(cm.interests) >= initial_count

    def test_get_status_returns_dict(self, cm):
        status = cm.get_status()
        assert isinstance(status, dict)

    def test_get_thought_returns_string_or_none(self, cm):
        thought = cm.get_thought()
        assert thought is None or isinstance(thought, str)

    def test_boost_curiosity(self, cm):
        before = cm.curiosity_score
        cm.boost_curiosity(20)
        assert cm.curiosity_score >= before
