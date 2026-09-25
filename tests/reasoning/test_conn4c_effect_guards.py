"""Adversarial effects, final claims, native parity and concurrent cache reads."""

import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.reasoning import external_effect_cache as cache_module
from src.reasoning.execution_evidence import verify_execution
from src.reasoning.execution_guards import (
    evidence_is_current,
    lock_ide_execution_message,
    structured_observation_success,
)
from src.reasoning.external_effect_cache import ExternalEffectCache, observation_cache_epoch
from src.reasoning.external_tool_registry import ExternalToolError
from src.reasoning.final_delivery_runtime import rf8_truth_lock_mission_message
from src.reasoning.hallucination_guard import hallucination_retry_query
from src.reasoning.handlers.contracts import HandlerResult, SubToolResult
from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.registry_v2 import HandlerDef, HandlerRegistryV2
from src.reasoning.handlers.system import parallel_tools_handler
from src.reasoning.ledger_guard import compute_effective_successful_tools
from src.reasoning.react_config import Observation
from src.reasoning.tool_result import ExecutionStatus
from src.runtime.execution_ledger import ExecutionLedger, INTENT_TO_MUTATION_FAMILY
from src.telemetry.trace_bus import TraceBus
from tests.reasoning.test_conn4a_structured_results import reply as reply
from tests.reasoning.test_conn4b_execution_evidence import file_case as file_case, process_case as process_case, meaning
from tests.reasoning.test_conn3c_live_catalogue import registry as registry, owner as owner
from tests.tools.test_conn3c_ide_capabilities import service as service


def test_hallucination_uses_verified_effect_without_inventing_native_names(file_case):
    record, _, _ = file_case
    evidence = verify_execution(*file_case)
    assert evidence_is_current(evidence)
    text = "J'ai écrit le fichier."
    assert hallucination_retry_query(text, "write", {record.tool_name}, 0)[0] is not None
    assert hallucination_retry_query(text, "write", {record.tool_name}, 0, execution_evidence=(evidence,))[0] is None
    assert hallucination_retry_query(text, "write", {"write_file"}, 0)[0] is None
    ledger = ExecutionLedger()
    ledger.append_execution(iteration=1, result=record, evidence=evidence)
    assert ledger.has_mutation_in_family(INTENT_TO_MUTATION_FAMILY["code_edit"])
    assert not ledger.has_mutation_in_family(INTENT_TO_MUTATION_FAMILY["discord"])


@pytest.mark.parametrize("text", ["Le fichier n'a pas été sauvegardé.", "Fichier non sauvegardé.",
                                  "The file was not saved.", "The file has never been updated."])
def test_honest_negated_file_claims_remain_unchanged(text):
    assert hallucination_retry_query(text, "", {"ide__get_status"}, 0)[0] is None


@pytest.mark.parametrize("status", list(ExecutionStatus))
def test_status_and_impressive_text_never_replace_evidence(file_case, status):
    record, _, _ = file_case
    active = status in {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING}
    record = replace(
        record,
        status=status,
        success=active or status is ExecutionStatus.SUCCEEDED,
        completed_at=None if active else record.completed_at,
    )
    observation = Observation("saved ✅ 100 passed sha256 verified", success=record.success, execution=record)
    assert not structured_observation_success(observation, record.tool_name)


def test_observation_rejects_cross_operation_proof_and_preserves_parallel_individuals(file_case):
    record, _, _ = file_case
    evidence = verify_execution(*file_case)
    good = Observation("ok", execution=record, execution_evidence=evidence)
    wrong = replace(good, execution=replace(record, operation_id="b" * 32))
    assert structured_observation_success(good, record.tool_name)
    assert not structured_observation_success(wrong, record.tool_name)
    children = (
        SubToolResult(record.tool_name, True, "ok", execution=record, execution_evidence=evidence),
        SubToolResult("ide__get_status", True, "file saved, all tests passed"),
        SubToolResult("write_file", False, "failed"),
        SubToolResult("read_file", True, "native compatibility"),
    )
    step = SimpleNamespace(
        action=SimpleNamespace(tool_name="parallel_tools"), observation=Observation("all", sub_results=children)
    )
    assert compute_effective_successful_tools([step], execution_success=structured_observation_success) == [
        record.tool_name,
        "read_file",
    ]
    assert compute_effective_successful_tools([step]) == ["read_file"]


