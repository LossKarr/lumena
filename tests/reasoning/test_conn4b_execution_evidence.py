"""Execution effects require bound receipts, real rereads and terminal facts."""

from dataclasses import replace
import hashlib
import json

import pytest

from src.reasoning.execution_evidence import EvidenceError, EvidenceScope, verify_execution
from src.reasoning.plan_evidence import has_sufficient_proof
from src.reasoning.tool_result import ExecutionStatus
from src.reasoning.tool_semantics import Availability, ToolEffect
from src.runtime.execution_ledger import ExecutionLedger
from src.tools.ide_semantics import local_ide_semantics
from tests.reasoning.test_conn4a_structured_results import reply as reply, adapt
from tests.tools.test_conn3c_ide_capabilities import service as service


def meaning(record, action):
    return local_ide_semantics(
        action,
        instance_id=record.provider_instance_id,
        revision=record.catalog_revision,
        availability=Availability.READY,
    )


@pytest.fixture
def file_case(reply, tmp_path):
    path = tmp_path / "code.py"
    content = b"print('saved')\n"
    path.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    record = adapt(reply)
    semantics = meaning(record, "write_file")
    proof = {
        "schema_version": 1,
        "kind": "file_write",
        "target": str(path),
        "reread": True,
        "bytes": len(content),
        "before_sha256": None,
        "after_sha256": digest,
        "observed_at": "2026-09-13T12:00:00.500Z",
    }
    record = replace(
        record, tool_name=semantics.tool_name, effect=semantics.effect, target=str(path), proof_json=json.dumps(proof)
    )
    scope = EvidenceScope(
        tmp_path, record.workspace_id, record.provider_instance_id, record.catalog_revision, path, digest
    )
    return record, semantics, scope


@pytest.fixture
def process_case(reply, tmp_path):
    record = adapt(reply)
    semantics = meaning(record, "test_run")
    record = replace(
        record,
        tool_name=semantics.tool_name,
        effect=semantics.effect,
        status=ExecutionStatus.RUNNING,
        completed_at=None,
        target=None,
        data_json=json.dumps({"run": {"id": "owned-run"}}),
        proof_json="{}",
    )
    proof = {
        "schema_version": 1,
        "kind": "test_execution",
        "operation_id": record.operation_id,
        "process": {
            "run_id": "owned-run",
            "status": "passed",
            "exit_code": 0,
            "exit_observed": True,
            "cancellation_requested": False,
            "started_at": "2026-09-13T12:00:02Z",
            "completed_at": "2026-09-13T12:00:03Z",
            "cwd": str(tmp_path),
            "executable": "python",
            "args": ["-m", "pytest"],
            "output_sha256": "b" * 64,
        },
        "tests": {
            "run_id": "owned-run",
            "status": "passed",
            "completed_at": "2026-09-13T12:00:03Z",
            "counts": {"passed": 3, "failed": 0, "skipped": 1, "total": 4},
            "report_valid": True,
            "report_sha256": "c" * 64,
        },
    }
    completion = replace(
        record,
        tool_name="ide__operation_get",
        effect=ToolEffect.READ_ONLY,
        operation_id="d" * 32,
        request_id="followup",
        status=ExecutionStatus.SUCCEEDED,
        completed_at="2026-09-13T12:00:04Z",
        proof_json=json.dumps(proof),
        data_json=json.dumps({"operation": {"transportOperationId": record.operation_id}}),
    )
    scope = EvidenceScope(tmp_path, record.workspace_id, record.provider_instance_id, record.catalog_revision)
    return record, semantics, scope, completion


def test_file_reread_produces_canonical_evidence_and_metadata_ledger(file_case):
    record, semantics, scope = file_case
    evidence = verify_execution(*file_case)
    assert evidence.target == str(scope.expected_target.resolve())
    assert evidence.after_sha256 == scope.expected_after_sha256
    ledger = ExecutionLedger()
    entry = ledger.append_execution(iteration=1, result=record, evidence=evidence)
    assert ledger.has_source_mutation() and ledger.has_any_mutation()
    assert ledger.written_basenames() == {"code.py"}
    assert ledger.latest_for_target(evidence.target) is entry
    assert has_sufficient_proof(record.tool_name, "meaningless text", "create document", execution_evidence=evidence)
    assert "output" not in str(entry.to_dict()) and "saved" not in str(entry.to_dict())
    assert "code.py" not in repr(evidence)


