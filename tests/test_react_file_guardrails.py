from pathlib import Path
import json
import sys
import asyncio

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.reasoning.react import ToolRegistry, ReActLoop, ActionType
from src.runtime.task_orchestrator import TaskOrchestrator


@pytest.mark.asyncio
async def test_tool_registry_write_file_keeps_distinct_relative_paths(tmp_path: Path):
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)

    obs_client = await registry.execute("write_file", {
        "path": "client/main.py",
        "content": "print('client')",
    })
    obs_server = await registry.execute("write_file", {
        "path": "server/main.py",
        "content": "print('server')",
    })

    result_client = obs_client.content
    result_server = obs_server.content

    # Encoding can vary on Windows consoles; only assert that no validation error occurred.
    assert "validation" not in result_client.lower()
    assert "validation" not in result_server.lower()

    client_path = registry.file_guardrails.find_workspace_match(Path("client/main.py"))
    server_path = registry.file_guardrails.find_workspace_match(Path("server/main.py"))

    assert client_path is not None
    assert server_path is not None
    assert client_path != server_path
    assert client_path.read_text(encoding="utf-8") == "print('client')"
    assert server_path.read_text(encoding="utf-8") == "print('server')"


@pytest.mark.asyncio
async def test_tool_registry_write_file_fails_when_guardrails_fail(tmp_path: Path):
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)

    observation = await registry.execute("write_file", {"path": "demo.py", "content": ""})

    assert observation.success is False
    assert "validation" in observation.content.lower() or "vide" in observation.content.lower()


@pytest.mark.asyncio
async def test_react_read_file_supports_line_pagination(tmp_path: Path):
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    target = tmp_path / "long_file.py"
    content = "\n".join([f"line_{i}" for i in range(1, 901)]) + "\n"
    target.write_text(content, encoding="utf-8")

    # Sans range → lecture complète (pas de SUITE DISPONIBLE)
    obs_full = await registry.execute("read_file", {"path": str(target)})
    assert obs_full.success
    assert "line_1" in obs_full.content
    assert "line_900" in obs_full.content

    # Avec range explicite → pagination (SUITE DISPONIBLE sur fin de range < fin de fichier)
    obs1 = await registry.execute("read_file", {"path": str(target), "start_line": 1, "end_line": 350})
    first_page = obs1.content
    assert "lignes 1-350/900" in first_page
    assert "line_1" in first_page
    assert "line_350" in first_page
    assert "SUITE DISPONIBLE" in first_page

    obs2 = await registry.execute("read_file", {"path": str(target), "start_line": 351, "end_line": 700})
    second_page = obs2.content
    assert "lignes 351-700/900" in second_page
    assert "line_351" in second_page
    assert "line_700" in second_page


def test_react_parser_handles_balanced_action_input_and_final_blocks(tmp_path: Path):
    async def _dummy_llm(_messages, **kwargs):
        return "unused"

    loop = ReActLoop(_dummy_llm, ToolRegistry(lumena=None, lumena_root=tmp_path))

    payload = {
        "path": "index.html",
        "content": "<script>const state = {\"a\":1};\nconsole.log('FINAL');</script>",
    }
    response_tool = (
        "THOUGHT: Je cree le fichier\n"
        "ACTION: write_file\n"
        f"ACTION_INPUT: {json.dumps(payload)}"
    )

    thought_tool, action_tool = loop._parse_response(response_tool)
    assert thought_tool.content
    assert action_tool.action_type == ActionType.TOOL_CALL
    assert action_tool.tool_name == "write_file"
    assert action_tool.tool_args["path"] == "index.html"
    assert "console.log('FINAL')" in action_tool.tool_args["content"]

    response_final = (
        "THOUGHT: C'est fini\n"
        "ACTION: FINAL\n"
        "ACTION_INPUT: Tous les fichiers ont ete crees."
    )
    _thought_final, action_final = loop._parse_response(response_final)
    assert action_final.action_type == ActionType.FINAL_ANSWER
    assert "Tous les fichiers" in (action_final.answer or "")


def test_react_parser_supports_clarify_action(tmp_path: Path):
    async def _dummy_llm(_messages, **kwargs):
        return "unused"

    loop = ReActLoop(_dummy_llm, ToolRegistry(lumena=None, lumena_root=tmp_path))

    response = (
        "THOUGHT: Il manque un detail essentiel\n"
        "ACTION: CLARIFY\n"
        "ACTION_INPUT: Veux-tu un mode rapide ou approfondi ?"
    )

    _thought, action = loop._parse_response(response)
    assert action.action_type == ActionType.CLARIFY
    assert "mode rapide" in (action.answer or "")


