import asyncio
from types import SimpleNamespace

import pytest

from src.reasoning.react import ReActLoop
from src.runtime.task_orchestrator import TaskOrchestrator
from src.runtime.task_steering import queue_steering


def _voice_task(orch):
    rec = orch.start_task(
        conversation_id="voice-conv", channel="voice", message_preview="travail",
        metadata={"kind": "voice_turn", "objective": "travail"},
    )
    orch.mark_running(rec.task_id)
    return rec.task_id


@pytest.mark.asyncio
async def test_react_consumes_voice_steering_once_before_prompt():
    orch = TaskOrchestrator(persistence_path=None)
    task_id = _voice_task(orch)
    queue_steering(orch, task_id, "add_constraint", {"text": "utilise le format CSV"})
    prompts = []

    async def llm(messages, stop=None):
        prompts.append(messages[-1]["content"])
        return "THOUGHT: fini\nACTION: FINAL\nACTION_INPUT: Voici le format demande."

    loop = ReActLoop(
        llm, task_orchestrator=orch, task_id=task_id,
        runtime_ctx=SimpleNamespace(channel="voice"), max_iterations=2,
    )
    assert await loop.run("prepare les donnees") == "Voici le format demande."
    assert "utilise le format CSV" in prompts[0]
    command = orch.get_task(task_id)["metadata"]["steering_commands"][0]
    assert command["status"] == "applied"


@pytest.mark.asyncio
async def test_react_pause_waits_without_calling_llm_then_resumes():
    orch = TaskOrchestrator(persistence_path=None)
    task_id = _voice_task(orch)
    queue_steering(orch, task_id, "pause")
    called = asyncio.Event()

    async def llm(messages, stop=None):
        called.set()
        return "THOUGHT: fini\nACTION: FINAL\nACTION_INPUT: Repris proprement."

    loop = ReActLoop(
        llm, task_orchestrator=orch, task_id=task_id,
        runtime_ctx=SimpleNamespace(channel="voice"), max_iterations=2,
    )
    run = asyncio.create_task(loop.run("travail long"))
    await asyncio.sleep(0.05)
    assert not called.is_set()
    assert orch.get_task(task_id)["metadata"]["paused"] is True
    queue_steering(orch, task_id, "resume")
    assert await asyncio.wait_for(run, timeout=2.0) == "Repris proprement."
    assert called.is_set()
    assert orch.get_task(task_id)["metadata"]["paused"] is False
