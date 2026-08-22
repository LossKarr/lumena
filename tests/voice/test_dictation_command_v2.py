from __future__ import annotations

import pytest

from src.voice.v2.dictation import extract_dictation_send_command


@pytest.mark.parametrize("command", ["Envoyer", "envoyer.", "ENVOYEZ !"])
def test_command_only_can_target_existing_composer_text(command):
    decision = extract_dictation_send_command(command)
    assert decision.should_send is True
    assert decision.text == ""


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("Bonjour Lumena. Envoyer.", "Bonjour Lumena."),
        ("Mon message finit par envoyer. Envoyez.", "Mon message finit par envoyer."),
        ("Tout est prêt ! Envoyer", "Tout est prêt !"),
        ("Une dernière chose ? Envoyez...", "Une dernière chose ?"),
    ],
)
def test_final_sentence_command_is_removed(spoken, expected):
    decision = extract_dictation_send_command(spoken)
    assert decision.should_send is True
    assert decision.text == expected
    assert decision.boundary == "sentence"


def test_last_whisper_segment_proves_boundary_without_punctuation():
    decision = extract_dictation_send_command(
        "Bonjour Lumena Envoyer",
        [{"text": "Bonjour Lumena"}, {"text": "Envoyer"}],
    )
    assert decision.should_send is True
    assert decision.text == "Bonjour Lumena"
    assert decision.boundary == "segment"


@pytest.mark.parametrize(
    "spoken",
    [
        "Je veux envoyer ce message",
        "Le mot envoyer est important",
        "Envoyer mon message",
        "Mon message finit par envoyer",
        "Je terminerai par envoyez demain",
    ],
)
def test_command_word_inside_normal_sentence_never_sends(spoken):
    decision = extract_dictation_send_command(spoken)
    assert decision.should_send is False
    assert decision.text == spoken


def test_non_isolated_last_segment_never_sends():
    decision = extract_dictation_send_command(
        "Je veux envoyer", [{"text": "Je veux envoyer"}]
    )
    assert decision.should_send is False
