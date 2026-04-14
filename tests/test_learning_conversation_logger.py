"""Tests unitaires pour src/learning/conversation_logger.py"""
import pytest
from pathlib import Path
from unittest.mock import patch

from src.learning.conversation_logger import (
    _is_implicit_negative_feedback,
    _content_hash,
    queue_conversation,
    _NEGATIVE_FEEDBACK_PATTERNS,
)


class TestContentHash:
    def test_same_input_same_hash(self):
        h1 = _content_hash("hello user", "response text")
        h2 = _content_hash("hello user", "response text")
        assert h1 == h2

    def test_different_user_different_hash(self):
        h1 = _content_hash("hello", "same response")
        h2 = _content_hash("goodbye", "same response")
        assert h1 != h2

    def test_hash_is_short(self):
        h = _content_hash("abc", "def")
        assert len(h) == 16


class TestNegativeFeedback:
    def test_known_negative_patterns(self):
        assert _is_implicit_negative_feedback("ça marche pas du tout")
        assert _is_implicit_negative_feedback("t'as rien fait je vois rien")
        assert _is_implicit_negative_feedback("c'est faux arrête")

    def test_neutral_message_not_negative(self):
        assert not _is_implicit_negative_feedback("Merci bien, tu as bien fait")
        assert not _is_implicit_negative_feedback("Super, voilà ce que je voulais")
        assert not _is_implicit_negative_feedback("ok")

    def test_case_insensitive(self):
        assert _is_implicit_negative_feedback("ÇA MARCHE PAS")


class TestQueueConversation:
    def test_queue_runs_without_error(self, tmp_path):
        with patch("src.learning.conversation_logger._POOL_DIR", tmp_path):
            # Should not raise
            result = queue_conversation(
                user_message="Quelle heure est-il?",
                response="Il est 14h30.",
                model_used="deepseek-v3",
                provider="openrouter",
            )
            assert isinstance(result, bool)

    def test_short_messages_not_logged(self, tmp_path):
        """Messages trop courts ne doivent pas créer de fichier."""
        with patch("src.learning.conversation_logger._POOL_DIR", tmp_path):
            result = queue_conversation("ok", "yes", model_used="m", provider="p")
            assert result is False

    def test_negative_feedback_not_logged_skip(self, tmp_path):
        """Le feedback négatif dans le message utilisateur est détecté correctement."""
        assert _is_implicit_negative_feedback("T'as rien fait comme d'habitude")
