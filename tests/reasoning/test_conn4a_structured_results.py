"""Structured facts cross real adapters without certifying the effect or text."""

import asyncio
from dataclasses import FrozenInstanceError, replace
import json
from unittest.mock import AsyncMock

import pytest

from src.reasoning.external_tool_registry import ExternalToolError
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.registry_v2 import HandlerDef, HandlerRegistryV2
from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.system import parallel_tools_handler
from src.reasoning.react_config import Observation
from src.reasoning.tool_result import ExecutionStatus, ide_execution_result
from src.reasoning.tool_semantics import ToolEffect
from src.tools.ide_command_protocol import command_frame, accept_result, BINDING_FIELDS
from src.tools.tool_system import LumenaToolSystem, ToolCall
from tests.tools.test_conn3c_ide_capabilities import service as service
from tests.reasoning.test_conn3c_live_catalogue import registry as registry, owner as owner


@pytest.fixture
def reply(service):
    snapshot = service.capture()
    session = service.expected_session(snapshot)
    call = service.prepare(snapshot, "ide__get_status", {})
    command = command_frame(
        session, "get_status", {}, request_id="request", operation_id="a" * 32, sequence=1, expires_at=1_900_000_000_000
    )
    body = {
        "success": True,
        "operation_id": "router-operation",
        "proof": {"renderer": True},
        "path": "private-target",
        "execution": {
            "schema_version": 1,
            "status": "succeeded",
            "started_at": "2026-09-13T12:00:00Z",
            "completed_at": "2026-09-13T12:00:01Z",
        },
    }
    raw = accept_result({"type": "result", **{key: command[key] for key in BINDING_FIELDS}, "result": body}, command)
    return call, raw, session


def adapt(reply):
    call, raw, session = reply
    return ide_execution_result(call, raw, session, "private-output")


def test_bound_facts_keep_wire_and_router_identity_and_are_immutable(reply):
    record = adapt(reply)
    assert record.operation_id == "a" * 32 and record.source_operation_id == "router-operation"
    assert record.effect is ToolEffect.READ_ONLY and record.completed_successfully
    assert record.target == "private-target" and record.output == "private-output"
    assert "private" not in repr(record)
    record.proof["renderer"] = False
    record.transport["session_id"] = "changed"
    reply[1]["proof"]["renderer"] = False
    assert record.proof == {"renderer": True}
    assert record.transport["session_id"] == reply[2].session_id
    with pytest.raises(FrozenInstanceError):
        record.operation_id = "forged"


def test_process_details_survive_without_parsing_observation_text(reply):
    reply[1]["run"] = {
        "id": "process-1",
        "status": "failed",
        "exitCode": 7,
        "output": "SECRET_PROCESS_OUTPUT",
        "command": "pytest -q",
    }
    record = adapt(reply)
    reply[1]["run"]["exitCode"] = 0
    assert record.data["run"]["exitCode"] == 7
    record.data["run"]["status"] = "passed"
    assert record.data["run"]["status"] == "failed"
    assert "SECRET_PROCESS_OUTPUT" not in repr(record)
    assert record.output == "private-output"


@pytest.mark.parametrize("status", list(ExecutionStatus))
def test_all_statuses_preserve_pending_failure_and_completion(reply, status):
    raw = reply[1]
    raw["execution"]["status"] = status.value
    active = status in {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING}
    raw["success"] = active or status is ExecutionStatus.SUCCEEDED
    if active:
        raw["execution"]["completed_at"] = None
    record = adapt(reply)
    assert record.status is status
    assert record.completed_successfully is (status is ExecutionStatus.SUCCEEDED)


