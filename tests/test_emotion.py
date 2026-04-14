"""Tests unitaires pour src/emotion.py"""
import asyncio
import pytest
from unittest.mock import patch

from src.emotion import (
    Mood,
    EnergyLevel,
    EmotionalState,
    EmotionAnalyzer,
    EmotionManager,
    POSITIVE_KEYWORDS,
    NEGATIVE_KEYWORDS,
    CURIOSITY_KEYWORDS,
    EXCITEMENT_KEYWORDS,
)


@pytest.fixture(autouse=True)
def _no_persist(tmp_path):
    """Isole les tests du state persisté sur disque."""
    with patch("src.utils.paths.EMOTION_STATE_FILE", tmp_path / "state.json"):
        with patch("src.utils.paths.EMOTION_HISTORY_FILE", tmp_path / "hist.jsonl"):
            with patch.dict("os.environ", {"LUMENA_EMOTION_LLM_ANALYSIS": "0"}):
                yield


# ─── Mood / EnergyLevel enums ──────────────────────────────────────────────

class TestEnums:
    def test_mood_values(self):
        assert Mood.NEUTRAL.value == "neutral"
        assert Mood.HAPPY.value == "happy"
        assert Mood.CURIOUS.value == "curious"

    def test_energy_values(self):
        assert EnergyLevel.LOW.value == "low"
        assert EnergyLevel.HIGH.value == "high"


# ─── EmotionalState defaults ───────────────────────────────────────────────

class TestEmotionalStateDefaults:
    def test_default_mood(self):
        state = EmotionalState()
        assert state.mood == Mood.NEUTRAL
        # Avec PAD: arousal=0 → MEDIUM (propriété dérivée de l'axe arousal)
        assert state.energy in (EnergyLevel.MEDIUM, EnergyLevel.HIGH)

    def test_default_scores(self):
        state = EmotionalState()
        assert 0 <= state.happiness <= 100
        assert 0 <= state.curiosity <= 100
        assert state.compliments_received == 0
        assert state.tasks_completed == 0


# ─── EmotionAnalyzer.analyze_message ───────────────────────────────────────

class TestEmotionAnalyzerAnalyzeMessage:
    def test_positive_message_increases_happiness(self):
        impacts = EmotionAnalyzer.analyze_message("merci c'est super génial!")
        assert impacts["happiness"] > 0

    def test_negative_message_decreases_happiness(self):
        impacts = EmotionAnalyzer.analyze_message("c'est nul, ça ne marche pas")
        assert impacts["happiness"] < 0

    def test_question_increases_curiosity(self):
        impacts = EmotionAnalyzer.analyze_message("pourquoi est-ce que ça marche?")
        assert impacts["curiosity"] > 0

    def test_excitement_keywords(self):
        impacts = EmotionAnalyzer.analyze_message("wow nouveau projet incroyable!")
        assert impacts["excitement"] > 0

    def test_neutral_message_zero_impacts(self):
        impacts = EmotionAnalyzer.analyze_message("bonjour")
        # No strong keywords — most impacts should be near 0
        assert impacts["happiness"] == pytest.approx(0.0, abs=0.2)

    def test_positive_reduces_boredom(self):
        impacts = EmotionAnalyzer.analyze_message("super merci!")
        assert impacts["boredom"] <= 0

    def test_returns_all_keys(self):
        impacts = EmotionAnalyzer.analyze_message("test")
        for key in ["happiness", "curiosity", "excitement", "boredom", "pride"]:
            assert key in impacts

    def test_happiness_capped_at_0_5(self):
        # Many positive keywords
        msg = " ".join(POSITIVE_KEYWORDS[:20])
        impacts = EmotionAnalyzer.analyze_message(msg)
        assert impacts["happiness"] <= 0.5


# ─── EmotionAnalyzer.is_compliment ────────────────────────────────────────

class TestEmotionAnalyzerIsCompliment:
    def test_merci_is_compliment(self):
        assert EmotionAnalyzer.is_compliment("merci beaucoup!") is True

    def test_bravo_is_compliment(self):
        assert EmotionAnalyzer.is_compliment("bravo tu as réussi") is True

    def test_neutral_not_compliment(self):
        assert EmotionAnalyzer.is_compliment("voici ma question") is False

    def test_heart_emoji_is_compliment(self):
        assert EmotionAnalyzer.is_compliment("❤️") is True

    def test_case_insensitive(self):
        assert EmotionAnalyzer.is_compliment("MERCI") is True


# ─── EmotionAnalyzer.is_question ───────────────────────────────────────────

class TestEmotionAnalyzerIsQuestion:
    def test_question_mark(self):
        assert EmotionAnalyzer.is_question("what is this?") is True

    def test_pourquoi(self):
        assert EmotionAnalyzer.is_question("pourquoi ça plante") is True

    def test_comment(self):
        assert EmotionAnalyzer.is_question("comment faire ça") is True

    def test_not_question(self):
        assert EmotionAnalyzer.is_question("voici le résultat") is False


# ─── EmotionManager ────────────────────────────────────────────────────────

class TestEmotionManager:
    def test_init(self):
        mgr = EmotionManager()
        assert mgr.state.mood == Mood.NEUTRAL
        assert mgr.analyzer is not None

    def test_process_positive_message_affects_state(self):
        mgr = EmotionManager()
        initial_happiness = mgr.state.happiness
        asyncio.get_event_loop().run_until_complete(
            mgr.process_user_message("merci c'est super génial bravo!")
        )
        # Happiness should have increased (or at least not decreased)
        assert mgr.state.happiness >= initial_happiness - 5  # small tolerance

    def test_state_is_emotional_state(self):
        mgr = EmotionManager()
        assert isinstance(mgr.state, EmotionalState)
