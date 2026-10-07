"""VOICE3-9 — output language guard."""
import pytest

from src.voice.v2.language_guard import check_response_language, detect_text_language


@pytest.mark.parametrize(("text", "expected"), [
    ("Bonjour, je vais vérifier cela maintenant et je reviens avec le résultat.", "fr"),
    ("Hello, I will check this now and return with the result.", "en"),
    ("Hola, voy a comprobar esto ahora y vuelvo con el resultado.", "es"),
])
def test_detects_supported_generated_languages(text, expected):
    detected, confidence = detect_text_language(text)
    assert detected == expected
    assert confidence >= 0.60


def test_mostly_spanish_answer_is_blocked_in_french_session():
    check = check_response_language(
        "Hola, esto está hecho. Ahora puedo continuar con la siguiente tarea.", "fr"
    )
    assert check.compliant is False
    assert check.detected == "es"
    assert check.reason == "language_mismatch"


def test_expected_language_is_accepted():
    check = check_response_language(
        "Bonjour, c'est fait. Je peux maintenant continuer avec la suite.", "fr"
    )
    assert check.compliant is True and check.detected == "fr"


@pytest.mark.parametrize("short_text", ["OK.", "Lumena.", "Hello !", "Sí."])
def test_short_or_ambiguous_text_is_not_false_blocked(short_text):
    assert check_response_language(short_text, "fr").compliant is True