@pytest.mark.asyncio
async def test_tool_registry_parallel_tools_read_only(tmp_path: Path):
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    first = tmp_path / "first.txt"
    second = tmp_path / "second.txt"
    first.write_text("alpha", encoding="utf-8")
    second.write_text("beta", encoding="utf-8")

    observation = await registry.execute(
        "parallel_tools",
        {
            "tool_calls": [
                {"name": "read_file", "args": {"path": str(first)}},
                {"name": "read_file", "args": {"path": str(second)}},
            ]
        },
    )

    assert observation.success is True
    assert "parallel_tools" in observation.content
    assert "read_file" in observation.content


@pytest.mark.asyncio
async def test_tool_registry_parallel_tools_blocks_recursion(tmp_path: Path):
    """Only parallel_tools itself is blocked (anti-recursion). Other tools are allowed."""
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    observation = await registry.execute(
        "parallel_tools",
        {
            "tool_calls": [
                {"name": "parallel_tools", "args": {}},
            ]
        },
    )
    assert "interdit" in observation.content.lower() or "cursion" in observation.content.lower()


@pytest.mark.asyncio
async def test_tool_registry_parallel_tools_allows_mutation(tmp_path: Path):
    """With blocklist approach, write_file is allowed — Lumena is autonomous."""
    registry = ToolRegistry(lumena=None, lumena_root=tmp_path)
    observation = await registry.execute(
        "parallel_tools",
        {
            "tool_calls": [
                {"name": "write_file", "args": {"path": "x.txt", "content": "x"}},
            ]
        },
    )
    assert observation.success is True
    assert "non autorise" not in observation.content.lower()


@pytest.mark.asyncio
async def test_react_loop_updates_task_orchestrator_states(tmp_path: Path):
    async def _final_llm(_messages, **kwargs):
        return (
            "THOUGHT: Je termine.\n"
            "ACTION: FINAL\n"
            "ACTION_INPUT: Réponse finale de test."
        )

    orchestrator = TaskOrchestrator()
    task = orchestrator.start_task(
        conversation_id="conv_react_orch",
        channel="ide",
        message_preview="react integration",
        task_id="task_react_orch",
    )
    assert task.state == "queued"

    loop = ReActLoop(
        llm_chat_func=_final_llm,
        tools=ToolRegistry(lumena=None, lumena_root=tmp_path),
        task_orchestrator=orchestrator,
        task_id=task.task_id,
    )

    response = await loop.run("termine la tache")
    assert "finale" in response.lower()

    payload = orchestrator.get_task(task.task_id)
    assert payload is not None
    assert payload["state"] == "done"
    assert payload["last_checkpoint"] is not None


@pytest.mark.asyncio
async def test_react_loop_marks_waiting_io_on_timeout(tmp_path: Path):
    async def _slow_llm(_messages, **kwargs):
        await asyncio.sleep(0.15)
        return "THOUGHT: lent\nACTION: FINAL\nACTION_INPUT: ok"

    orchestrator = TaskOrchestrator()
    task = orchestrator.start_task(
        conversation_id="conv_react_timeout",
        channel="web",
        message_preview="timeout react",
        task_id="task_react_timeout",
    )

    loop = ReActLoop(
        llm_chat_func=_slow_llm,
        tools=ToolRegistry(lumena=None, lumena_root=tmp_path),
        task_orchestrator=orchestrator,
        task_id=task.task_id,
    )
    loop.max_iterations = 1
    # Force un timeout global court pour valider la transition waiting_io.
    loop.timeout_seconds = 0.01
    response = await loop.run("tache longue")
    assert "temps" in response.lower() or "timeout" in response.lower()

    payload = orchestrator.get_task(task.task_id)
    assert payload is not None
    assert payload["state"] == "waiting_io"


@pytest.mark.asyncio
async def test_react_loop_clarify_sets_waiting_io_with_checkpoint(tmp_path: Path):
    async def _clarify_llm(_messages, **kwargs):
        return (
            "THOUGHT: Je dois demander une precision avant de continuer.\n"
            "ACTION: CLARIFY\n"
            "ACTION_INPUT: Quel format final veux-tu, markdown ou texte brut ?"
        )

    orchestrator = TaskOrchestrator()
    task = orchestrator.start_task(
        conversation_id="conv_react_clarify",
        channel="ide",
        message_preview="clarify flow",
        task_id="task_react_clarify",
    )

    loop = ReActLoop(
        llm_chat_func=_clarify_llm,
        tools=ToolRegistry(lumena=None, lumena_root=tmp_path),
        task_orchestrator=orchestrator,
        task_id=task.task_id,
    )
    loop.max_iterations = 1

    response = await loop.run("continue et demande une precision si necessaire")
    assert "format final" in response.lower()

    payload = orchestrator.get_task(task.task_id)
    assert payload is not None
    assert payload["state"] == "waiting_io"
    assert payload["last_error"] == "clarification_required"
    assert isinstance(payload["last_checkpoint"], dict)
    assert payload["last_checkpoint"]["phase"] == "clarify_waiting_io"
    assert "markdown" in payload["last_checkpoint"].get("clarification_question", "").lower()
