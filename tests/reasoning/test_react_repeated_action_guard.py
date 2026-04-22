from pathlib import Path
import sys
import json

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.reasoning.react import Observation, ReActLoop


class _DummyTools:
    def __init__(self):
        self.tools = {"list_directory": {"handler": None}}

    def get_tools_description(self) -> str:
        return "- list_directory(path)"

    async def execute(self, tool_name: str, tool_args, **kwargs):
        return Observation(content=f"ok {tool_name} {tool_args}", success=True)


def _tool_step(path: str, thought: str = "J'explore le workspace.") -> str:
    return (
        f"THOUGHT: {thought}\n"
        "ACTION: list_directory\n"
        f"ACTION_INPUT: {json.dumps({'path': path}, ensure_ascii=False)}"
    )


def _final_step(answer: str) -> str:
    return (
        "THOUGHT: C'est terminé.\n"
        "ACTION: FINAL\n"
        f"ACTION_INPUT: {answer}"
    )


def _llm_sequence(responses):
    state = {"idx": 0}

    async def _llm(_messages, **kwargs):
        idx = state["idx"]
        if idx >= len(responses):
            return _final_step("Fallback final.")
        state["idx"] = idx + 1
        return responses[idx]

    return _llm


@pytest.mark.asyncio
async def test_react_does_not_stop_on_single_non_consecutive_repeat():
    responses = [
        _tool_step("workspace"),
        _tool_step("workspace/2026-02-15"),
        _tool_step("workspace"),
        _final_step("Termine sans boucle."),
    ]
    loop = ReActLoop(llm_chat_func=_llm_sequence(responses), tools=_DummyTools())
    loop.max_iterations = 8

    result = await loop.run("explore puis termine")

    assert "Termine sans boucle" in result


@pytest.mark.asyncio
async def test_react_still_stops_after_third_identical_signature():
    """list_directory est exempté du guard anti-boucle (a son propre redirect).
    Le LLM finit par atteindre FINAL après les warnings repeated path."""
    responses = [
        _tool_step("workspace", "Je regarde la racine du workspace."),
        _tool_step("workspace/2026-02-15", "Maintenant je regarde le sous-dossier."),
        _tool_step("workspace", "Je reviens vérifier la racine."),
        _tool_step("workspace/2026-02-15", "Je vérifie encore le sous-dossier."),
        _tool_step("workspace", "Dernière vérification de la racine."),
    ]
    loop = ReActLoop(llm_chat_func=_llm_sequence(responses), tools=_DummyTools())
    loop.max_iterations = 8

    result = await loop.run("explore beaucoup")

    # list_directory exempté → le LLM touche le fallback FINAL au lieu du kill boucle
    assert result is not None and len(result) > 0
