"""Tests P1 — Modèle PAD (Pleasure-Arousal-Dominance)."""
import asyncio
import os
import pytest
from unittest.mock import patch

from src.emotion import (
    Mood, EnergyLevel, EmotionalState, EmotionManager,
)


@pytest.fixture(autouse=True)
def _no_persist(tmp_path):
    """Isole les tests du state persisté sur disque et désactive le LLM."""
    with patch("src.utils.paths.EMOTION_STATE_FILE", tmp_path / "state.json"):
        with patch("src.utils.paths.EMOTION_HISTORY_FILE", tmp_path / "hist.jsonl"):
            with patch.dict("os.environ", {
                "LUMENA_EMOTION_LLM_ANALYSIS": "0",
                "LUMENA_EMOTION_SENSITIVITY": "0.4012",  # multiplier ≈ 1.0
            }):
                yield


def _run(coro):
    """Helper pour exécuter une coroutine dans les tests sync."""
    return asyncio.run(coro)


# ─── EmotionalState PAD structure ─────────────────────────────────────────

class TestEmotionalStatePAD:
    def test_pad_init_zero(self):
        s = EmotionalState()
        assert s.pleasure == 0.0
        assert s.arousal == 0.0
        assert s.dominance == 0.0

    def test_pad_range(self):
        s = EmotionalState()
        s.pleasure = 0.5
        s.arousal = -0.3
        s.dominance = 0.2
        assert s.pleasure == 0.5
        assert s.arousal == -0.3

    def test_compat_happiness_50_at_neutral(self):
        s = EmotionalState()
        assert s.happiness == pytest.approx(50.0)

    def test_compat_happiness_high_at_positive_pleasure(self):
        s = EmotionalState()
        s.pleasure = 1.0
        assert s.happiness == pytest.approx(100.0)

    def test_compat_happiness_low_at_negative_pleasure(self):
        s = EmotionalState()
        s.pleasure = -1.0
        assert s.happiness == pytest.approx(0.0)

    def test_compat_boredom_high_at_negative_arousal(self):
        s = EmotionalState()
        s.arousal = -1.0
        s.pleasure = -0.3
        assert s.boredom > 60.0

    def test_compat_tiredness_high_at_negative_arousal(self):
        s = EmotionalState()
        s.arousal = -1.0
        assert s.tiredness > 90.0

    def test_compat_pride_high_at_positive_dominance(self):
        s = EmotionalState()
        s.dominance = 1.0
        s.pleasure = 0.5
        assert s.pride > 60.0

    def test_default_energy_medium(self):
        """P1: EnergyLevel défaut est MEDIUM (pas HIGH)."""
        s = EmotionalState()
        assert s.energy == EnergyLevel.MEDIUM


# ─── PAD→Mood mapping (nearest-neighbor) ──────────────────────────────────

class TestPADMoodMapping:
    def test_happy_prototype(self):
        """Point (+0.8, +0.4, +0.3) → HAPPY."""
        mgr = EmotionManager()
        mgr.state.pleasure = 0.8
        mgr.state.arousal = 0.4
        mgr.state.dominance = 0.3
        mgr.state.last_mood_change = mgr.state.last_mood_change.replace(year=2000)
        mood = mgr._determine_mood()
        assert mood == Mood.HAPPY

    def test_excited_prototype(self):
        """Point (+0.7, +0.8, +0.5) → EXCITED."""
        mgr = EmotionManager()
        mgr.state.pleasure = 0.7
        mgr.state.arousal = 0.8
        mgr.state.dominance = 0.5
        mood = mgr._determine_mood()
        assert mood == Mood.EXCITED

    def test_bored_prototype(self):
        """Point (-0.3, -0.6, -0.2) → BORED (si activé)."""
        mgr = EmotionManager()
        mgr.state.pleasure = -0.3
        mgr.state.arousal = -0.6
        mgr.state.dominance = -0.2
        mood = mgr._determine_mood()
        assert mood == Mood.BORED

    def test_tired_prototype(self):
        mgr = EmotionManager()
        mgr.state.pleasure = -0.2
        mgr.state.arousal = -0.7
        mgr.state.dominance = -0.3
        mood = mgr._determine_mood()
        assert mood == Mood.TIRED

    def test_neutral_prototype(self):
        mgr = EmotionManager()
        mgr.state.pleasure = 0.0
        mgr.state.arousal = 0.0
        mgr.state.dominance = 0.0
        mood = mgr._determine_mood()
        assert mood == Mood.NEUTRAL

    def test_curious_prototype(self):
        mgr = EmotionManager()
        mgr.state.pleasure = 0.4
        mgr.state.arousal = 0.5
        mgr.state.dominance = 0.3
        mood = mgr._determine_mood()
        assert mood == Mood.CURIOUS


# ─── _apply_delta + inertie ────────────────────────────────────────────────

class TestApplyDelta:
    def test_pleasure_increases(self):
        mgr = EmotionManager()
        mgr._apply_delta({"pleasure": 0.3}, inertia=0.0)
        assert mgr.state.pleasure == pytest.approx(0.3, abs=0.01)

    def test_inertia_slows_change(self):
        mgr = EmotionManager()
        mgr._apply_delta({"pleasure": 1.0}, inertia=0.9)
        # Avec inertia=0.9: new = 0*0.9 + 1.0*0.1 = 0.1
        assert mgr.state.pleasure == pytest.approx(0.1, abs=0.01)

    def test_clamp_max(self):
        mgr = EmotionManager()
        mgr.state.pleasure = 0.95
        mgr._apply_delta({"pleasure": 0.5}, inertia=0.0)
        assert mgr.state.pleasure <= 1.0

    def test_clamp_min(self):
        mgr = EmotionManager()
        mgr.state.pleasure = -0.95
        mgr._apply_delta({"pleasure": -0.5}, inertia=0.0)
        assert mgr.state.pleasure >= -1.0

    def test_energy_updated(self):
        mgr = EmotionManager()
        mgr._apply_delta({"arousal": 0.5}, inertia=0.0)
        assert mgr.state.energy == EnergyLevel.HIGH

    def test_low_energy(self):
        mgr = EmotionManager()
        mgr._apply_delta({"arousal": -0.5}, inertia=0.0)
        assert mgr.state.energy == EnergyLevel.LOW


