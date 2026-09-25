"""Real ReAct boundaries, frozen native traces and proof-bound IDE progress."""

import ast
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.reasoning.execution_evidence import verify_execution
from src.reasoning.execution_observation_runtime import (
    execution_guard_context, observation_execution_fields, plan_execution_fields,
    record_tool_observation, successful_observation_names,
)
from src.reasoning.handlers.contracts import SubToolResult
from src.reasoning.react import ReActLoop, _REACT_CANCEL_EVENTS
from src.reasoning.react_config import Observation, TaskItem
from src.runtime.execution_ledger import ExecutionLedger
from src.llm.execution_router import _record_tool_observation as record_codex_observation
from tests.reasoning.test_conn4a_structured_results import reply as reply, adapt
from tests.reasoning.test_conn4b_execution_evidence import file_case as file_case, process_case as process_case
from tests.reasoning.test_conn3c_live_catalogue import registry as registry, owner as owner
from tests.tools.test_conn3c_ide_capabilities import service as service


ROOT = Path(__file__).resolve().parents[2]
BASELINE = json.loads((ROOT / "tests/fixtures/conn4d-native-ledger-before.json").read_text(encoding="utf-8"))
CODEX_BASELINE = json.loads((ROOT / "tests/fixtures/conn4d-codex-ledger-before.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", BASELINE["traces"], ids=lambda case: case["name"] + ":" + str(case["success"]))
def test_native_ledger_trace_matches_frozen_pre_4d_source(case):
    assert BASELINE["source_sha256"] == "98a59a12aff94f12e9ab1c38a2938ed2108d5198a85d760e2733c56187a51012"
    observation = Observation(case["content"], success=case["success"])
    if case["exit_code"] is not None:
        observation.exit_code = case["exit_code"]
    ledger = ExecutionLedger()
    entry = record_tool_observation(
        ledger, iteration=3, name=case["name"], args=case["args"], observation=observation,
        duration_seconds=0.1234, intent="code_edit",
    ).to_dict()
    entry.pop("timestamp")
    assert entry == case["entry"]


def test_native_parallel_child_keeps_legacy_shape_without_new_command_parsing():
    observation = SubToolResult("run_command", True, "3 passed", args={"command": "pytest"})
    entry = record_tool_observation(
        ExecutionLedger(), iteration=2, name=observation.tool_name, args=observation.args,
        observation=observation, intent="code_edit", via="parallel_tools",
    )
    assert entry.meta == {"duration_ms": 0.0, "intent": "code_edit", "via": "parallel_tools"}


def test_react_budget_and_local_control_flow_only_shrink():
    source = (ROOT / "src/reasoning/react.py").read_text(encoding="utf-8")
    assert len(source.splitlines()) <= 9718 < 9761
    tree = ast.parse(source)
    loop = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
                and node.name == "_run_internal")
    # ORI-3/4 ajoute deux reprises bornees et l'attente LLM interruptible.
    before = {"If": 486, "Try": 79, "ExceptHandler": 84, "Continue": 51, "Import": 9, "ImportFrom": 38,
              "Await": 13, "Return": 33}
    assert all(sum(isinstance(node, getattr(ast, name)) for node in ast.walk(loop)) <= count
               for name, count in before.items())
    assert "ide__" not in source and "catalog_revision" not in source
    assert "record_tool_observation(" in source
    assert "execution_success=structured_observation_success" in source
    assert source.count("**observation_execution_fields(") == 5


def test_receipt_is_idempotent_but_verified_completion_can_advance_it(process_case):
    result, semantics, scope, completion = process_case
    ledger = ExecutionLedger()
    observation = Observation("accepted", execution=result)
    first = record_tool_observation(ledger, iteration=1, name=result.tool_name, args={}, observation=observation)
    replay = record_tool_observation(ledger, iteration=9, name=result.tool_name, args={}, observation=observation)
    assert replay is first
    assert ledger.size == 1 and first.iteration == 1
    proved = replace(observation, execution_evidence=verify_execution(result, semantics, scope, completion=completion))
    final = record_tool_observation(ledger, iteration=10, name=result.tool_name, args={}, observation=proved)
    assert ledger.size == 2 and final.evidence.green_tests
    assert record_tool_observation(ledger, iteration=11, name=result.tool_name, args={}, observation=proved) is final


