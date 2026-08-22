# -*- coding: utf-8 -*-
"""LOT PG-1 + DS-1 (run SkiLoc 2026-07-12, log logslumena A.txt).

DS-1 — deepseek émet parfois ses tool-calls au format DSML natif EN TEXTE :
le parser récupérait le nom d'outil mais PERDAIT les paramètres
(« Paramètre(s) requis manquant(s) » ×8 dans le run) et du DSML brut a été
streamé dans un FINAL chat. On CONVERTIT au lieu de jeter.

PG-1 — le plan guard « Aucune progression en 10 itérations » a coupé SkiLoc
avec 2 048 s de budget restant, 5 s après un tir PYTEST GATE accordé, alors
que le lead venait de corriger (mutations réelles). La stagnation mesurait le
PLAN (tâches PUBLISH-ONLY non marquables), pas le TRAVAIL.
"""

import inspect

import pytest

from src.llm.output_normalizer import (
    convert_dsml_tool_calls,
    dsml_first_action,
    strip_dsml_markup,
)
from src.subagents.mission_budget import no_progress_rescue_allowed


# Bloc DSML VERBATIM du run SkiLoc (04:40:26).
_DSML_SINGLE = (
    "Je vais lire la suite du fichier pour voir les stubs restants.\n"
    "<｜｜DSML｜｜tool_calls>\n"
    '<｜｜DSML｜｜invoke name="read_file">\n'
    '<｜｜DSML｜｜parameter name="path" string="true">tests/test_api.py</｜｜DSML｜｜parameter>\n'
    "</｜｜DSML｜｜invoke>\n"
    "</｜｜DSML｜｜tool_calls>"
)

_DSML_MULTI = (
    "<｜｜DSML｜｜tool_calls>\n"
    '<｜｜DSML｜｜invoke name="list_directory">\n'
    '<｜｜DSML｜｜parameter name="path" string="true">tests</｜｜DSML｜｜parameter>\n'
    "</｜｜DSML｜｜invoke>\n"
    '<｜｜DSML｜｜invoke name="read_files_batch">\n'
    '<｜｜DSML｜｜parameter name="paths" string="false">["a.py", "b.py"]</｜｜DSML｜｜parameter>\n'
    "</｜｜DSML｜｜invoke>\n"
    "</｜｜DSML｜｜tool_calls>"
)


# ═══════════════ DS-1.1 — conversion pure ═══════════════

class TestConvertDsml:
    def test_single_invoke_becomes_action_with_params(self):
        out = convert_dsml_tool_calls(_DSML_SINGLE)
        assert "ACTION: read_file" in out
        assert '"path": "tests/test_api.py"' in out
        assert "DSML" not in out  # plus aucun marqueur

    def test_multi_invoke_becomes_multiple_actions(self):
        out = convert_dsml_tool_calls(_DSML_MULTI)
        assert "ACTION: list_directory" in out
        assert "ACTION: read_files_batch" in out
        # string="false" → valeur JSON (liste), pas une chaîne brute
        assert '"paths": ["a.py", "b.py"]' in out
        assert "DSML" not in out

    def test_text_without_dsml_strictly_unchanged(self):
        plain = "THOUGHT: rien à voir\nACTION: read_file\nACTION_INPUT: {\"path\": \"x\"}"
        assert convert_dsml_tool_calls(plain) is plain  # même objet : zéro coût

    def test_residual_markers_removed_even_unclosed(self):
        broken = "texte <｜｜DSML｜｜tool_calls>\n<｜｜DSML｜｜invoke name=\"x\">"
        out = convert_dsml_tool_calls(broken)
        assert "DSML" not in out


class TestStripDsml:
    def test_final_never_shows_dsml(self):
        final = "Voici ma réponse.\n\n" + _DSML_SINGLE
        out = strip_dsml_markup(final)
        assert "DSML" not in out
        assert "Voici ma réponse." in out

    def test_plain_final_unchanged(self):
        plain = "Réponse normale sans le moindre marqueur."
        assert strip_dsml_markup(plain) is plain


class TestDsmlFirstAction:
    def test_codeagent_gets_full_action_dict(self):
        action = dsml_first_action(_DSML_SINGLE)
        assert action == {"action": "read_file", "path": "tests/test_api.py"}

    def test_none_without_dsml(self):
        assert dsml_first_action('{"action": "write_file"}') is None


# ═══════════════ DS-1.2 — intégration parse_response ═══════════════

