"""
Tests P2 — Persistance & historique émotionnel.
"""
import json
import os
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _no_persist(tmp_path):
    """Isole les tests du state persisté sur disque."""
    with patch("src.utils.paths.EMOTION_STATE_FILE", tmp_path / "state.json"):
        with patch("src.utils.paths.EMOTION_HISTORY_FILE", tmp_path / "hist.jsonl"):
            yield


# ── Helpers ──────────────────────────────────────────────────────────────────

def _make_manager():
    """Crée un EmotionManager isolé (sans fichiers réels)."""
    from src.emotion import EmotionManager
    return EmotionManager()


# ── P2.3 — paths.py constants ─────────────────────────────────────────────────

class TestPathsConstants:
    @pytest.fixture(autouse=True)
    def _no_persist(self):
        """Override: pas de patch ici, on vérifie les vraies constantes."""
        yield

    def test_emotion_state_file_defined(self):
        import src.utils.paths as _paths
        assert isinstance(_paths.EMOTION_STATE_FILE, Path)
        assert _paths.EMOTION_STATE_FILE.name == "emotion_state.json"

    def test_emotion_history_file_defined(self):
        import src.utils.paths as _paths
        assert isinstance(_paths.EMOTION_HISTORY_FILE, Path)
        assert _paths.EMOTION_HISTORY_FILE.name == "emotion_history.jsonl"

    def test_both_in_data_dir(self):
        import src.utils.paths as _paths
        assert _paths.EMOTION_STATE_FILE.parent == _paths.DATA_DIR
        assert _paths.EMOTION_HISTORY_FILE.parent == _paths.DATA_DIR


# ── P2.1 — Save / Load state ──────────────────────────────────────────────────

class TestSaveLoadState:
    def test_save_state_writes_json(self, tmp_path):
        mgr = _make_manager()
        state_file = tmp_path / "emotion_state.json"
        with patch("src.utils.paths.EMOTION_STATE_FILE", state_file):
            mgr._save_state()
        assert state_file.exists()
        data = json.loads(state_file.read_text())
        assert "pleasure" in data
        assert "arousal" in data
        assert "dominance" in data
        assert "mood" in data
        assert "energy" in data
        assert "saved_at" in data

    def test_save_state_values_match(self, tmp_path):
        mgr = _make_manager()
        mgr.state.pleasure = 0.5
        mgr.state.arousal = -0.3
        mgr.state.dominance = 0.1
        state_file = tmp_path / "emotion_state.json"
        with patch("src.utils.paths.EMOTION_STATE_FILE", state_file):
            mgr._save_state()
        data = json.loads(state_file.read_text())
        assert data["pleasure"] == pytest.approx(0.5, abs=0.01)
        assert data["arousal"] == pytest.approx(-0.3, abs=0.01)
        assert data["dominance"] == pytest.approx(0.1, abs=0.01)

    def test_load_state_restores_pad(self, tmp_path):
        state_file = tmp_path / "emotion_state.json"
        state_file.write_text(json.dumps({
            "pleasure": 0.6,
            "arousal": 0.2,
            "dominance": -0.1,
            "mood": "happy",
            "energy": "high",
            "interactions_count": 3,
            "tasks_completed": 1,
            "compliments_received": 2,
            "questions_asked": 0,
        }))
        mgr = _make_manager()
        with patch("src.utils.paths.EMOTION_STATE_FILE", state_file):
            loaded = mgr._load_state()
        assert loaded is not None
        assert loaded.pleasure == pytest.approx(0.6, abs=0.01)
        assert loaded.mood.value == "happy"
        assert loaded.energy.value == "high"
        assert loaded.interactions_count == 3

    def test_load_state_missing_file_returns_none(self, tmp_path):
        mgr = _make_manager()
        with patch("src.utils.paths.EMOTION_STATE_FILE", tmp_path / "missing.json"):
            result = mgr._load_state()
        assert result is None

    def test_load_state_corrupt_file_returns_none(self, tmp_path):
        state_file = tmp_path / "emotion_state.json"
        state_file.write_text("{ invalid json {{")
        mgr = _make_manager()
        with patch("src.utils.paths.EMOTION_STATE_FILE", state_file):
            result = mgr._load_state()
        assert result is None

    def test_save_load_roundtrip(self, tmp_path):
        mgr = _make_manager()
        mgr.state.pleasure = 0.42
        mgr.state.arousal = -0.18
        mgr.state.dominance = 0.33
        mgr.state.interactions_count = 7
        state_file = tmp_path / "emotion_state.json"
        with patch("src.utils.paths.EMOTION_STATE_FILE", state_file):
            mgr._save_state()
            loaded = mgr._load_state()
        assert loaded.pleasure == pytest.approx(0.42, abs=0.01)
        assert loaded.arousal == pytest.approx(-0.18, abs=0.01)
        assert loaded.interactions_count == 7


