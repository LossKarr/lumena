"""Tests unitaires pour src/autonomy/goals.py"""
import pytest
from datetime import datetime, timedelta

from src.autonomy.goals import (
    Goal, GoalManager,
    GoalStatus, GoalPriority, GoalType,
)


@pytest.fixture
def gm(tmp_path):
    return GoalManager(data_dir=tmp_path)


class TestGoalDataclass:
    def test_default_status(self):
        g = Goal(
            id="g1",
            title="Learn Rust",
            description="Study Rust language",
            goal_type=GoalType.LEARNING,
        )
        assert g.status == GoalStatus.PENDING
        assert g.progress == 0.0

    def test_update_progress(self):
        g = Goal(
            id="g1",
            title="Study",
            description="Study basics",
            goal_type=GoalType.LEARNING,
            steps_total=4,
        )
        g.update_progress(2)
        assert g.steps_completed == 2
        assert g.progress == 50.0

    def test_complete_on_full_progress(self):
        g = Goal(
            id="g1",
            title="Study",
            description="Desc",
            goal_type=GoalType.LEARNING,
            steps_total=2,
        )
        g.update_progress(2)
        assert g.status == GoalStatus.COMPLETED

    def test_fail_sets_status(self):
        g = Goal(
            id="g1",
            title="Title",
            description="Desc",
            goal_type=GoalType.LEARNING,
        )
        g.fail("not enough data")
        assert g.status == GoalStatus.FAILED
        assert "not enough data" in g.notes[0]

    def test_to_dict_roundtrip(self):
        g = Goal(
            id="g2",
            title="Organize",
            description="Clean workspace",
            goal_type=GoalType.ORGANIZING,
        )
        d = g.to_dict()
        assert d["id"] == "g2"
        assert d["goal_type"] == GoalType.ORGANIZING.value


class TestGoalManager:
    def test_create_goal(self, gm):
        goal = gm.create_goal(
            title="Learn Python",
            description="Study Python deeply",
            goal_type=GoalType.LEARNING,
        )
        assert goal.title == "Learn Python"
        assert goal.id in gm.goals

    def test_get_active_goals_starts_empty(self, gm):
        active = gm.get_active_goals()
        assert active == []

    def test_get_active_goals(self, gm):
        import time
        gm.create_goal("Learn Python", "Study Python deeply", GoalType.LEARNING)
        time.sleep(0.001)  # éviter collision d'IDs basés sur timestamp
        gm.create_goal("Build App", "Create a web application", GoalType.CREATING)
        active = gm.get_active_goals()
        assert len(active) >= 1
        titles = [g.title for g in active]
        assert "Build App" in titles

    def test_complete_goal(self, gm):
        g = gm.create_goal("T", "D", GoalType.MAINTENANCE)
        gm.complete_goal(g.id)
        assert gm.goals[g.id].status == GoalStatus.COMPLETED

    def test_get_stats(self, gm):
        gm.create_goal("T", "D", GoalType.SOCIAL)
        stats = gm.get_stats()
        assert stats["total"] == 1
        assert stats["active"] == 1

    def test_persistence(self, tmp_path):
        gm1 = GoalManager(data_dir=tmp_path)
        gm1.create_goal("Persistent", "Save and reload", GoalType.EXPLORATION)
        gm1._save()

        gm2 = GoalManager(data_dir=tmp_path)
        assert any(g.title == "Persistent" for g in gm2.goals.values())