# ─── EnergyLevel dérivé de arousal ────────────────────────────────────────

class TestEnergyFromArousal:
    def test_high_arousal_high_energy(self):
        mgr = EmotionManager()
        mgr.state.arousal = 0.5
        assert mgr._compute_energy() == EnergyLevel.HIGH

    def test_low_arousal_low_energy(self):
        mgr = EmotionManager()
        mgr.state.arousal = -0.5
        assert mgr._compute_energy() == EnergyLevel.LOW

    def test_neutral_arousal_medium_energy(self):
        mgr = EmotionManager()
        mgr.state.arousal = 0.1
        assert mgr._compute_energy() == EnergyLevel.MEDIUM


# ─── Decay passif ──────────────────────────────────────────────────────────

class TestDecay:
    @patch.dict(os.environ, {"LUMENA_EMOTION_DECAY": "0.5"})
    def test_decay_reduces_pleasure(self):
        mgr = EmotionManager()
        mgr.state.pleasure = 0.8
        mgr.update_passive(user_present=False)
        assert mgr.state.pleasure < 0.8

    @patch.dict(os.environ, {"LUMENA_EMOTION_DECAY": "0.5"})
    def test_decay_towards_zero(self):
        mgr = EmotionManager()
        mgr.state.pleasure = 0.8
        mgr.state.arousal = 0.6
        for _ in range(20):
            mgr.update_passive(user_present=False)
        assert abs(mgr.state.pleasure) < 0.2

    def test_decay_custom_env(self):
        with patch.dict(os.environ, {"LUMENA_EMOTION_DECAY": "0.1"}):
            mgr = EmotionManager()
            mgr.state.pleasure = 0.5
            mgr.update_passive(user_present=False)
            # Avec decay=0.1: pleasure * (1-0.1) = 0.45
            assert mgr.state.pleasure < 0.5


# ─── process_user_message → PAD ───────────────────────────────────────────

class TestProcessUserMessagePAD:
    def test_compliment_raises_pleasure(self):
        mgr = EmotionManager()
        _run(mgr.process_user_message("merci tu es super génial bravo"))
        assert mgr.state.pleasure > 0.0

    def test_negative_message_lowers_pleasure(self):
        mgr = EmotionManager()
        _run(mgr.process_user_message("c'est nul mauvais bug erreur"))
        assert mgr.state.pleasure < 0.0

    def test_question_raises_arousal(self):
        mgr = EmotionManager()
        _run(mgr.process_user_message("pourquoi comment qu'est-ce que c'est?"))
        assert mgr.state.arousal > 0.0

    def test_last_interaction_updated(self):
        from datetime import datetime, timedelta
        mgr = EmotionManager()
        mgr.state.last_interaction = datetime.now() - timedelta(hours=1)
        _run(mgr.process_user_message("bonjour"))
        assert (datetime.now() - mgr.state.last_interaction).total_seconds() < 5

    def test_interactions_count_increments(self):
        mgr = EmotionManager()
        _run(mgr.process_user_message("test"))
        assert mgr.state.interactions_count == 1


# ─── process_own_response → PAD ──────────────────────────────────────────

class TestProcessOwnResponsePAD:
    def test_task_completed_raises_pleasure_dominance(self):
        mgr = EmotionManager()
        _run(mgr.process_own_response("J'ai fait la tâche.", task_completed=True))
        assert mgr.state.pleasure > 0.0
        assert mgr.state.dominance > 0.0

    def test_tasks_completed_counter(self):
        mgr = EmotionManager()
        _run(mgr.process_own_response("OK fait.", task_completed=True))
        assert mgr.state.tasks_completed == 1


# ─── get_emotional_context format compact ─────────────────────────────────

class TestGetEmotionalContextPAD:
    def test_contains_humeur_label(self):
        mgr = EmotionManager()
        ctx = mgr.get_emotional_context()
        assert "Humeur=" in ctx

    def test_contains_pad_values(self):
        mgr = EmotionManager()
        ctx = mgr.get_emotional_context()
        assert "PAD(" in ctx

    def test_contains_energie(self):
        mgr = EmotionManager()
        ctx = mgr.get_emotional_context()
        assert "Énergie=" in ctx

    def test_modifier_present_for_happy(self):
        mgr = EmotionManager()
        mgr.state.mood = Mood.HAPPY
        ctx = mgr.get_emotional_context()
        assert "chaleureuse" in ctx.lower() or "Comportement" in ctx

    def test_no_excessive_newlines(self):
        mgr = EmotionManager()
        ctx = mgr.get_emotional_context()
        assert len(ctx.splitlines()) <= 3


# ─── get_stats PAD keys ────────────────────────────────────────────────────

class TestGetStatsPAD:
    def test_pad_keys_present(self):
        mgr = EmotionManager()
        stats = mgr.get_stats()
        assert "pleasure" in stats
        assert "arousal" in stats
        assert "dominance" in stats

    def test_compat_keys_present(self):
        """Les clés legacy (happiness, boredom, etc.) restent pour la compat."""
        mgr = EmotionManager()
        stats = mgr.get_stats()
        for k in ("happiness", "curiosity", "boredom", "tiredness", "pride"):
            assert k in stats, f"Clé compat manquante: {k}"