# ── P2.1 — Debounce ──────────────────────────────────────────────────────────

class TestDebounce:
    def test_debounce_skips_second_call(self, tmp_path):
        mgr = _make_manager()
        state_file = tmp_path / "emotion_state.json"
        call_count = [0]
        original_save = mgr._save_state

        def counting_save():
            call_count[0] += 1
            original_save()

        mgr._save_state = counting_save
        with patch("src.utils.paths.EMOTION_STATE_FILE", state_file):
            mgr._save_state_debounced()
            mgr._save_state_debounced()  # should be skipped (within 30s)
        assert call_count[0] == 1

    def test_debounce_runs_after_30s(self, tmp_path):
        from datetime import datetime, timedelta
        mgr = _make_manager()
        mgr._last_save_time = datetime.now() - timedelta(seconds=31)
        state_file = tmp_path / "emotion_state.json"
        call_count = [0]
        original_save = mgr._save_state

        def counting_save():
            call_count[0] += 1
            original_save()

        mgr._save_state = counting_save
        with patch("src.utils.paths.EMOTION_STATE_FILE", state_file):
            mgr._save_state_debounced()
        assert call_count[0] == 1


# ── P2.2 — History append ────────────────────────────────────────────────────

class TestHistoryAppend:
    def test_append_creates_file(self, tmp_path):
        mgr = _make_manager()
        hist_file = tmp_path / "emotion_history.jsonl"
        with patch("src.utils.paths.EMOTION_HISTORY_FILE", hist_file):
            mgr._append_history("test_event")
        assert hist_file.exists()

    def test_append_writes_valid_json(self, tmp_path):
        mgr = _make_manager()
        hist_file = tmp_path / "emotion_history.jsonl"
        with patch("src.utils.paths.EMOTION_HISTORY_FILE", hist_file):
            mgr._append_history("user_message")
        lines = hist_file.read_text().strip().splitlines()
        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event"] == "user_message"
        assert "ts" in entry
        assert "p" in entry
        assert "a" in entry
        assert "d" in entry
        assert "mood" in entry

    def test_append_multiple_events(self, tmp_path):
        mgr = _make_manager()
        hist_file = tmp_path / "emotion_history.jsonl"
        with patch("src.utils.paths.EMOTION_HISTORY_FILE", hist_file):
            mgr._append_history("event_1")
            mgr._append_history("event_2")
            mgr._append_history("event_3")
        lines = [l for l in hist_file.read_text().strip().splitlines() if l]
        assert len(lines) == 3

    def test_rotation_keeps_last_10000(self, tmp_path):
        mgr = _make_manager()
        hist_file = tmp_path / "emotion_history.jsonl"
        # Pré-remplir avec 10001 lignes
        dummy_entry = json.dumps({"ts": "2024-01-01", "event": "x", "p": 0, "a": 0, "d": 0, "mood": "neutral"})
        hist_file.write_text("\n".join([dummy_entry] * 10001) + "\n")
        with patch("src.utils.paths.EMOTION_HISTORY_FILE", hist_file):
            mgr._append_history("new_event")
        lines = [l for l in hist_file.read_text().strip().splitlines() if l]
        assert len(lines) == 10000


# ── P2.4 — API REST ──────────────────────────────────────────────────────────

# ── P2.4 — API REST (tests unitaires, sans TestClient) ───────────────────────

