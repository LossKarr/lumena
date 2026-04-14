"""Tests P0 — Wizard fix: env vars → personality/emotion runtime."""
import os
import pytest
from unittest.mock import patch

from src.emotion import Mood, EnergyLevel, EmotionManager
from src.personality import LumenaPersonality, _DEFAULT_TRAITS

# Env propre (sans LUMENA_TRAIT_* du .env réel)
_CLEAN_ENV = {k: v for k, v in os.environ.items()
              if not k.startswith("LUMENA_TRAIT_")
              and k not in ("LUMENA_USE_EMOJIS", "LUMENA_EMOJI_FREQUENCY",
                            "LUMENA_DEFAULT_MOOD", "LUMENA_ENABLED_MOODS")}


@pytest.fixture(autouse=True)
def _no_persist(tmp_path):
    """Isole les tests du state persisté sur disque."""
    with patch("src.utils.paths.EMOTION_STATE_FILE", tmp_path / "state.json"):
        with patch("src.utils.paths.EMOTION_HISTORY_FILE", tmp_path / "hist.jsonl"):
            yield


# ─── _DEFAULT_TRAITS constant ──────────────────────────────────────────────

class TestDefaultTraits:
    def test_default_traits_keys(self):
        assert set(_DEFAULT_TRAITS) == {
            "curiosity", "playfulness", "warmth", "proactivity",
            "honesty", "creativity", "patience", "loyalty",
        }

    def test_default_traits_values_range(self):
        for k, v in _DEFAULT_TRAITS.items():
            assert 0 <= v <= 100, f"{k} hors bornes"

    def test_personality_uses_default_traits(self):
        # Isoler des LUMENA_TRAIT_* éventuellement dans le .env réel
        env_clean = {k: v for k, v in os.environ.items()
                     if not k.startswith("LUMENA_TRAIT_")}
        with patch.dict(os.environ, env_clean, clear=True):
            p = LumenaPersonality()
            assert p.traits == _DEFAULT_TRAITS
            # Doit être une copie, pas le même objet
            assert p.traits is not _DEFAULT_TRAITS


# ─── LUMENA_USE_EMOJIS ─────────────────────────────────────────────────────

class TestUseEmojis:
    @patch.dict(os.environ, _CLEAN_ENV, clear=True)
    def test_default_emojis_true(self):
        p = LumenaPersonality()
        assert p.use_emojis is True

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_USE_EMOJIS": "0"}, clear=True)
    def test_emojis_disabled(self):
        p = LumenaPersonality()
        assert p.use_emojis is False

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_USE_EMOJIS": "1"}, clear=True)
    def test_emojis_enabled_explicit(self):
        p = LumenaPersonality()
        assert p.use_emojis is True

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_USE_EMOJIS": "false"}, clear=True)
    def test_emojis_disabled_false(self):
        p = LumenaPersonality()
        assert p.use_emojis is False


# ─── LUMENA_EMOJI_FREQUENCY ────────────────────────────────────────────────

class TestEmojiFrequency:
    @patch.dict(os.environ, _CLEAN_ENV, clear=True)
    def test_default_frequency(self):
        p = LumenaPersonality()
        assert p.emoji_frequency == pytest.approx(0.3)

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_EMOJI_FREQUENCY": "50"}, clear=True)
    def test_custom_frequency(self):
        p = LumenaPersonality()
        assert p.emoji_frequency == pytest.approx(0.5)

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_EMOJI_FREQUENCY": "0"}, clear=True)
    def test_zero_frequency(self):
        p = LumenaPersonality()
        assert p.emoji_frequency == pytest.approx(0.0)

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_EMOJI_FREQUENCY": "150"}, clear=True)
    def test_clamped_high(self):
        p = LumenaPersonality()
        assert p.emoji_frequency == pytest.approx(1.0)

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_EMOJI_FREQUENCY": "abc"}, clear=True)
    def test_invalid_keeps_default(self):
        p = LumenaPersonality()
        assert p.emoji_frequency == pytest.approx(0.3)


# ─── LUMENA_DEFAULT_MOOD ──────────────────────────────────────────────────

class TestDefaultMood:
    @patch.dict(os.environ, _CLEAN_ENV, clear=True)
    def test_default_neutral(self):
        p = LumenaPersonality()
        assert p.current_mood == Mood.NEUTRAL

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_DEFAULT_MOOD": "happy"}, clear=True)
    def test_custom_mood(self):
        p = LumenaPersonality()
        assert p.current_mood == Mood.HAPPY

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_DEFAULT_MOOD": "excited"}, clear=True)
    def test_excited_mood(self):
        p = LumenaPersonality()
        assert p.current_mood == Mood.EXCITED

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_DEFAULT_MOOD": "INVALID"}, clear=True)
    def test_invalid_mood_falls_back_neutral(self):
        p = LumenaPersonality()
        assert p.current_mood == Mood.NEUTRAL

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_DEFAULT_MOOD": "touched"}, clear=True)
    def test_touched_mood(self):
        p = LumenaPersonality()
        assert p.current_mood == Mood.TOUCHED