@pytest.mark.parametrize(
    "change",
    [
        "stale",
        "wrong_target",
        "outside",
        "bad_hash",
        "wrong_size",
        "no_reread",
        "no_schema",
        "future",
        "readonly",
        "wrong_instance",
        "wrong_workspace",
    ],
)
def test_file_proofs_refuse_stale_cross_scope_or_inadequate_facts(file_case, tmp_path, change):
    record, semantics, scope = file_case
    proof = record.proof
    if change == "stale":
        scope.expected_target.write_text("changed after the tool finished")
    elif change == "wrong_target":
        other = tmp_path / "other.py"
        other.write_bytes(scope.expected_target.read_bytes())
        scope = replace(scope, expected_target=other)
    elif change == "outside":
        inner = tmp_path / "inner"
        inner.mkdir()
        scope = replace(scope, workspace=inner)
    elif change == "bad_hash":
        proof["after_sha256"] = "0" * 64
    elif change == "wrong_size":
        proof["bytes"] = True
    elif change == "no_reread":
        proof["reread"] = "true"
    elif change == "no_schema":
        proof["schema_version"] = True
    elif change == "future":
        proof["observed_at"] = "2026-09-14T12:00:00Z"
    elif change == "readonly":
        semantics = meaning(record, "get_status")
        record = replace(record, tool_name=semantics.tool_name, effect=semantics.effect)
    else:
        scope = replace(scope, **{("provider_instance_id" if change == "wrong_instance" else "workspace_id"): "other"})
    record = replace(record, proof_json=json.dumps(proof))
    try:
        assert verify_execution(record, semantics, scope) is None
    except EvidenceError as exc:
        assert str(tmp_path) not in str(exc)


def test_unverified_success_and_fake_text_cannot_create_mutation_or_green_tests(file_case):
    record, _, _ = file_case
    ledger = ExecutionLedger()
    entry = ledger.append_execution(iteration=1, result=record)
    entry.meta["test_outcome"] = {"green": True}
    assert not ledger.has_any_mutation() and not ledger.has_green_test_run()
    assert not has_sufficient_proof(record.tool_name, "saved ✅ tests passed 100% success", "tests")
    ledger.append(
        iteration=2, action="ide__test_run", success=True, proof="passed", meta={"test_outcome": {"green": True}}
    )
    assert not ledger.has_green_test_run()


def test_running_test_requires_owned_followup_and_is_recorded_once(process_case):
    record, semantics, scope, completion = process_case
    assert verify_execution(record, semantics, scope) is None
    evidence = verify_execution(record, semantics, scope, completion=completion)
    assert evidence.green_tests
    ledger = ExecutionLedger()
    ledger.append_execution(iteration=1, result=record)
    assert not ledger.has_green_test_run()
    entry = ledger.append_execution(iteration=2, result=record, evidence=evidence)
    assert ledger.has_green_test_run() and ledger.has_fresh_green_test_run()
    assert ledger.last_test_outcome()["passed"] == 3
    assert ledger.append_execution(iteration=99, result=record, evidence=evidence) is entry
    assert ledger.size == 2
    assert has_sufficient_proof(record.tool_name, "", "run tests", execution_evidence=evidence)
    assert not has_sufficient_proof(record.tool_name, "", "API accessible", execution_evidence=evidence)


@pytest.mark.parametrize(
    "change",
    [
        "other_run",
        "other_operation",
        "list_runs",
        "other_instance",
        "no_report",
        "report_pending",
        "no_exit",
        "bool_exit",
        "empty",
        "false_count",
        "ignore",
        "deselect",
        "fake_runner",
        "future",
        "running",
    ],
)
def test_test_verdict_needs_bound_report_and_observed_exit(process_case, change):
    record, semantics, scope, completion = process_case
    proof = completion.proof
    if change == "other_run":
        proof["process"]["run_id"] = "someone-else"
    elif change == "other_operation":
        proof["operation_id"] = "f" * 32
    elif change == "list_runs":
        completion = replace(completion, tool_name="ide__test_runs")
    elif change == "other_instance":
        completion = replace(completion, provider_instance_id="other")
    elif change == "no_report":
        proof.pop("tests")
    elif change == "report_pending":
        proof["tests"]["status"] = "running"
    elif change == "no_exit":
        proof["process"]["exit_observed"] = False
    elif change == "bool_exit":
        proof["process"]["exit_code"] = False
    elif change == "empty":
        proof["tests"]["counts"] = dict.fromkeys(["passed", "failed", "skipped", "total"], 0)
    elif change == "false_count":
        proof["tests"]["counts"]["passed"] = 50
    elif change in {"ignore", "deselect"}:
        proof["process"]["args"].append("--" + change + "=tests/failing.py")
    elif change == "fake_runner":
        proof["process"]["args"] = ["-c", "print('100 passed')"]
    elif change == "future":
        proof["process"]["completed_at"] = "2026-09-14T12:00:00Z"
    else:
        proof["process"]["status"] = "running"
        proof["process"]["completed_at"] = None
    completion = replace(completion, proof_json=json.dumps(proof))
    try:
        assert verify_execution(record, semantics, scope, completion=completion) is None
    except EvidenceError:
        pass