class TestParseResponseDsml:
    def test_dsml_parsed_as_real_action_with_args(self):
        from src.reasoning.response_parser import parse_response
        thought, action, _halluc, pending = parse_response(
            "THOUGHT: je lis le fichier\n" + _DSML_SINGLE
        )
        assert action.tool_name == "read_file"
        assert action.tool_args.get("path") == "tests/test_api.py"

    def test_dsml_multi_queued(self):
        from src.reasoning.response_parser import parse_response
        _t, action, _h, pending = parse_response("THOUGHT: deux appels\n" + _DSML_MULTI)
        assert action.tool_name == "list_directory"
        assert len(pending) == 1
        assert pending[0][0] == "read_files_batch"
        assert pending[0][1].get("paths") == ["a.py", "b.py"]


# ═══════════════ DS-1.3 — filet CodeAgent ═══════════════

def test_codeagent_parse_action_json_handles_dsml():
    from src.agents.sub_agent import _parse_action_json
    action = _parse_action_json(_DSML_SINGLE)
    assert action == {"action": "read_file", "path": "tests/test_api.py"}


def test_codeagent_parse_action_json_still_prefers_json():
    from src.agents.sub_agent import _parse_action_json
    action = _parse_action_json('{"action": "write_file", "path": "a.py", "content": "x"}')
    assert action["action"] == "write_file"


# ═══════════════ PG-1.b — helper pur de sauvetage ═══════════════

class TestNoProgressRescue:
    def test_skiloc_case_rescued(self):
        """Le cas EXACT du run : mission, tests présents, shots=1, 2 048 s
        restantes, ratio 0,15, jamais sauvé → sauvetage."""
        assert no_progress_rescue_allowed(
            is_mission=True, tests_present=True, gate_shots=1,
            remaining_s=2048, ratio_used=0.15, already_rescued=False) is True

    def test_only_once(self):
        assert no_progress_rescue_allowed(
            is_mission=True, tests_present=True, gate_shots=1,
            remaining_s=2048, ratio_used=0.15, already_rescued=True) is False

    def test_chat_never_rescued(self):
        """Hors mission : le garde anti-boucle chat/navigateur reste INTACT."""
        assert no_progress_rescue_allowed(
            is_mission=False, tests_present=True, gate_shots=0,
            remaining_s=2048, ratio_used=0.1, already_rescued=False) is False

    def test_no_tests_no_rescue(self):
        assert no_progress_rescue_allowed(
            is_mission=True, tests_present=False, gate_shots=0,
            remaining_s=2048, ratio_used=0.1, already_rescued=False) is False

    def test_gate_cap_respected(self):
        assert no_progress_rescue_allowed(
            is_mission=True, tests_present=True, gate_shots=4,
            remaining_s=2048, ratio_used=0.1, already_rescued=False) is False

    def test_short_budget_no_rescue(self):
        assert no_progress_rescue_allowed(
            is_mission=True, tests_present=True, gate_shots=1,
            remaining_s=120, ratio_used=0.9, already_rescued=False) is False

    def test_no_deadline_mission_rescued(self):
        assert no_progress_rescue_allowed(
            is_mission=True, tests_present=True, gate_shots=0,
            remaining_s=None, ratio_used=None, already_rescued=False) is True


# ═══════════════ PG-1.a / PG-1.c — invariants structurels ═══════════════

def _react_source() -> str:
    import src.reasoning.react as react_mod
    return inspect.getsource(react_mod)


def test_pg1a_mutation_tools_and_reset_wired():
    from src.reasoning.react import _PG1_MUTATION_TOOLS
    # les mutations du run SkiLoc qui comptaient « sans progression »
    for tool in ("insert_at_anchor", "write_file", "edit_file",
                 "publish_mission_workspace", "apply_patches"):
        assert tool in _PG1_MUTATION_TOOLS, tool
    # les lectures ne remettent JAMAIS le compteur à zéro par cette voie
    assert "read_file" not in _PG1_MUTATION_TOOLS
    src = _react_source()
    assert "_pg1_mutation_ok" in src
    # la mutation réussie remet à ZÉRO (pas -1)
    i = src.find("_pg1_mutation_ok = bool(")
    assert i > 0
    block = src[i:i + 700]
    assert "self._iterations_without_progress = 0" in block


def test_pg1b_rescue_wired_before_forced_final():
    src = _react_source()
    i_rescue = src.find("no_progress_rescue_allowed")
    i_force = src.find("Aucune progression en {} iterations, FINAL force")
    assert i_rescue != -1 and i_force != -1
    assert i_rescue < i_force  # le sauvetage est tenté AVANT la coupe
    assert "_no_progress_rescue_used" in src
    assert "plan_no_progress_rescue" in src


def test_pg1c_gate_relaunch_resets_stagnation():
    src = _react_source()
    # chaque tir accordé (pytest ×2, js, browser — voie FINAL LLM) reset le compteur
    assert src.count("PG-1.c") >= 4