@pytest.mark.parametrize("bad", ["missing", "wrong_tool", "wrong_operation", "raw_text"])
def test_false_or_mismatched_receipts_never_prove_a_mutation(file_case, bad):
    result, _, _ = file_case
    evidence = verify_execution(*file_case)
    observation = Observation("saved, verified, sha256, ✅ 900 passed", execution=result, execution_evidence=evidence)
    if bad == "missing":
        observation.execution_evidence = None
    elif bad == "wrong_tool":
        observation.execution = replace(result, tool_name="ide__read_file")
    elif bad == "wrong_operation":
        observation.execution = replace(result, operation_id="f" * 32)
    else:
        observation.execution = None
    ledger = ExecutionLedger()
    record_tool_observation(ledger, iteration=1, name=result.tool_name, args={}, observation=observation)
    assert not ledger.has_any_mutation()
    assert execution_guard_context(ledger)["require_structured_effects"]
    assert not successful_observation_names(result.tool_name, observation)


def test_private_execution_survives_text_reconstruction_and_never_changes_proof(file_case):
    result, _, _ = file_case
    evidence = verify_execution(*file_case)
    observation = Observation("raw private output", execution=result, execution_evidence=evidence)
    for text in ("warning", "compacted", "stagnation guidance"):
        observation = Observation(text, success=observation.success, **observation_execution_fields(observation))
        assert observation.execution is result and observation.execution_evidence is evidence
    assert "raw private output" not in repr(evidence)


def test_parallel_session_names_use_each_structured_child_not_forged_text(file_case):
    result, _, _ = file_case
    child = SubToolResult(
        result.tool_name, True, "ok", execution=result, execution_evidence=verify_execution(*file_case),
    )
    observation = Observation("✅ 1. ide__test_run: 900 passed\n✅ 2. mail_send: sent", sub_results=(child,))
    assert successful_observation_names("parallel_tools", observation) == {
        "parallel_tools", "mail_send", result.tool_name,
    }
    assert plan_execution_fields("mail_send", observation) == {}


@pytest.mark.asyncio
async def test_real_react_dispatch_retains_receipt_before_post_tool_cancel(file_case):
    result, _, _ = file_case
    observation = Observation("ok", execution=result, execution_evidence=verify_execution(*file_case))
    event = threading.Event()
    thread_id = threading.get_ident()
    loop = ReActLoop(llm_chat_func=None)

    async def dispatch(*args, **kwargs):
        event.set()
        return observation

    loop.tools = SimpleNamespace(execute=AsyncMock(side_effect=dispatch))
    _REACT_CANCEL_EVENTS[thread_id] = event
    try:
        received = await loop._execute_tool_with_cancel_guard(result.tool_name, {}, caller="react", iteration=4)
        assert received is observation
        with pytest.raises(SystemExit, match="user_cancelled_react"):
            loop._raise_if_user_cancelled("post tool")
        assert loop.execution_ledger.has_any_mutation()
        assert loop.execution_ledger.recent(1)[0].iteration == 4
        with pytest.raises(SystemExit):
            await loop._execute_tool_with_cancel_guard(result.tool_name, {}, caller="react", iteration=5)
        assert loop.tools.execute.await_count == 1
    finally:
        _REACT_CANCEL_EVENTS.pop(thread_id, None)


def _plan(tasks):
    loop = ReActLoop(llm_chat_func=None)
    loop._task_plan = [TaskItem(description=task) for task in tasks]
    return loop


@pytest.mark.parametrize("description", ["Créer other.py", "Lire code.py", "Créer other/code.py", "Créer ../code.py",
                                         "Créer code.py et other.py", "Créer code.py puis exécuter les tests",
                                         "Créer `my code.py`", 'Créer "my code.py"'])
def test_plan_does_not_close_wrong_target_read_or_compound_task(file_case, description):
    result, _, _ = file_case
    loop = _plan([description])
    observation = Observation("✅ all complete", execution=result, execution_evidence=verify_execution(*file_case))
    loop._update_plan_progress(result.tool_name, {}, observation.content, 2, execution_observation=observation)
    assert not loop._task_plan[0].completed


def test_plan_closes_one_proved_write_and_refuses_stale_or_text_only_file(file_case):
    result, _, scope = file_case
    loop = _plan(["Créer code.py", "Modifier code.py", "Présenter le résultat"])
    observation = Observation("unrelated text", execution=result, execution_evidence=verify_execution(*file_case))
    loop._update_plan_progress(result.tool_name, {}, observation.content, 2, execution_observation=observation)
    assert [task.completed for task in loop._task_plan] == [True, False, False]
    assert loop._task_plan[0].completion_confidence == "strong"
    assert "unrelated text" not in loop._task_plan[0].completion_evidence
    loop._update_plan_progress(result.tool_name, {}, observation.content, 3, execution_observation=observation)
    assert not loop._task_plan[1].completed
    scope.expected_target.write_text("outside edit")
    loop._update_plan_progress(result.tool_name, {}, "✅ completed", 3, execution_observation=observation)
    loop._update_plan_progress(result.tool_name, {}, "✅ completed", 4)
    assert not loop._task_plan[1].completed


