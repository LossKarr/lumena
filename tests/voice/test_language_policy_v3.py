"""VOICE3-6 — multilingual intent scope and persistence."""
import json

import pytest

from src.voice.v2.language_policy import LanguagePolicy


@pytest.mark.parametrize("utterance", [
    "Comment dit-on bonjour en anglais ?",
    "Traduis cette phrase en espagnol.",
    "How do you say merci in German?",
    "¿Cómo se dice bonjour en inglés?",
])
def test_translation_is_one_shot_and_never_mutates_session(utterance):
    policy = LanguagePolicy(default_language="fr")
    decision = policy.decide(utterance)
    assert decision.scope == "one_shot"
    assert decision.changed is False
    assert policy.state.session_language == "fr"


@pytest.mark.parametrize(("utterance", "expected"), [
    ("Parle maintenant uniquement en anglais", "en"),
    ("À partir de maintenant, réponds en espagnol", "es"),
    ("Speak in English from now on", "en"),
    ("Ahora habla en español", "es"),
])
def test_explicit_session_switch_is_durable_for_current_session(utterance, expected):
    policy = LanguagePolicy(default_language="fr")
    decision = policy.decide(utterance)
    assert decision.scope == "session" and decision.changed is True
    assert decision.response_language == expected
    assert policy.decide("Quelle heure est-il ?").response_language == expected


def test_profile_switch_is_atomic_and_separated_by_user_and_channel(tmp_path):
    path = tmp_path / "voice" / "languages.json"
    owner = LanguagePolicy(user_id="owner", channel="voice", state_path=path)
    decision = owner.decide("Réponds-moi toujours en anglais")
    assert decision.scope == "profile" and decision.persist_profile is True
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["profiles"]["voice:owner"] == "en"
    assert LanguagePolicy(user_id="owner", channel="voice", state_path=path).state.profile_language == "en"
    assert LanguagePolicy(user_id="guest", channel="voice", state_path=path).state.profile_language == "fr"
    assert LanguagePolicy(user_id="owner", channel="telegram", state_path=path).state.profile_language == "fr"


@pytest.mark.parametrize("utterance", [
    "J'apprends l'anglais aujourd'hui.",
    "Le fichier s'appelle spanish_notes.txt.",
    "Le mot allemand Schadenfreude est intéressant.",
    "Essaie de prononcer correctement José.",
])
def test_foreign_word_or_language_mention_never_switches_session(utterance):
    policy = LanguagePolicy(default_language="fr")
    decision = policy.decide(utterance)
    assert decision.changed is False
    assert decision.response_language == "fr"


def test_detected_input_is_observed_but_never_changes_response_by_itself():
    policy = LanguagePolicy(default_language="fr")
    decision = policy.decide(
        "Hola, necesito ayuda", detected_language="es", detected_confidence=0.96
    )
    assert decision.state.detected_input_language == "es"
    assert decision.state.detected_confidence == pytest.approx(0.96)
    assert decision.response_language == "fr"


def test_low_confidence_detection_keeps_previous_observation_and_session():
    policy = LanguagePolicy(default_language="fr")
    policy.decide("Hello", detected_language="en", detected_confidence=0.91)
    decision = policy.decide("Nom propre", detected_language="es", detected_confidence=0.20)
    assert decision.state.detected_input_language == "en"
    assert decision.response_language == "fr"


def test_one_shot_instruction_names_target_without_mutating_state():
    policy = LanguagePolicy(default_language="fr")
    decision = policy.decide("Réponds en anglais juste cette fois")
    assert decision.scope == "one_shot"
    assert "anglais" in decision.prompt_instruction()
    assert policy.state.session_language == "fr"