@pytest.mark.parametrize("change", ["exit_nonzero", "report_failed", "cancelled"])
def test_real_failed_test_facts_stay_red_even_when_text_says_passed(process_case, change):
    record, semantics, scope, completion = process_case
    proof = completion.proof
    if change == "exit_nonzero":
        proof["process"].update(exit_code=7, status="failed")
    elif change == "report_failed":
        proof["tests"].update(status="failed", counts={"passed": 2, "failed": 1, "skipped": 1, "total": 4})
    else:
        proof["process"].update(status="cancelled", cancellation_requested=True)
    completion = replace(completion, proof_json=json.dumps(proof), output="all passed ✅")
    evidence = verify_execution(record, semantics, scope, completion=completion)
    assert evidence is not None and not evidence.green_tests and not evidence.success
    ledger = ExecutionLedger()
    ledger.append_execution(iteration=1, result=record, evidence=evidence)
    assert not ledger.has_green_test_run() and not ledger.last_test_outcome()["green"]


def test_test_freshness_uses_start_before_writes_not_late_reply_or_replay(file_case, process_case):
    file, file_semantics, file_scope = file_case
    run, semantics, scope, completion = process_case
    evidence = verify_execution(run, semantics, scope, completion=completion)
    ledger = ExecutionLedger()
    ledger.append_execution(iteration=1, result=run, evidence=evidence)
    proof = file.proof
    proof["observed_at"] = "2026-09-13T12:00:02.500Z"
    file = replace(file, operation_id="e" * 32, proof_json=json.dumps(proof), completed_at="2026-09-13T12:00:02.800Z")
    ledger.append_execution(iteration=2, result=file, evidence=verify_execution(file, file_semantics, file_scope))
    assert not ledger.has_fresh_green_test_run()
    ledger.append_execution(iteration=3, result=run, evidence=evidence)
    assert not ledger.has_fresh_green_test_run()


def test_ledger_rejects_cross_operation_evidence_and_conflicting_replay(file_case):
    record, _, _ = file_case
    evidence = verify_execution(*file_case)
    ledger = ExecutionLedger()
    with pytest.raises(EvidenceError, match="mismatch"):
        ledger.append_execution(iteration=1, result=replace(record, operation_id="b" * 32), evidence=evidence)
    ledger.append_execution(iteration=2, result=record, evidence=evidence)
    with pytest.raises(EvidenceError, match="conflict"):
        ledger.append_execution(iteration=3, result=record, evidence=replace(evidence, proof_digest="f" * 64))


def test_reset_clears_operation_index_and_native_projection_remains_compatible(file_case):
    record, _, _ = file_case
    evidence = verify_execution(*file_case)
    ledger = ExecutionLedger()
    first = ledger.append_execution(iteration=1, result=record, evidence=evidence)
    ledger.clear()
    assert ledger.size == 0
    assert ledger.append_execution(iteration=2, result=record, evidence=evidence) is not first
    legacy = ledger.append(iteration=3, action="read_file", success=True, meta={"nested": {"value": 1}})
    legacy.to_dict()["meta"]["nested"]["value"] = 2
    assert legacy.meta["nested"]["value"] == 1
    assert set(legacy.to_dict()) == {"iteration", "action", "target", "success", "proof", "timestamp", "meta"}


def test_process_completion_never_becomes_a_test_report(process_case):
    record, _, scope, completion = process_case
    semantics = meaning(record, "task_run")
    record = replace(record, tool_name=semantics.tool_name, effect=semantics.effect)
    proof = completion.proof
    proof["kind"] = "process"
    proof.pop("tests")
    completion = replace(completion, proof_json=json.dumps(proof))
    evidence = verify_execution(record, semantics, scope, completion=completion)
    assert evidence.success and not evidence.green_tests
    assert not has_sufficient_proof(record.tool_name, "tests passed", "tests", execution_evidence=evidence)
    assert has_sufficient_proof(record.tool_name, "", "commande exécutée", execution_evidence=evidence)