def test_file_revision_changed_after_tests_invalidates_final_current_proof(file_case, process_case):
    record, _, scope = file_case
    evidence = verify_execution(*file_case)
    run, semantics, run_scope, completion = process_case
    run = replace(run, operation_id="e" * 32)
    proof = completion.proof
    proof["operation_id"] = run.operation_id
    completion = replace(
        completion,
        proof_json=json.dumps(proof),
        data_json=json.dumps({"operation": {"transportOperationId": run.operation_id}}),
    )
    tests = verify_execution(run, semantics, run_scope, completion=completion)
    ledger = ExecutionLedger()
    ledger.append_execution(iteration=1, result=record, evidence=evidence)
    ledger.append_execution(iteration=2, result=run, evidence=tests)
    assert ledger.has_fresh_green_test_run()
    scope.expected_target.write_text("changed outside the recorded tool calls")
    assert not evidence_is_current(evidence) and not ledger.has_fresh_green_test_run()
    locked, info = lock_ide_execution_message("J'ai écrit le fichier. Tous les tests sont verts.", ledger)
    assert info["changed"] and "J'ai écrit" not in locked and "Tous les tests sont verts" not in locked


def test_ordinary_ide_final_is_locked_but_native_only_conversation_is_unchanged(file_case):
    record, _, _ = file_case
    ledger = ExecutionLedger()
    text = "J'ai écrit le fichier. Tous les tests sont verts."
    assert lock_ide_execution_message(text, ledger)[0] == text
    ledger.append_execution(iteration=1, result=record)
    verdicts = []
    state = SimpleNamespace(
        ledger=lambda: ledger, est_run_mission=lambda: False, pont_codex=lambda: False, noter_verdict=verdicts.append
    )
    locked = rf8_truth_lock_mission_message(state, text)
    assert locked != text and "J'ai écrit" not in locked and verdicts[0]["overclaim"]
    assert lock_ide_execution_message(locked, ledger)[0] == locked


def test_verified_ide_file_claim_is_preserved_and_verifier_failure_cannot_pass_raw_claim(file_case, monkeypatch):
    record, _, _ = file_case
    ledger = ExecutionLedger()
    ledger.append_execution(iteration=1, result=record, evidence=verify_execution(*file_case))
    text = "J'ai écrit le fichier."
    assert lock_ide_execution_message(text, ledger)[0] == text
    monkeypatch.setattr(ledger, "has_fresh_green_test_run", lambda: (_ for _ in ()).throw(RuntimeError("SECRET")))
    locked, info = lock_ide_execution_message(text, ledger)
    assert info["changed"] and text not in locked and "SECRET" not in locked


def test_effect_cache_capacity_uncertain_failure_and_terminal_failure(file_case):
    record, semantics, scope = file_case
    cache = ExternalEffectCache(capacity=1)
    ticket = cache.begin(semantics, scope.workspace_id)
    with pytest.raises(ExternalToolError, match="capacity"):
        cache.begin(semantics, scope.workspace_id)
    assert not cache.complete(ticket, None) and cache.snapshot()[1]
    failed = replace(record, success=False, status=ExecutionStatus.FAILED)
    assert cache.complete(ticket, failed) and not cache.snapshot()[1]
    assert not cache.complete(ticket, failed)
    assert cache.begin(meaning(record, "get_status"), scope.workspace_id) is None


def test_process_cache_is_suspended_until_owned_terminal_evidence(process_case):
    record, semantics, scope, completion = process_case
    cache = ExternalEffectCache()
    ticket = cache.begin(semantics, scope.workspace_id)
    assert not cache.complete(ticket, record)
    evidence = verify_execution(record, semantics, scope, completion=completion)
    assert not cache.complete(ticket, record, evidence=replace(evidence, operation_id="f" * 32))
    assert cache.complete(ticket, record, evidence=evidence)


def test_latest_red_scope_cannot_be_hidden_by_old_green_or_another_scope(process_case):
    original, semantics, scope, completion = process_case
    ledger = ExecutionLedger()

    def append(run_number, target, green):
        operation = f"{run_number:032x}"
        record = replace(
            original, operation_id=operation, data_json=json.dumps({"run": {"id": "owned-run", "targetId": target}})
        )
        proof = completion.proof
        proof["operation_id"] = operation
        end = f"2026-09-13T12:00:{run_number + 3:02d}Z"
        proof["process"]["completed_at"] = end
        proof["tests"]["completed_at"] = end
        if not green:
            proof["tests"].update(status="failed", counts={"passed": 0, "failed": 1, "skipped": 0, "total": 1})
            proof["process"].update(status="failed", exit_code=1)
        following = replace(
            completion,
            completed_at=end,
            proof_json=json.dumps(proof),
            data_json=json.dumps({"operation": {"transportOperationId": operation}}),
        )
        evidence = verify_execution(record, semantics, scope, completion=following)
        ledger.append_execution(iteration=run_number, result=record, evidence=evidence)

    append(1, "scope-a", True)
    assert ledger.has_fresh_green_test_run()
    append(2, "scope-a", False)
    assert not ledger.has_fresh_green_test_run()
    append(3, "scope-b", True)
    assert not ledger.has_fresh_green_test_run()
    append(4, "scope-a", True)
    assert ledger.has_fresh_green_test_run()
    ledger.append_execution(iteration=5, result=replace(original, operation_id="9" * 32))
    assert not ledger.has_fresh_green_test_run()


