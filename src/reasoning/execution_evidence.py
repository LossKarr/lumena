"""Verify authenticated execution facts against local meaning and host scope.

No observation text, model argument or generic run list can supply a proof.
Authorization happens before execution; this resolver does not grant authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re

from .plan_evidence import ProofCapability
from .tool_result import ToolExecutionResult
from .tool_semantics import ToolEffect, ToolSemantics

_HASH = re.compile(r"[0-9a-f]{64}\Z")
_MAX_FILE = 16 * 1024 * 1024


class EvidenceError(ValueError):
    """Fixed error codes; never disclose paths, command arguments or output."""


@dataclass(frozen=True, slots=True)
class EvidenceScope:
    workspace: Path = field(repr=False)
    workspace_id: str
    provider_instance_id: str
    catalog_revision: str
    expected_target: Path | None = field(default=None, repr=False)
    expected_after_sha256: str | None = None


@dataclass(frozen=True, slots=True)
class VerifiedExecutionEvidence:
    tool_name: str
    operation_id: str
    provider_instance_id: str
    catalog_revision: str
    workspace_id: str
    effect: ToolEffect
    capabilities: frozenset[ProofCapability]
    target: str | None = field(repr=False)
    started_at: str
    completed_at: str
    success: bool
    proof_digest: str
    before_sha256: str | None = None
    after_sha256: str | None = None
    test_counts: tuple[int, int, int, int] | None = None
    exit_code: int | None = None
    test_scope_digest: str | None = None

    @property
    def green_tests(self) -> bool:
        return (
            self.success
            and self.effect is ToolEffect.TEST_EXECUTION
            and self.test_counts is not None
            and self.test_counts[0] > 0
            and self.test_counts[1] == 0
            and self.exit_code == 0
        )

    def test_outcome(self) -> dict | None:
        if self.test_counts is None:
            return None
        passed, failed, skipped, _ = self.test_counts
        return {
            "is_test_cmd": True,
            "green": self.green_tests,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "errors": 0,
            "exit_code": self.exit_code,
            "structured": True,
        }


def _time(value: object) -> datetime:
    if type(value) is not str or len(value) > 64:
        raise EvidenceError("evidence_time_invalid")
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise EvidenceError("evidence_time_invalid") from None
    if result.tzinfo is None:
        raise EvidenceError("evidence_time_invalid")
    return result


def _hash(value: object) -> bool:
    return type(value) is str and _HASH.fullmatch(value) is not None


def _canonical(scope: EvidenceScope, value: object, *, file: bool = False) -> Path:
    if not isinstance(value, (str, Path)) or not str(value) or "\x00" in str(value):
        raise EvidenceError("evidence_target_invalid")
    try:
        root = scope.workspace.resolve(strict=True)
        raw = Path(value)
        target = (raw if raw.is_absolute() else root / raw).resolve(strict=True)
        if not root.is_dir() or not target.is_relative_to(root) or (file and not target.is_file()):
            raise EvidenceError("evidence_target_outside_scope")
        return target
    except (OSError, RuntimeError, ValueError):
        raise EvidenceError("evidence_target_invalid") from None


def _binding(result: ToolExecutionResult, scope: EvidenceScope) -> None:
    if (
        type(result) is not ToolExecutionResult
        or result.provider_instance_id != scope.provider_instance_id
        or result.catalog_revision != scope.catalog_revision
        or result.workspace_id != scope.workspace_id
    ):
        raise EvidenceError("evidence_binding_invalid")


def verify_execution(
    result: ToolExecutionResult,
    semantics: ToolSemantics,
    scope: EvidenceScope,
    *,
    completion: ToolExecutionResult | None = None,
) -> VerifiedExecutionEvidence | None:
    """Use the original dispatch receipt, optionally its bounded internal follow-up.

    The caller retains the original receipt and host scope. A completion is an
    authenticated operation_get reply for that receipt, never an arbitrary run
    found by the model. Its timestamps remain original, including on replay.
    """
    _binding(result, scope)
    if (
        type(semantics) is not ToolSemantics
        or result.tool_name != semantics.tool_name
        or result.effect is not semantics.effect
        or semantics.provider_instance_id != scope.provider_instance_id
        or semantics.catalog_revision != scope.catalog_revision
    ):
        raise EvidenceError("evidence_semantics_invalid")
    effect = semantics.effect
    if effect not in {
        ToolEffect.FILE_WRITE,
        ToolEffect.PROCESS_LAUNCH,
        ToolEffect.PROCESS_COMPLETION,
        ToolEffect.TEST_EXECUTION,
    }:
        return None
    if effect is ToolEffect.FILE_WRITE:
        if completion is not None or not result.completed_successfully:
            return None
        proof = result.proof
        if (
            proof.get("schema_version") != 1
            or type(proof.get("schema_version")) is not int
            or proof.get("kind") != "file_write"
            or proof.get("reread") is not True
            or not _hash(proof.get("after_sha256"))
            or proof.get("before_sha256") is not None
            and not _hash(proof["before_sha256"])
        ):
            return None
        if scope.expected_target is None:
            raise EvidenceError("evidence_expected_target_required")
        target = _canonical(scope, result.target, file=True)
        if target != _canonical(scope, proof.get("target"), file=True) or target != _canonical(
            scope, scope.expected_target, file=True
        ):
            raise EvidenceError("evidence_target_mismatch")
        try:
            with target.open("rb") as stream:
                before_read = os.fstat(stream.fileno())
                data = stream.read(_MAX_FILE + 1)
                after_read = os.fstat(stream.fileno())
            current = target.stat()
        except OSError:
            raise EvidenceError("evidence_reread_failed") from None
        identities = [
            (item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns) for item in (before_read, after_read, current)
        ]
        if identities[0] != identities[1] or identities[1] != identities[2] or target.resolve() != target:
            raise EvidenceError("evidence_file_revision_changed")
        if (
            len(data) > _MAX_FILE
            or type(proof.get("bytes")) is not int
            or len(data) != proof["bytes"]
            or hashlib.sha256(data).hexdigest() != proof["after_sha256"]
            or scope.expected_after_sha256 is not None
            and scope.expected_after_sha256 != proof["after_sha256"]
        ):
            raise EvidenceError("evidence_file_revision_changed")
        if not _time(result.started_at) <= _time(proof.get("observed_at")) <= _time(result.completed_at):
            raise EvidenceError("evidence_time_invalid")
        return VerifiedExecutionEvidence(
            result.tool_name,
            result.operation_id,
            result.provider_instance_id,
            result.catalog_revision,
            scope.workspace_id,
            effect,
            frozenset({ProofCapability.FILE_WRITE}) & semantics.proof_capabilities,
            str(target),
            result.started_at,
            result.completed_at,
            True,
            proof["after_sha256"],
            proof.get("before_sha256"),
            proof["after_sha256"],
        )
    source = completion or result
    _binding(source, scope)
    if completion is not None:
        operation = source.data.get("operation", {})
        if (
            source.tool_name != "ide__operation_get"
            or source.effect is not ToolEffect.READ_ONLY
            or not source.completed_successfully
            or type(operation) is not dict
            or operation.get("transportOperationId") != result.operation_id
        ):
            raise EvidenceError("evidence_followup_invalid")
    proof = source.proof
    expected_kind = "test_execution" if effect is ToolEffect.TEST_EXECUTION else "process"
    if (
        type(proof.get("schema_version")) is not int
        or proof["schema_version"] != 1
        or proof.get("kind") != expected_kind
        or proof.get("operation_id") != result.operation_id
    ):
        return None
    process = proof.get("process")
    original_run = result.data.get("run", {})
    if (
        type(process) is not dict
        or type(original_run) is not dict
        or not original_run.get("id")
        or process.get("run_id") != original_run["id"]
    ):
        raise EvidenceError("evidence_run_mismatch")
    if process.get("status") == "running" or process.get("completed_at") is None:
        return None
    if (
        process.get("status") not in {"passed", "failed", "cancelled"}
        or process.get("exit_observed") is not True
        or type(process.get("exit_code")) is not int
        or type(process.get("cancellation_requested")) is not bool
        or not _hash(process.get("output_sha256"))
    ):
        return None
    target = _canonical(scope, process.get("cwd"))
    start, end = process.get("started_at"), process.get("completed_at")
    if not _time(result.started_at) <= _time(start) <= _time(end) <= _time(source.completed_at):
        raise EvidenceError("evidence_time_invalid")
    args, executable = process.get("args"), process.get("executable")
    if (
        type(executable) is not str
        or not executable
        or len(executable) > 32768
        or type(args) is not list
        or len(args) > 256
        or any(type(arg) is not str or len(arg) > 32768 for arg in args)
    ):
        raise EvidenceError("evidence_command_invalid")
    success = process["status"] == "passed" and process["exit_code"] == 0 and not process["cancellation_requested"]
    test_counts = None
    digest = process["output_sha256"]
    capability = ProofCapability.PROCESS_LAUNCH
    if effect is ToolEffect.TEST_EXECUTION:
        tests = proof.get("tests")
        command = " ".join([executable, *args]).lower()
        if (
            type(tests) is not dict
            or tests.get("run_id") != process["run_id"]
            or tests.get("report_valid") is not True
            or not _hash(tests.get("report_sha256"))
            or tests.get("completed_at") != end
            or tests.get("status") not in {"passed", "failed", "cancelled"}
            or re.search(r"\b(pytest|vitest|jest)\b", command) is None
            or re.search(r"--(?:ignore|deselect|exclude|passwithnotests)\b", command)
        ):
            return None
        counts = tests.get("counts")
        keys = ("passed", "failed", "skipped", "total")
        if (
            type(counts) is not dict
            or set(counts) != set(keys)
            or any(type(counts[key]) is not int or not 0 <= counts[key] <= 100000 for key in keys)
            or counts["total"] != sum(counts[key] for key in keys[:3])
            or counts["total"] == 0
        ):
            return None
        test_counts = tuple(counts[key] for key in keys)
        success = success and tests["status"] == "passed" and counts["passed"] > 0 and counts["failed"] == 0
        digest, capability = tests["report_sha256"], ProofCapability.TEST_EXECUTION
    return VerifiedExecutionEvidence(
        result.tool_name,
        result.operation_id,
        result.provider_instance_id,
        result.catalog_revision,
        scope.workspace_id,
        effect,
        frozenset({capability}) & semantics.proof_capabilities,
        str(target),
        start,
        end,
        success,
        digest,
        test_counts=test_counts,
        exit_code=process["exit_code"],
        test_scope_digest=(
            hashlib.sha256(original_run["targetId"].encode()).hexdigest()
            if effect is ToolEffect.TEST_EXECUTION
            and type(original_run.get("targetId")) is str
            and 0 < len(original_run["targetId"]) <= 32768
            else None
        ),
    )