# ─── LUMENA_TRAIT_* env vars ───────────────────────────────────────────────

class TestTraitEnvVars:
    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_TRAIT_CURIOSITY": "50"}, clear=True)
    def test_trait_override(self):
        p = LumenaPersonality()
        assert p.traits["curiosity"] == 50
        # Les autres restent aux défauts
        assert p.traits["loyalty"] == _DEFAULT_TRAITS["loyalty"]

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_TRAIT_CURIOSITY": "-10"}, clear=True)
    def test_trait_clamped_low(self):
        p = LumenaPersonality()
        assert p.traits["curiosity"] == 0

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_TRAIT_CURIOSITY": "200"}, clear=True)
    def test_trait_clamped_high(self):
        p = LumenaPersonality()
        assert p.traits["curiosity"] == 100

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_TRAIT_CURIOSITY_ENABLED": "0"}, clear=True)
    def test_trait_disabled(self):
        p = LumenaPersonality()
        assert p.traits["curiosity"] == 0

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_TRAIT_CURIOSITY_ENABLED": "false"}, clear=True)
    def test_trait_disabled_false(self):
        p = LumenaPersonality()
        assert p.traits["curiosity"] == 0

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_TRAIT_CURIOSITY_ENABLED": "0", "LUMENA_TRAIT_CURIOSITY": "90"}, clear=True)
    def test_trait_disabled_overrides_value(self):
        """ENABLED=0 a priorité sur la valeur du slider."""
        p = LumenaPersonality()
        assert p.traits["curiosity"] == 0

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_TRAIT_CURIOSITY": "abc"}, clear=True)
    def test_trait_invalid_keeps_default(self):
        p = LumenaPersonality()
        assert p.traits["curiosity"] == _DEFAULT_TRAITS["curiosity"]


# ─── LUMENA_ENABLED_MOODS ─────────────────────────────────────────────────

class TestEnabledMoods:
    @patch.dict(os.environ, _CLEAN_ENV, clear=True)
    def test_default_all_moods(self):
        mgr = EmotionManager()
        assert mgr._enabled_moods == set(Mood)

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_ENABLED_MOODS": "happy,curious,neutral"}, clear=True)
    def test_restricted_moods(self):
        mgr = EmotionManager()
        assert mgr._enabled_moods == {Mood.HAPPY, Mood.CURIOUS, Mood.NEUTRAL}

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_ENABLED_MOODS": "happy, curious , neutral"}, clear=True)
    def test_whitespace_tolerant(self):
        mgr = EmotionManager()
        assert mgr._enabled_moods == {Mood.HAPPY, Mood.CURIOUS, Mood.NEUTRAL}

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_ENABLED_MOODS": ""}, clear=True)
    def test_empty_means_all(self):
        mgr = EmotionManager()
        assert mgr._enabled_moods == set(Mood)

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_ENABLED_MOODS": "happy,INVALID,neutral"}, clear=True)
    def test_invalid_mood_ignored(self):
        mgr = EmotionManager()
        assert Mood.HAPPY in mgr._enabled_moods
        assert Mood.NEUTRAL in mgr._enabled_moods

    @patch.dict(os.environ, _CLEAN_ENV, clear=True)
    def test_filter_mood_allowed(self):
        mgr = EmotionManager()
        assert mgr._filter_mood(Mood.HAPPY) == Mood.HAPPY

    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_ENABLED_MOODS": "neutral,happy"}, clear=True)
    def test_filter_mood_blocked(self):
        mgr = EmotionManager()
        assert mgr._filter_mood(Mood.EXCITED) == Mood.NEUTRAL


# ─── Sync personality → EmotionManager ─────────────────────────────────────

class TestSyncPersonalityEmotion:
    @patch.dict(os.environ, {**_CLEAN_ENV, "LUMENA_DEFAULT_MOOD": "happy"}, clear=True)
    def test_force_mood_from_personality(self):
        p = LumenaPersonality()
        mgr = EmotionManager()
        mgr._personality_ref = p
        mgr.force_mood(p.current_mood)
        assert mgr.state.mood == Mood.HAPPY

    @patch.dict(os.environ, _CLEAN_ENV, clear=True)
    def test_personality_ref_attribute(self):
        mgr = EmotionManager()
        # _personality_ref existe mais vaut None par défaut (P2)
        assert hasattr(mgr, "_personality_ref")
        assert mgr._personality_ref is None
        mgr._personality_ref = LumenaPersonality()
        assert mgr._personality_ref.name == "Lumena"