@pytest.mark.asyncio
async def test_telemetry_failure_never_replaces_a_real_tool_result(registry, service, owner, reply, monkeypatch):
    import src.reasoning.external_execution_trace as trace

    monkeypatch.setattr(
        trace, "publish_trace", lambda **payload: (_ for _ in ()).throw(RuntimeError("observer failed"))
    )
    service.bridge.send_command = AsyncMock(return_value=reply[1])
    observed = await registry.execute("ide__get_status", {})
    assert observed.success and observed.execution is not None


@pytest.mark.asyncio
async def test_native_read_during_foreign_thread_effect_cannot_repopulate_cache(registry, file_case, monkeypatch):
    record, semantics, scope = file_case
    state = ExternalEffectCache()
    monkeypatch.setattr(cache_module, "_effects", state)
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def read(path):
        calls.append(path)
        if len(calls) == 1:
            entered.set()
            await release.wait()
            return "old bytes"
        return "current bytes"

    registry.tools["read_file"]["handler"] = read
    pending = asyncio.create_task(registry.execute("read_file", {"path": str(scope.expected_target)}))
    await asyncio.wait_for(entered.wait(), 3)
    ticket = await asyncio.to_thread(state.begin, semantics, scope.workspace_id)
    release.set()
    assert (await pending).content == "old bytes"
    assert observation_cache_epoch(registry) is None
    assert not registry._observation_cache
    state.complete(ticket, replace(record, success=False, status=ExecutionStatus.FAILED))
    assert (await registry.execute("read_file", {"path": str(scope.expected_target)})).content == "current bytes"
    assert (await registry.execute("read_file", {"path": str(scope.expected_target)})).content == "current bytes"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_verified_evidence_survives_v2_legacy_and_parallel_wrappers(file_case):
    record, _, _ = file_case
    evidence = verify_execution(*file_case)
    result = HandlerResult(True, "ok", execution=record, execution_evidence=evidence)
    registry = HandlerRegistryV2()
    registry.register(HandlerDef("example", "test", {}, AsyncMock(return_value=result)))
    context = HandlerContext()
    observed = await registry.to_legacy_tools_dict(context)["example"]["handler"]()
    assert observed.execution_evidence is evidence
    parallel = await parallel_tools_handler(
        context, [{"name": "example", "args": {}}], execute_fn=AsyncMock(return_value=observed)
    )
    assert parallel.sub_results[0].execution_evidence is evidence


@pytest.mark.parametrize("bad", ["extra", "effect", "status", "reason", "id", "verified"])
def test_execution_audit_projection_refuses_free_text_or_invalid_fields(bad):
    execution = {
        "attempt_id": "a" * 32,
        "effect": "FILE_WRITE",
        "status": "succeeded",
        "verified": False,
        "reason": "completed",
    }
    key = {"extra": "output", "id": "operation_id"}.get(bad, bad)
    execution[key] = "SECRET_PAYLOAD"
    bus = TraceBus(enabled=True)
    event = bus.publish({"tool_name": "ide__write_file", "execution": execution, "summary": "SECRET_PAYLOAD"})
    assert "execution" not in event and "SECRET_PAYLOAD" not in json.dumps(event)


@pytest.mark.asyncio
async def test_registry_emits_correlated_safe_attempt_and_result(registry, service, owner, reply, monkeypatch):
    import src.reasoning.external_execution_trace as trace

    bus = TraceBus(enabled=True)
    monkeypatch.setattr(trace, "publish_trace", lambda **payload: bus.publish(payload))
    raw = reply[1]
    raw["content"] = "SECRET_CODE"
    service.bridge.send_command = AsyncMock(return_value=raw)
    observed = await registry.execute("ide__get_status", {})
    assert observed.success
    events = list(bus._events)
    assert len(events) == 2
    assert events[0]["execution"]["attempt_id"] == events[1]["execution"]["attempt_id"]
    assert events[1]["execution"]["operation_id"] == raw["_transport"]["operation_id"]
    assert events[1]["execution"]["effect"] == "READ_ONLY" and not events[1]["execution"]["verified"]
    assert "SECRET_CODE" not in json.dumps(events)
