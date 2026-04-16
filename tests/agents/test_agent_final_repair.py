from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.reasoning.react import ReActLoop, ToolRegistry


class _SequencedLLM:
    def __init__(self, scripted):
        self._scripted = list(scripted)
        self._last_meta = {}

    async def chat(self, _messages, **kwargs):
        if not self._scripted:
            raise AssertionError("No scripted LLM response left")
        response, finish_reason = self._scripted.pop(0)
        self._last_meta = {
            "finish_reason": finish_reason,
            "provider_used": "test-provider",
            "model_used": "test-model",
        }
        return response

    def get_meta(self):
        return dict(self._last_meta)


def _final_response(answer: str) -> str:
    return f"THOUGHT: analyse\nACTION: FINAL\nACTION_INPUT: {answer}"


@pytest.mark.asyncio
async def test_agent_final_repair_triggers_on_length_finish_reason(tmp_path: Path):
    llm = _SequencedLLM(
        [
            (_final_response("Voici la reponse finale mais elle coupe et"), "length"),
            (_final_response("Voici la reponse finale complete et coherente."), "stop"),
        ]
    )
    loop = ReActLoop(
        llm.chat,
        ToolRegistry(lumena=None, lumena_root=tmp_path),
        llm_meta_getter=llm.get_meta,
        max_final_repair_attempts=1,
    )

    result = await loop.run("Donne une reponse finale")
    run_meta = loop.get_run_meta()

    assert result == "Voici la reponse finale complete et coherente."
    assert run_meta["agent_output_incomplete"] is False
    assert run_meta["agent_repair_attempts"] == 1
    assert run_meta["agent_final_finish_reason"] == "stop"


@pytest.mark.asyncio
async def test_agent_final_repair_triggers_with_heuristic_without_length_reason(tmp_path: Path):
    llm = _SequencedLLM(
        [
            (_final_response('Voici un extrait JSON incomplet: {"phase": 1'), "stop"),
            (_final_response('Voici un extrait JSON complet: {"phase": 1}.'), "stop"),
        ]
    )
    loop = ReActLoop(
        llm.chat,
        ToolRegistry(lumena=None, lumena_root=tmp_path),
        llm_meta_getter=llm.get_meta,
        max_final_repair_attempts=1,
    )

    result = await loop.run("Repare la sortie finale")
    run_meta = loop.get_run_meta()

    assert result == 'Voici un extrait JSON complet: {"phase": 1}.'
    assert run_meta["agent_output_incomplete"] is False
    assert run_meta["agent_repair_attempts"] == 1


@pytest.mark.asyncio
async def test_agent_final_repair_sets_metadata_only_when_still_incomplete(tmp_path: Path):
    second_incomplete = "La sortie reste partielle et ne termine pas correctement avec"
    llm = _SequencedLLM(
        [
            (_final_response("Premiere tentative incomplete qui se termine avec"), "stop"),
            (_final_response(second_incomplete), "stop"),
        ]
    )
    loop = ReActLoop(
        llm.chat,
        ToolRegistry(lumena=None, lumena_root=tmp_path),
        llm_meta_getter=llm.get_meta,
        max_final_repair_attempts=1,
    )

    result = await loop.run("Test incomplet")
    run_meta = loop.get_run_meta()

    assert result == second_incomplete
    assert run_meta["agent_output_incomplete"] is True
    assert run_meta["agent_repair_attempts"] == 1
    assert isinstance(run_meta["agent_output_warning"], str)
    assert run_meta["agent_output_warning"]
    assert "potentiellement incompl" not in result
    assert "[⚠" not in result


@pytest.mark.asyncio
async def test_agent_final_does_not_repair_complete_stop_answer(tmp_path: Path):
    complete_answer = (
        "Le jeu a bien ete cree dans workspace/2026-02-06/chrono-maze-game/chrono_maze.py. "
        "Tu peux l'executer avec: python workspace/2026-02-06/chrono-maze-game/chrono_maze.py."
    )
    llm = _SequencedLLM(
        [
            (_final_response(complete_answer), "stop"),
        ]
    )
    loop = ReActLoop(
        llm.chat,
        ToolRegistry(lumena=None, lumena_root=tmp_path),
        llm_meta_getter=llm.get_meta,
        max_final_repair_attempts=1,
    )

    result = await loop.run("Cree un jeu python dans workspace")
    run_meta = loop.get_run_meta()

    assert result == complete_answer
    assert run_meta["agent_output_incomplete"] is False
    assert run_meta["agent_repair_attempts"] == 0
    assert run_meta["agent_final_finish_reason"] == "stop"


def test_single_file_creation_intent_detection(tmp_path: Path):
    loop = ReActLoop(
        lambda _messages: None,
        ToolRegistry(lumena=None, lumena_root=tmp_path),
    )

    assert loop._is_single_file_creation_request(
        "creer moi un jeu en .py dans workspace"
    ) is True
    assert loop._is_single_file_creation_request(
        "cree une landing page html css js"
    ) is False