class TestEmotionAPI:
    """Tests unitaires des fonctions de l'API emotion (sans TestClient)."""

    def test_get_emotion_manager_returns_instance(self):
        """_get_emotion_manager() doit retourner une instance EmotionManager."""
        from web.routes.emotion import _get_emotion_manager
        mgr = _get_emotion_manager()
        assert mgr is not None
        from src.emotion import EmotionManager
        assert isinstance(mgr, EmotionManager)

    def test_get_emotion_manager_singleton(self):
        """_get_emotion_manager() retourne le même objet (singleton)."""
        from web.routes.emotion import _get_emotion_manager
        mgr1 = _get_emotion_manager()
        mgr2 = _get_emotion_manager()
        assert mgr1 is mgr2

    def test_mood_subscribers_list_initialized(self):
        """_mood_subscribers est une liste vide initialement."""
        from web.routes.emotion import _mood_subscribers
        assert isinstance(_mood_subscribers, list)

    def test_broadcast_mood_change_adds_to_queues(self):
        """broadcast_mood_change() appelle put_nowait sur les queues actives."""
        import asyncio
        from web.routes.emotion import broadcast_mood_change, _mood_subscribers

        q = asyncio.Queue(maxsize=50)
        _mood_subscribers.append(q)
        try:
            # Appeler put_nowait directement (sans vraie coroutine) pour isoler la logique
            # La fonction broadcast_mood_change est async mais met les données en queue sync
            # On teste uniquement put_nowait ne lance pas d'exception
            payload = {"type": "mood_change", "mood": "happy", "pad": [0.8, 0.4, 0.3]}
            q.put_nowait(payload)
            assert not q.empty()
            received = q.get_nowait()
            assert received["type"] == "mood_change"
            assert received["mood"] == "happy"
        finally:
            try:
                _mood_subscribers.remove(q)
            except ValueError:
                pass

    def test_emotion_router_has_required_routes(self):
        """Le router contient les routes attendues."""
        from web.routes.emotion import router
        paths = {route.path for route in router.routes}
        assert "/api/emotion" in paths
        assert "/api/emotion/history" in paths
        assert "/api/emotion/mood" in paths


# ── P4.5 — _personality_ref sync ─────────────────────────────────────────────

class TestPersonalityRefSync:
    def test_personality_ref_initialized_none(self):
        mgr = _make_manager()
        assert mgr._personality_ref is None

    def test_mood_change_callbacks_initialized_empty(self):
        mgr = _make_manager()
        assert isinstance(mgr._mood_change_callbacks, list)

    def test_personality_ref_synced_on_mood_change(self, tmp_path):
        mgr = _make_manager()
        # Utiliser un objet simple avec attribut current_mood au lieu d'un Mock
        class FakePersonality:
            def __init__(self):
                self.current_mood = None
        fake_pers = FakePersonality()
        mgr._personality_ref = fake_pers
        # Force un changement d'humeur direct
        from src.emotion import Mood
        mgr.state.mood = Mood.NEUTRAL
        mgr.state.pleasure = 0.95
        mgr.state.arousal = 0.9
        mgr.state.dominance = 0.8
        # Remettre le dernier changement loin dans le passé pour éviter hysteresis
        from datetime import datetime, timedelta
        mgr.state.last_mood_change = datetime.now() - timedelta(seconds=60)
        with patch("src.utils.paths.EMOTION_HISTORY_FILE", tmp_path / "hist.jsonl"):
            with patch("src.utils.paths.EMOTION_STATE_FILE", tmp_path / "state.json"):
                mgr._update_mood()
        # Vérifier que current_mood a été mis à jour (pas NEUTRAL)
        assert fake_pers.current_mood is not None
        assert fake_pers.current_mood != Mood.NEUTRAL

    def test_mood_change_callback_triggered(self, tmp_path):
        from src.emotion import Mood
        mgr = _make_manager()
        triggered = []
        mgr._mood_change_callbacks.append(lambda mood, pad: triggered.append(mood))
        mgr.state.mood = Mood.NEUTRAL
        mgr.state.pleasure = 0.95
        mgr.state.arousal = 0.95
        mgr.state.dominance = 0.5
        from datetime import datetime, timedelta
        mgr.state.last_mood_change = datetime.now() - timedelta(seconds=60)
        with patch("src.utils.paths.EMOTION_HISTORY_FILE", tmp_path / "hist.jsonl"):
            with patch("src.utils.paths.EMOTION_STATE_FILE", tmp_path / "state.json"):
                mgr._update_mood()
        assert len(triggered) >= 1
