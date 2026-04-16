"""Conftest pour tests core — isolation de l'état emotion."""
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _isolate_emotion_state_core(tmp_path):
    """Empêche la contamination de l'état emotion entre tests."""
    fake_state = tmp_path / "emotion_state.json"
    fake_history = tmp_path / "emotion_history.jsonl"
    with patch("src.utils.paths.EMOTION_STATE_FILE", fake_state), \
         patch("src.utils.paths.EMOTION_HISTORY_FILE", fake_history):
        yield
