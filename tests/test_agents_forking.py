"""Tests unitaires pour src/agents/forking_agent.py"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from src.agents.forking_agent import ForkingAgent, Fork, FORKS


class TestFork:
    def test_fork_has_required_fields(self):
        fields = set(Fork.__dataclass_fields__)
        assert "name" in fields
        assert "emoji" in fields
        assert "system_prompt" in fields

    def test_fork_creation(self):
        fork = Fork(
            name="test_fork",
            emoji="🔧",
            system_prompt="You are a test assistant."
        )
        assert fork.name == "test_fork"
        assert fork.emoji == "🔧"
        assert len(fork.system_prompt) > 0

    def test_fork_repr(self):
        fork = Fork(name="myf", emoji="🤖", system_prompt="sp")
        r = repr(fork)
        assert "myf" in r


class TestFORKS:
    def test_forks_is_nonempty_list(self):
        assert isinstance(FORKS, list)
        assert len(FORKS) >= 2

    def test_all_forks_are_fork_instances(self):
        for f in FORKS:
            assert isinstance(f, Fork)
            assert f.name
            assert f.emoji
            assert f.system_prompt

    def test_forks_have_unique_names(self):
        names = [f.name for f in FORKS]
        assert len(names) == len(set(names))


class TestForkingAgent:
    @pytest.fixture
    def agent(self):
        return ForkingAgent()

    def test_instantiation(self, agent):
        assert agent is not None

    def test_has_forks(self, agent):
        assert hasattr(agent, "forks")

    def test_has_execute(self, agent):
        assert callable(getattr(agent, "execute", None))

    def test_has_delegate(self, agent):
        assert callable(getattr(agent, "delegate", None))

    def test_has_name(self, agent):
        assert agent.name is not None

    def test_has_status(self, agent):
        assert hasattr(agent, "status")

    def test_has_history(self, agent):
        assert hasattr(agent, "history")
        assert isinstance(agent.history, list)