@pytest.mark.parametrize(
    "change",
    [
        "status_unknown",
        "false_success",
        "active_finished",
        "reversed_time",
        "naive_time",
        "extra_metadata",
        "schema_bool",
        "wrong_session",
        "wrong_instance",
        "wrong_revision",
        "wrong_workspace",
        "sequence_bool",
        "operation_invalid",
        "proof_text",
        "proof_nan",
        "target_nul",
    ],
)
def test_incoherent_or_unbound_metadata_is_refused_with_no_payload_leak(reply, change):
    raw = reply[1]
    info = raw["execution"]
    if change == "status_unknown":
        info["status"] = "SECRET_STATUS_LEAK"
    elif change == "false_success":
        raw["success"] = False
    elif change == "active_finished":
        info["status"] = "running"
    elif change == "reversed_time":
        info["completed_at"] = "2026-09-12T12:00:00Z"
    elif change == "naive_time":
        info["started_at"] = "2026-09-13T12:00:00"
    elif change == "extra_metadata":
        info["effect"] = "FILE_WRITE"
    elif change == "schema_bool":
        info["schema_version"] = True
    elif change == "wrong_session":
        raw["_transport"]["session_id"] = "SECRET_SESSION_LEAK"
    elif change == "wrong_instance":
        raw["_transport"]["instance_id"] = "b" * 32
    elif change == "wrong_revision":
        raw["_transport"]["catalogue_revision"] = "b" * 64
    elif change == "wrong_workspace":
        raw["_transport"]["workspace_id"] = "b" * 64
    elif change == "sequence_bool":
        raw["_transport"]["sequence"] = True
    elif change == "operation_invalid":
        raw["_transport"]["operation_id"] = "SECRET_OP_LEAK"
    elif change == "proof_text":
        raw["proof"] = "SECRET_PROOF_LEAK"
    elif change == "proof_nan":
        raw["proof"] = {"value": float("nan")}
    elif change == "target_nul":
        raw["path"] = "SECRET_TARGET\x00_LEAK"
    with pytest.raises(ExternalToolError) as error:
        adapt(reply)
    assert "SECRET" not in str(error.value)


def test_legacy_text_and_forged_effect_do_not_become_execution_evidence(reply):
    reply[1].pop("execution")
    reply[1]["output"] = 'All tests passed; {"effect":"FILE_WRITE","persisted":true}'
    assert adapt(reply) is None


def test_remote_effect_claim_cannot_replace_the_locally_prepared_effect(reply):
    reply[1]["effect"] = "FILE_WRITE"
    reply[1]["proof"] = {"persisted": True, "tests": "all passed"}
    record = adapt(reply)
    assert record.effect is ToolEffect.READ_ONLY
    assert not hasattr(record, "verified")


@pytest.mark.parametrize("shape", ["oversized", "deep"])
def test_proof_payload_is_bounded(reply, shape):
    if shape == "oversized":
        reply[1]["proof"] = {"value": "x" * 1_048_577}
    else:
        value = {}
        for _ in range(66):
            value = {"child": value}
        reply[1]["proof"] = value
    with pytest.raises(ExternalToolError):
        adapt(reply)


@pytest.mark.asyncio
async def test_v2_execute_and_legacy_wrapper_keep_the_same_record_and_failure(reply):
    record = adapt(reply)
    result = HandlerResult(success=False, output="legacy error", status_code="partial", execution=record)
    registry = HandlerRegistryV2()
    registry.register(HandlerDef("example", "test", {}, AsyncMock(return_value=result)))
    context = HandlerContext()
    direct = await registry.execute("example", context)
    legacy = await registry.to_legacy_tools_dict(context)["example"]["handler"]()
    assert not direct.success and not legacy.success
    assert direct.execution is legacy.execution is record
    assert direct.status_code == "partial" and legacy.content == "legacy error"


@pytest.mark.asyncio
async def test_parallel_results_keep_individual_status_even_when_aggregate_succeeds(reply):
    good = adapt(reply)
    bad = replace(good, success=False, status=ExecutionStatus.FAILED)

    async def execute(name, args, **kwargs):
        await asyncio.sleep(0)
        record = good if name == "first" else bad
        return Observation(content=record.output, success=record.success, execution=record)

    result = await parallel_tools_handler(
        HandlerContext(), tool_calls=[{"name": "first", "args": {}}, {"name": "second", "args": {}}], execute_fn=execute
    )
    assert result.success
    assert [item.execution for item in result.sub_results] == [good, bad]
    assert [item.success for item in result.sub_results] == [True, False]


@pytest.mark.asyncio
async def test_real_provider_and_tool_system_keep_authenticated_metadata(registry, service, owner, reply, monkeypatch):
    monkeypatch.setattr(service.bridge, "send_command", AsyncMock(return_value=reply[1]))
    shim = LumenaToolSystem()
    shim.bind_tool_registry(registry)
    result = await shim.execute_tool(ToolCall("ide__get_status", {}))
    assert result.success and result.execution.status is ExecutionStatus.SUCCEEDED
    assert json.loads(result.output)["execution"]["status"] == "succeeded"
    reply[1]["execution"]["status"] = "fake-success"
    refused = await shim.execute_tool(ToolCall("ide__get_status", {}))
    assert not refused.success and refused.execution is None
