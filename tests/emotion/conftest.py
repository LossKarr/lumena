"""Conftest pour tests emotion — isolation de l'état persisté."""
import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def _isolate_emotion_state(tmp_path, request):
    """Empêche le chargement/sauvegarde d'état emotion entre tests."""
    if "no_emotion_isolation" in request.keywords:
        yield
        return
    fake_state = tmp_path / "emotion_state.json"
    fake_history = tmp_path / "emotion_history.jsonl"
    with patch("src.utils.paths.EMOTION_STATE_FILE", fake_state), \
         patch("src.utils.paths.EMOTION_HISTORY_FILE", fake_history):
        yield
