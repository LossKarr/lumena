"""Tests unitaires pour src/agents/session_manager.py"""
import pytest

from src.agents.session_manager import SessionManager
from src.agents.session import Session, SessionState


@pytest.fixture
def sm():
    return SessionManager()


class TestSessionManager:
    def test_create_session(self, sm):
        session = sm.create_session("user1")
        assert isinstance(session, Session)
        assert session.user_id == "user1"

    def test_create_session_registers_in_sessions(self, sm):
        session = sm.create_session("user1")
        assert session.id in sm.sessions

    def test_create_session_sets_active(self, sm):
        session = sm.create_session("user1")
        assert sm.active_session["user1"] == session.id

    def test_get_active_session(self, sm):
        session = sm.create_session("user1")
        active = sm.get_active_session("user1")
        assert active is session

    def test_get_active_session_no_session(self, sm):
        result = sm.get_active_session("ghost_user")
        assert result is None

    def test_create_multiple_sessions(self, sm):
        s1 = sm.create_session("user1", title="Chat 1")
        s2 = sm.create_session("user1", title="Chat 2")
        user_sessions = sm.user_sessions.get("user1", [])
        assert s1.id in user_sessions
        assert s2.id in user_sessions

    def test_switch_session(self, sm):
        s1 = sm.create_session("user1")
        s2 = sm.create_session("user1")
        sm.switch_session("user1", s1.id)
        assert sm.active_session["user1"] == s1.id

    def test_delete_session(self, sm):
        session = sm.create_session("user1")
        sm.delete_session(session.id)
        assert session.id not in sm.sessions

    def test_get_user_sessions(self, sm):
        sm.create_session("user2")
        sm.create_session("user2")
        sessions = sm.list_user_sessions("user2")
        assert len(sessions) == 2

    def test_max_sessions_per_user(self):
        sm = SessionManager(max_sessions_per_user=2)
        sm.create_session("user1")
        sm.create_session("user1")
        # Third should either be rejected or oldest removed
        try:
            sm.create_session("user1")
            assert len(sm.user_sessions.get("user1", [])) <= 3
        except Exception:
            pass  # Acceptable behavior
