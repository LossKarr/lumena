"""Immutable execution facts. Carrying a proof is not verifying that proof."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import json
import re

from .external_tool_registry import ExternalToolError, PreparedExternalCall
from .tool_semantics import ToolEffect
from ..tools.ide_command_protocol import BINDING_FIELDS, encode_frame
from ..tools.ide_protocol import NegotiatedSession, ProtocolError, TRANSPORT_VERSION


class ExecutionStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    CONFLICT = "conflict"


def _timestamp(value: object) -> datetime:
    if type(value) is not str or len(value) > 64:
        raise ExternalToolError("ide_result_timestamp_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ExternalToolError("ide_result_timestamp_invalid") from None
    if parsed.tzinfo is None:
        raise ExternalToolError("ide_result_timestamp_invalid")
    return parsed


@dataclass(frozen=True, slots=True)
class ToolExecutionResult:
    tool_name: str
    success: bool
    status: ExecutionStatus
    effect: ToolEffect
    operation_id: str
    request_id: str
    provider_instance_id: str
    catalog_revision: str
    workspace_id: str | None
    started_at: str
    completed_at: str | None
    target: str | None = field(repr=False)
    proof_json: str = field(repr=False)
    output: str = field(repr=False)
    transport_json: str = field(repr=False)
    data_json: str = field(repr=False)
    source_operation_id: str | None = None

    def __post_init__(self) -> None:
        if (type(self.success) is not bool or type(self.status) is not ExecutionStatus
                or type(self.effect) is not ToolEffect):
            raise ExternalToolError("execution_result_invalid")
        if (self.status is ExecutionStatus.SUCCEEDED and not self.success
                or self.success and self.status in {ExecutionStatus.FAILED, ExecutionStatus.CANCELLED,
                                                    ExecutionStatus.TIMED_OUT, ExecutionStatus.CONFLICT}):
            raise ExternalToolError("execution_result_invalid")
        start = _timestamp(self.started_at)
        if self.status in {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING}:
            if self.completed_at is not None:
                raise ExternalToolError("execution_result_invalid")
        elif _timestamp(self.completed_at) < start:
            raise ExternalToolError("execution_result_invalid")
        if type(self.output) is not str or len(self.output.encode("utf-8")) > 1_100_000:
            raise ExternalToolError("execution_result_output_invalid")
        for encoded in (self.proof_json, self.transport_json, self.data_json):
            try:
                value = json.loads(encoded)
                if type(value) is not dict:
                    raise ValueError()
                encode_frame(value)
            except (TypeError, ValueError, RecursionError):
                raise ExternalToolError("execution_result_payload_invalid") from None

    @property
    def proof(self) -> dict:
        return json.loads(self.proof_json)

    @property
    def transport(self) -> dict:
        return json.loads(self.transport_json)

    @property
    def data(self) -> dict:
        """Business payload (run, exit code, etc.), still awaiting verification."""
        return json.loads(self.data_json)

    @property
    def completed_successfully(self) -> bool:
        """Operation completion only; never an assertion of a verified effect."""
        return self.success and self.status is ExecutionStatus.SUCCEEDED


def ide_execution_result(
    call: PreparedExternalCall, raw: dict, session: NegotiatedSession, output: str,
) -> ToolExecutionResult | None:
    """Adapt authenticated structured replies; legacy replies carry no proof.

    The bridge has already correlated the full reply to its pending command.
    Recheck the retained session here and keep wire and router identities apart.
    No text parsing or model-supplied effect can establish execution facts.
    """
    if "execution" not in raw:
        return None
    info = raw["execution"]
    if (type(info) is not dict or set(info) != {"schema_version", "status", "started_at", "completed_at"}
            or type(info.get("schema_version")) is not int or info["schema_version"] != 1):
        raise ExternalToolError("ide_result_metadata_invalid")
    try:
        status = ExecutionStatus(info["status"])
    except (ValueError, TypeError):
        raise ExternalToolError("ide_result_status_invalid") from None
    success = raw.get("success")
    if type(success) is not bool or (status is ExecutionStatus.SUCCEEDED and not success):
        raise ExternalToolError("ide_result_status_inconsistent")
    if success and status in {ExecutionStatus.FAILED, ExecutionStatus.CANCELLED,
                              ExecutionStatus.TIMED_OUT, ExecutionStatus.CONFLICT}:
        raise ExternalToolError("ide_result_status_inconsistent")
    started = _timestamp(info["started_at"])
    active = status in {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING}
    if active:
        if info["completed_at"] is not None:
            raise ExternalToolError("ide_result_status_inconsistent")
    elif _timestamp(info["completed_at"]) < started:
        raise ExternalToolError("ide_result_timestamp_invalid")
    transport = raw.get("_transport")
    if type(transport) is not dict or set(transport) != set(BINDING_FIELDS):
        raise ExternalToolError("ide_result_binding_invalid")
    if (type(transport["transport_version"]) is not int or transport["transport_version"] != TRANSPORT_VERSION
            or type(transport["sequence"]) is not int or not 1 <= transport["sequence"] <= 9_007_199_254_740_991
            or type(transport["request_id"]) is not str
            or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", transport["request_id"]) is None
            or type(transport["operation_id"]) is not str
            or re.fullmatch(r"[0-9a-f]{32}", transport["operation_id"]) is None):
        raise ExternalToolError("ide_result_binding_invalid")
    for key, expected in {
        "session_id": session.session_id, "instance_id": session.instance_id,
        "catalogue_revision": session.catalogue_hash, "workspace_id": session.workspace_id,
    }.items():
        if type(transport[key]) is not type(expected) or transport[key] != expected:
            raise ExternalToolError("ide_result_binding_invalid")
    if (call.semantics.provider_instance_id != session.instance_id
            or call.semantics.catalog_revision != session.catalogue_hash):
        raise ExternalToolError("ide_result_binding_invalid")
    proof = raw.get("proof", {})
    if type(proof) is not dict:
        raise ExternalToolError("ide_result_proof_invalid")
    target = raw.get("path")
    if target is not None and (type(target) is not str or len(target) > 32768 or "\x00" in target):
        raise ExternalToolError("ide_result_target_invalid")
    source_operation_id = raw.get("operation_id")
    if source_operation_id is not None and (type(source_operation_id) is not str
                                           or len(source_operation_id) > 128):
        raise ExternalToolError("ide_result_binding_invalid")
    try:
        proof_json = encode_frame(proof)
        transport_json = encode_frame(transport)
        data_json = encode_frame({key: value for key, value in raw.items()
                                  if key not in {"execution", "_transport", "proof"}})
    except ProtocolError:
        raise ExternalToolError("ide_result_proof_invalid") from None
    return ToolExecutionResult(
        tool_name=call.spec.name, success=success, status=status, effect=call.semantics.effect,
        operation_id=transport["operation_id"], request_id=transport["request_id"],
        provider_instance_id=session.instance_id, catalog_revision=session.catalogue_hash,
        workspace_id=session.workspace_id, started_at=info["started_at"], completed_at=info["completed_at"],
        target=target, proof_json=proof_json, output=output, transport_json=transport_json,
        data_json=data_json,
        source_operation_id=source_operation_id,
    )