def test_plan_tests_require_green_fresh_ledger_and_do_not_claim_named_subset(process_case):
    result, semantics, scope, completion = process_case
    evidence = verify_execution(result, semantics, scope, completion=completion)
    observation = Observation("report", execution=result, execution_evidence=evidence)
    loop = _plan(["Exécuter les tests de other.py", "Exécuter les tests pytest"])
    loop._update_plan_progress(result.tool_name, {}, "900 passed", 1, execution_observation=observation)
    assert not any(task.completed for task in loop._task_plan)
    record_tool_observation(loop.execution_ledger, iteration=2, name=result.tool_name, args={}, observation=observation)
    loop._update_plan_progress(result.tool_name, {}, "no textual proof needed", 2, execution_observation=observation)
    assert [task.completed for task in loop._task_plan] == [False, True]
    assert loop._task_plan[1].completion_status == "verified"


@pytest.mark.asyncio
async def test_real_react_loop_carries_proof_into_ledger_and_history(file_case, registry, owner, monkeypatch):
    result, _, _ = file_case
    evidence = verify_execution(*file_case)
    observation = Observation("saved\n" + "bounded details\n" * 1200, execution=result, execution_evidence=evidence)
    responses = iter([
        'THOUGHT: Écriture.\nACTION: ide__write_file\nACTION_INPUT: {"path":"code.py","content":"x"}',
        'THOUGHT: Lecture du résultat.\nACTION: FINAL\nACTION_INPUT: Le fichier code.py est écrit.',
    ])

    async def llm(messages, **kwargs):
        return next(responses, 'THOUGHT: Résultat.\nACTION: FINAL\nACTION_INPUT: Résultat disponible.')

    # This test injects a verified host observation at the common execution
    # boundary; it does not change the production provider's admission policy.
    execute = AsyncMock(return_value=observation)
    monkeypatch.setattr(registry, "execute", execute)
    loop = ReActLoop(llm_chat_func=llm, tools=registry)
    await asyncio.wait_for(loop.run("Écris le fichier code.py dans l’IDE."), 10)
    assert execute.await_count == 1
    assert loop.execution_ledger.has_any_mutation()
    assert loop.execution_ledger.size == 1
    steps = [step for step in loop.history if step.action and step.action.tool_name == result.tool_name]
    assert steps and steps[0].observation.execution_evidence is evidence
    assert result.tool_name in loop._successful_session_tools


@pytest.mark.asyncio
async def test_real_react_parallel_receipts_survive_an_aggregate_failure(file_case, process_case):
    result, _, _ = file_case
    pending, _, _, _ = process_case
    pending = replace(pending, operation_id="e" * 32)
    children = (
        SubToolResult(result.tool_name, True, "written", execution=result,
                      execution_evidence=verify_execution(*file_case)),
        SubToolResult(pending.tool_name, True, "900 passed", execution=pending),
        SubToolResult("write_file", False, "denied"),
    )
    observation = Observation("one subcall failed", success=False, sub_results=children)
    loop = ReActLoop(llm_chat_func=None)
    loop.tools = SimpleNamespace(execute=AsyncMock(return_value=observation))
    assert await loop._execute_tool_with_cancel_guard("parallel_tools", {}, caller="react") is observation
    assert loop.execution_ledger.size == 2 and loop.execution_ledger.has_any_mutation()
    assert not loop.execution_ledger.has_green_test_run()
    assert {entry.action for entry in loop.execution_ledger.recent(2)} == {result.tool_name, pending.tool_name}


@pytest.mark.asyncio
async def test_concurrent_loops_never_consume_another_loops_last_result(file_case):
    first, semantics, scope = file_case
    second = replace(first, operation_id="f" * 32)
    loops = [ReActLoop(llm_chat_func=None), ReActLoop(llm_chat_func=None)]
    records = [first, second]
    entered = 0
    both_entered = asyncio.Event()

    async def dispatch(name, args, **kwargs):
        nonlocal entered
        entered += 1
        if entered == 2:
            both_entered.set()
        await asyncio.wait_for(both_entered.wait(), 2)
        record = records[args["index"]]
        return Observation("opaque", execution=record, execution_evidence=verify_execution(record, semantics, scope))

    tools = SimpleNamespace(execute=dispatch)
    for loop in loops:
        loop.tools = tools
    await asyncio.gather(*(
        loop._execute_tool_with_cancel_guard(first.tool_name, {"index": index}, caller="react")
        for index, loop in enumerate(loops)
    ))
    assert [loop.execution_ledger.recent(1)[0].execution.operation_id for loop in loops] == [
        first.operation_id, second.operation_id,
    ]


