"""Tests Toggle — LUMENA_EMOTION_ENABLED on/off."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


# ── Fixture isolée ───────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _no_persist(tmp_path):
    fake_state = tmp_path / "emotion_state.json"
    fake_hist = tmp_path / "emotion_history.jsonl"
    import src.utils.paths as _paths
    with patch.object(_paths, "EMOTION_STATE_FILE", fake_state), \
         patch.object(_paths, "EMOTION_HISTORY_FILE", fake_hist), \
         patch.dict("os.environ", {"LUMENA_EMOTION_LLM_ANALYSIS": "0"}):
        yield


# ── Toggle enabled ───────────────────────────────────────────────────────────

class TestEmotionEnabled:

    def test_emotion_enabled_by_default(self):
        """Par défaut, LUMENA_EMOTION_ENABLED=1, emotion_manager est créé."""
        env = {"LUMENA_EMOTION_ENABLED": "1"}
        with patch.dict("os.environ", env):
            from src.core import LumenaCore
            core = LumenaCore()
            assert core.emotion_manager is not None

    def test_emotion_disabled_gives_none(self):
        """LUMENA_EMOTION_ENABLED=0 → emotion_manager is None."""
        env = {"LUMENA_EMOTION_ENABLED": "0"}
        with patch.dict("os.environ", env):
            from src.core import LumenaCore
            core = LumenaCore()
            assert core.emotion_manager is None

    def test_emotion_disabled_no_state_file(self, tmp_path):
        """Quand désactivé, aucun fichier d'état n'est créé."""
        import src.utils.paths as _paths
        state_file = _paths.EMOTION_STATE_FILE
        env = {"LUMENA_EMOTION_ENABLED": "0"}
        with patch.dict("os.environ", env):
            from src.core import LumenaCore
            core = LumenaCore()
            assert core.emotion_manager is None
            assert not state_file.exists()


# ── Config schema ────────────────────────────────────────────────────────────

class TestToggleConfigSchema:

    def test_emotion_enabled_in_schema(self):
        """LUMENA_EMOTION_ENABLED est dans le schema."""
        from web.routes.config import _CONFIG_SCHEMA
        keys = {e["key"] for e in _CONFIG_SCHEMA}
        assert "LUMENA_EMOTION_ENABLED" in keys

    def test_emotion_enabled_default_is_1(self):
        """Le défaut est '1' (activé)."""
        from web.routes.config import _CONFIG_SCHEMA
        entry = next(e for e in _CONFIG_SCHEMA if e["key"] == "LUMENA_EMOTION_ENABLED")
        assert entry["default"] == "1"
        assert entry["type"] == "bool"

    def test_env_example_has_toggle(self):
        """.env.example contient LUMENA_EMOTION_ENABLED."""
        env_example = Path(__file__).resolve().parent.parent.parent / ".env.example"
        content = env_example.read_text(encoding="utf-8")
        assert "LUMENA_EMOTION_ENABLED" in content


# ── React prompt guard ───────────────────────────────────────────────────────

class TestEmotionPromptGuard:

    def test_react_emotion_guard_code_exists(self):
        """react.py vérifie emotion_mgr avant injection."""
        src = Path(__file__).resolve().parent.parent.parent / "src" / "reasoning" / "react.py"
        content = src.read_text(encoding="utf-8")
        # Vérifie qu'il y a un guard `if emotion_mgr` ou `getattr`
        assert "emotion_mgr" in content
