"""Resolver reasoning_content (sans contrat) : extraction protocole/JSON, return "".

Fidèle au comportement historique DeepSeek généralisé aux 6 providers :
- content prioritaire ;
- content vide → THOUGHT/ACTION (borné, hors FINAL) puis dernier JSON d'action ;
- rien d'exploitable → "" (JAMAIS la prose brute, JAMAIS de levée/cascade).
"""
from __future__ import annotations

import json
import pytest

from src.llm.multi_provider import (
    _resolve_assistant_text,
    _extract_last_action_json,
)


# ── content prioritaire ───────────────────────────────────────────────────────
def test_content_present_wins_over_reasoning():
    msg = {"content": "Il fait 18°C", "reasoning_content": "blabla interne"}
    assert _resolve_assistant_text(msg) == "Il fait 18°C"


# ── prose brute JAMAIS exposée → "" (anti-fuite, ne lève pas) ─────────────────
def test_descriptive_prose_returns_empty_not_leaked():
    prose = "L'utilisateur veut la météo. Je dois réfléchir et appeler get_weather."
    msg = {"content": "", "reasoning_content": prose}
    assert _resolve_assistant_text(msg, provider="zai", model="glm-5.2") == ""


def test_long_prose_returns_empty():
    msg = {"content": "", "reasoning_content": "x" * 500}
    assert _resolve_assistant_text(msg) == ""


# ── extraction THOUGHT/ACTION (auto, sans contrat) ────────────────────────────
def test_extracts_thought_action():
    reasoning = "Je réfléchis...\nTHOUGHT: créer le fichier\nACTION: write_file\nACTION_INPUT: {\"path\": \"x\"}"
    out = _resolve_assistant_text({"content": "", "reasoning_content": reasoning})
    assert out.startswith("THOUGHT:")
    assert "ACTION: write_file" in out


def test_action_input_bounded_at_next_marker():
    reasoning = (
        "THOUGHT: étape 1\nACTION: read_file\nACTION_INPUT: {\"path\": \"a\"}\n"
        "THOUGHT: maintenant j'analyse en privé\nACTION: write_file\nACTION_INPUT: {\"path\": \"b\"}"
    )
    out = _resolve_assistant_text({"content": "", "reasoning_content": reasoning})
    assert "read_file" in out
    assert "write_file" not in out          # borné au 1er bloc
    assert "analyse en privé" not in out    # pas de suffixe privé


def test_final_in_reasoning_not_extracted():
    # ACTION FINAL = texte libre non borné → on ne l'extrait pas ; pas de JSON → ""
    reasoning = (
        "THOUGHT: je réponds\nACTION: FINAL\nACTION_INPUT: réponse utilisateur\n"
        "puis réflexion interne privée que personne ne doit voir"
    )
    out = _resolve_assistant_text({"content": "", "reasoning_content": reasoning})
    assert out == ""
    assert "privée" not in out


# ── extraction JSON d'action (CodeAgent) ──────────────────────────────────────
def test_extracts_last_action_json():
    reasoning = 'étape 1 {"action": "read_file", "path": "a"} puis {"action": "write_file", "path": "b"}'
    out = _resolve_assistant_text({"content": "", "reasoning_content": reasoning})
    assert json.loads(out)["action"] == "write_file"  # le DERNIER


def test_extract_last_action_json_helper():
    assert _extract_last_action_json('{"action":"a"} {"action":"b"}')["action"] == "b"
    assert _extract_last_action_json("rien") is None


# ── types non-string : pas de crash, "" ───────────────────────────────────────
@pytest.mark.parametrize("bad_content", [[], {}, [1, 2], {"a": 1}, 123, 0.5, True, ["bloc"]])
def test_non_string_content_returns_empty(bad_content):
    msg = {"content": bad_content, "reasoning_content": "prose interne"}
    assert _resolve_assistant_text(msg) == ""


@pytest.mark.parametrize("bad_reasoning", [[], {}, [1], 42])
def test_non_string_reasoning_returns_empty(bad_reasoning):
    assert _resolve_assistant_text({"content": "", "reasoning_content": bad_reasoning}) == ""


def test_whitespace_content_treated_as_empty():
    assert _resolve_assistant_text({"content": "   \n  ", "reasoning_content": "prose"}) == ""


def test_str_message_normalized():
    assert _resolve_assistant_text("réponse directe") == "réponse directe"
    assert _resolve_assistant_text("") == ""