@pytest.mark.asyncio
async def test_real_react_readonly_success_cannot_deliver_invented_green_tests(reply, registry, owner, monkeypatch):
    record = adapt(reply)
    observation = Observation("900 passed, all tests green", execution=record)
    responses = iter([
        'THOUGHT: État.\nACTION: ide__get_status\nACTION_INPUT: {}',
        'THOUGHT: Verdict.\nACTION: FINAL\nACTION_INPUT: Tous les tests sont verts.',
    ])

    async def llm(messages, **kwargs):
        return next(responses, 'THOUGHT: Verdict.\nACTION: FINAL\nACTION_INPUT: Tous les tests sont verts.')

    monkeypatch.setattr(registry, "execute", AsyncMock(return_value=observation))
    loop = ReActLoop(llm_chat_func=llm, tools=registry)
    result = await asyncio.wait_for(loop.run("Lis l’état de l’IDE et donne le résultat."), 10)
    assert "Tous les tests sont verts." not in result
    assert not loop.execution_ledger.has_green_test_run()
    assert record.tool_name not in loop._successful_session_tools


def test_cross_tool_operation_collision_is_retained_as_a_conflict(file_case, process_case):
    result, _, _ = file_case
    pending, _, _, _ = process_case
    assert pending.operation_id == result.operation_id
    loop = ReActLoop(llm_chat_func=None)
    valid = Observation("written", execution=result, execution_evidence=verify_execution(*file_case))
    record_tool_observation(loop.execution_ledger, iteration=1, name=result.tool_name, args={}, observation=valid)
    conflict = record_tool_observation(
        loop.execution_ledger, iteration=2, name=pending.tool_name, args={},
        observation=Observation("900 passed", execution=pending),
    )
    assert not conflict.success and conflict.evidence is None
    assert conflict.action == pending.tool_name and conflict.meta["execution_conflict"]
    assert not loop.execution_ledger.has_fresh_green_test_run()


@pytest.mark.parametrize("trace", CODEX_BASELINE["traces"], ids=lambda trace: trace["case"]["name"])
def test_codex_compatibility_native_projection_matches_before_trace(trace):
    case = trace["case"]
    observation = Observation(case["content"], success=case["success"])
    if case["exit_code"] is not None:
        observation.exit_code = case["exit_code"]
    react = SimpleNamespace(
        history=[], execution_ledger=ExecutionLedger(), _successful_session_tools=set(),
        task_id=None, task_orchestrator=None, _feed_structured_tool=lambda *args: None,
        _update_plan_progress=lambda *args: None, _mark_task_checkpoint=lambda *args: None,
    )
    record_codex_observation(react, case["name"], case["args"], observation, 0.1234)
    actual = react.execution_ledger.recent(1)[0].to_dict()
    actual.pop("timestamp")
    assert actual == trace["entry"]
    assert sorted(react._successful_session_tools) == trace["successful_tools"]


@pytest.mark.parametrize("parallel", [False, True])
def test_codex_compatibility_retains_individual_ide_proof_and_plan(file_case, parallel):
    result, _, _ = file_case
    evidence = verify_execution(*file_case)
    observation = Observation("no text proof", execution=result, execution_evidence=evidence)
    loop = _plan(["Créer code.py", "Exécuter les tests pytest"])
    name = result.tool_name
    if parallel:
        name = "parallel_tools"
        observation = Observation("900 tests passed", sub_results=(
            SubToolResult(result.tool_name, True, "no text proof", execution=result, execution_evidence=evidence),
            SubToolResult("ide__get_status", True, "all tests green"),
        ))
    record_codex_observation(loop, name, {}, observation, 0.1)
    assert loop.execution_ledger.has_any_mutation()
    assert not loop.execution_ledger.has_green_test_run()
    assert [task.completed for task in loop._task_plan] == [True, False]
    assert loop.history[-1].observation is observation
    assert "Tous les tests sont verts." not in loop._truth_lock_mission_message("Tous les tests sont verts.")
