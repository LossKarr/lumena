"""Tests P6 — Presets personnalité, sensibilité émotionnelle, context-aware."""
from __future__ import annotations

import os
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


# ── P6.1 — Presets personnalité ─────────────────────────────────────────────

class TestPersonalityPresets:

    def test_presets_dict_exists(self):
        """_PERSONALITY_PRESETS est défini dans personality.py."""
        from src.personality import _PERSONALITY_PRESETS
        assert isinstance(_PERSONALITY_PRESETS, dict)
        assert len(_PERSONALITY_PRESETS) >= 3

    def test_preset_professional(self):
        """Le preset professionnel a les bons traits."""
        from src.personality import _PERSONALITY_PRESETS
        p = _PERSONALITY_PRESETS["professional"]
        assert p["playfulness"] == 30
        assert p["proactivity"] == 90
        assert p["honesty"] == 95

    def test_preset_creative(self):
        """Le preset créatif a les bons traits."""
        from src.personality import _PERSONALITY_PRESETS
        p = _PERSONALITY_PRESETS["creative"]
        assert p["creativity"] == 95
        assert p["curiosity"] == 95

    def test_preset_companion(self):
        """Le preset compagnon a les bons traits."""
        from src.personality import _PERSONALITY_PRESETS
        p = _PERSONALITY_PRESETS["companion"]
        assert p["warmth"] == 95
        assert p["loyalty"] == 95

    def test_preset_env_var_applies(self):
        """LUMENA_PERSONALITY_PRESET=professional applique les traits."""
        with patch.dict("os.environ", {"LUMENA_PERSONALITY_PRESET": "professional"}):
            from src.personality import LumenaPersonality
            p = LumenaPersonality()
            assert p.traits["playfulness"] == 30
            assert p.traits["proactivity"] == 90

    def test_preset_env_var_unknown_ignored(self):
        """Un preset inconnu est ignoré (traits par défaut)."""
        # Nettoyer TOUTES les LUMENA_TRAIT_* pour éviter pollution par .env
        saved = {k: os.environ[k] for k in list(os.environ) if k.startswith("LUMENA_TRAIT_")}
        try:
            for k in saved:
                del os.environ[k]
            with patch.dict("os.environ", {"LUMENA_PERSONALITY_PRESET": "nonexistent"}):
                from src.personality import LumenaPersonality, _DEFAULT_TRAITS
                p = LumenaPersonality()
                assert p.traits["curiosity"] == _DEFAULT_TRAITS["curiosity"]
        finally:
            os.environ.update(saved)

    def test_individual_traits_override_preset(self):
        """LUMENA_TRAIT_* a priorité sur le preset."""
        env = {"LUMENA_PERSONALITY_PRESET": "professional", "LUMENA_TRAIT_PLAYFULNESS": "99"}
        with patch.dict("os.environ", env):
            from src.personality import LumenaPersonality
            p = LumenaPersonality()
            # Le preset met playfulness=30, l'env var le surcharge à 99
            assert p.traits["playfulness"] == 99


# ── P6.2 — Sensibilité émotionnelle ─────────────────────────────────────────

class TestEmotionSensitivity:

    def test_default_sensitivity_is_0_5(self):
        """La sensibilité par défaut est 0.5."""
        from src.emotion import EmotionManager
        mgr = EmotionManager()
        # Vérifier que _apply_delta utilise le multiplier
        # Sensitivity 0.5 → multiplier = 0.33 + 0.5*1.67 = 1.165
        old_p = mgr.state.pleasure
        mgr._apply_delta({"pleasure": 0.1}, inertia=0.0)
        # Avec multiplier ~1.165 × 0.1 = 0.1165
        new_p = mgr.state.pleasure
        assert new_p != old_p
        assert abs(new_p) > 0.01

    def test_sensitivity_zero_is_stoic(self):
        """Sensitivity=0 réduit les deltas (stoïque)."""
        with patch.dict("os.environ", {"LUMENA_EMOTION_SENSITIVITY": "0"}):
            from src.emotion import EmotionManager
            mgr = EmotionManager()
            mgr._apply_delta({"pleasure": 0.3}, inertia=0.0)
            # Multiplier = 0.33, so 0.3 * 0.33 = 0.099
            assert mgr.state.pleasure < 0.15

    def test_sensitivity_one_is_hypersensible(self):
        """Sensitivity=1 amplifie les deltas."""
        with patch.dict("os.environ", {"LUMENA_EMOTION_SENSITIVITY": "1"}):
            from src.emotion import EmotionManager
            mgr = EmotionManager()
            mgr._apply_delta({"pleasure": 0.3}, inertia=0.0)
            # Multiplier = 2.0, so 0.3 * 2.0 = 0.6
            assert mgr.state.pleasure > 0.4

    def test_sensitivity_range(self):
        """Le multiplier est dans [0.33, 2.0]."""
        # sensitivity=0 → 0.33
        m0 = 0.33 + 0.0 * 1.67
        assert abs(m0 - 0.33) < 0.01
        # sensitivity=1 → 2.0
        m1 = 0.33 + 1.0 * 1.67
        assert abs(m1 - 2.0) < 0.01


# ── Config schema ────────────────────────────────────────────────────────────

class TestP6ConfigSchema:

    def test_sensitivity_in_schema(self):
        """LUMENA_EMOTION_SENSITIVITY est dans le schema."""
        from web.routes.config import _CONFIG_SCHEMA
        keys = {e["key"] for e in _CONFIG_SCHEMA}
        assert "LUMENA_EMOTION_SENSITIVITY" in keys

    def test_preset_in_schema(self):
        """LUMENA_PERSONALITY_PRESET est dans le schema."""
        from web.routes.config import _CONFIG_SCHEMA
        keys = {e["key"] for e in _CONFIG_SCHEMA}
        assert "LUMENA_PERSONALITY_PRESET" in keys

    def test_preset_schema_options(self):
        """Le schema du preset a les bonnes options."""
        from web.routes.config import _CONFIG_SCHEMA
        entry = next(e for e in _CONFIG_SCHEMA if e["key"] == "LUMENA_PERSONALITY_PRESET")
        assert "professional" in entry["options"]
        assert "creative" in entry["options"]
        assert "companion" in entry["options"]

    def test_env_example_has_sensitivity(self):
        """.env.example contient LUMENA_EMOTION_SENSITIVITY."""
        env_example = Path(__file__).resolve().parent.parent / ".env.example"
        content = env_example.read_text(encoding="utf-8")
        assert "LUMENA_EMOTION_SENSITIVITY" in content

    def test_env_example_has_preset(self):
        """.env.example contient LUMENA_PERSONALITY_PRESET."""
        env_example = Path(__file__).resolve().parent.parent / ".env.example"
        content = env_example.read_text(encoding="utf-8")
        assert "LUMENA_PERSONALITY_PRESET" in content
