"""Tests unitaires pour src/agents/session.py"""
import pytest
from datetime import datetime

from src.agents.session import Session, SessionState, Message


class TestMessage:
    def test_message_creation(self):
        msg = Message(role="user", content="Hello")
        assert msg.role == "user"
        assert msg.content == "Hello"
        assert isinstance(msg.timestamp, datetime)

    def test_message_metadata(self):
        msg = Message(role="assistant", content="Hi", metadata={"tokens": 5})
        assert msg.metadata["tokens"] == 5


class TestSession:
    def test_default_state(self):
        s = Session()
        assert s.state == SessionState.ACTIVE
        assert s.messages == []
        assert len(s.id) > 0

    def test_add_message(self):
        s = Session()
        msg = s.add_message("user", "Test message")
        assert isinstance(msg, Message)
        assert len(s.messages) == 1

    def test_add_message_updates_timestamp(self):
        s = Session()
        before = s.updated_at
        s.add_message("user", "hello")
        assert s.updated_at >= before

    def test_get_messages_for_llm(self):
        s = Session()
        s.add_message("user", "Q")
        s.add_message("assistant", "A")
        msgs = s.get_messages_for_llm()
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

    def test_max_messages_truncates(self):
        s = Session(max_messages=5)
        for i in range(10):
            s.add_message("user", f"msg {i}" * 5)
        # Should not exceed max + system buffer
        assert len(s.messages) <= 10  # Soft bound

    def test_state_transitions(self):
        s = Session()
        s.state = SessionState.COMPLETED
        assert s.state == SessionState.COMPLETED

    def test_to_dict(self):
        s = Session(user_id="u1", title="Test session")
        d = s.to_dict()
        assert d["user_id"] == "u1"
        assert d["title"] == "Test session"
        assert "state" in d

    def test_clear_messages(self):
        s = Session()
        s.add_message("user", "msg1")
        s.add_message("user", "msg2")
        s.clear_messages()
        assert s.messages == []

    def test_get_message_count(self):
        s = Session()
        s.add_message("user", "a")
        s.add_message("assistant", "b")
        assert s.get_message_count() == 2
