"""Tests unitaires pour src/personality.py"""
import pytest

from src.personality import LumenaPersonality, Mood, EnergyLevel


class TestMoodEnum:
    def test_all_moods_unique(self):
        values = [m.value for m in Mood]
        assert len(values) == len(set(values))

    def test_neutral_present(self):
        assert Mood.NEUTRAL.value == "neutral"


class TestEnergyLevel:
    def test_levels_present(self):
        assert EnergyLevel.LOW.value == "low"
        assert EnergyLevel.MEDIUM.value == "medium"
        assert EnergyLevel.HIGH.value == "high"


class TestLumenaPersonality:
    def test_default_name(self):
        p = LumenaPersonality()
        assert p.name == "Lumena"
        assert p.nickname == "Lumi"

    def test_default_mood(self):
        p = LumenaPersonality()
        assert p.current_mood == Mood.NEUTRAL
        assert p.energy_level == EnergyLevel.HIGH

    def test_traits_in_range(self):
        p = LumenaPersonality()
        for trait, value in p.traits.items():
            assert 0 <= value <= 100, f"Trait {trait} hors plage: {value}"

    def test_get_system_prompt_nonempty(self):
        p = LumenaPersonality()
        prompt = p.get_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 50
        assert "Lumena" in prompt

    def test_system_prompt_reflects_mood(self):
        p = LumenaPersonality()
        p.current_mood = Mood.HAPPY
        prompt = p.get_system_prompt()
        assert "joyeu" in prompt.lower() or "enthusiast" in prompt.lower() or "happy" in prompt.lower()

    def test_favorite_topics_nonempty(self):
        p = LumenaPersonality()
        assert len(p.favorite_topics) > 0

    def test_emoji_frequency_valid(self):
        p = LumenaPersonality()
        assert 0.0 <= p.emoji_frequency <= 1.0

    def test_get_greeting(self):
        p = LumenaPersonality()
        greet = p.get_greeting()
        assert isinstance(greet, str)
        assert len(greet) > 0

    def test_update_mood(self):
        p = LumenaPersonality()
        p.update_mood(Mood.HAPPY)
        assert p.current_mood == Mood.HAPPY
