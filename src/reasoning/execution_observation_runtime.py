"""Explicit observation-to-ledger integration; no provider policy in ReAct."""

from __future__ import annotations

import re

from ..runtime.execution_ledger import ExecutionLedger, _extract_proof, _extract_target
from ..utils.external_tool_names import is_ide_tool_name
from .execution_evidence import EvidenceError, VerifiedExecutionEvidence
from .execution_guards import evidence_is_current, structured_observation_success
from .tool_result import ToolExecutionResult


def observation_execution_fields(observation) -> dict:
    """Keep private facts when only the model-visible text is reconstructed."""
    return {key: getattr(observation, key, None) for key in ("execution", "execution_evidence")}


def bound_observation_evidence(name: str, observation):
    result = getattr(observation, "execution", None)
    evidence = getattr(observation, "execution_evidence", None)
    if (type(result) is not ToolExecutionResult or result.tool_name != name
            or type(evidence) is not VerifiedExecutionEvidence):
        return None
    if any(getattr(evidence, key) != getattr(result, key) for key in (
        "tool_name", "operation_id", "provider_instance_id", "catalog_revision", "workspace_id", "effect",
    )):
        return None
    return evidence


def record_tool_observation(
    ledger, *, iteration: int, name: str, args: dict, observation,
    duration_seconds: float = 0.0, intent=None, via: str | None = None,
    legacy_meta: dict | None = None,
):
    """Preserve the native ledger trace; external receipts never use text proof."""
    if is_ide_tool_name(name):
        result = getattr(observation, "execution", None)
        conflict = False
        if type(result) is ToolExecutionResult and result.tool_name == name:
            evidence = bound_observation_evidence(name, observation)
            try:
                return ledger.append_execution(iteration=iteration, result=result, evidence=evidence)
            except EvidenceError:
                # Retain a refusal, including a conflicting replay, so final delivery
                # cannot mistake a missing entry for a native-only conversation.
                conflict = True
        return ledger.append(
            iteration=iteration, action=name, success=False,
            meta={"structured_execution": True, "verified": False, "execution_conflict": conflict},
        )

    target = _extract_target(name, args)
    proof = _extract_proof(name, observation.content or "", observation.success)
    meta = {"duration_ms": round(duration_seconds * 1000, 1), "intent": intent}
    if legacy_meta is not None:
        meta = dict(legacy_meta)
    elif via is not None:
        meta["via"] = via
    # This is the historical single-tool path. Parallel native children did not
    # parse commands here; preserve that behavior in the compatibility branch.
    elif name in ("run_command", "run_shell", "exec_command"):
        try:
            from .test_proof import is_test_command, parse_test_outcome

            command = str(args.get("command", "") or "")
            meta["command"] = command[:200]
            if is_test_command(command):
                meta["test_outcome"] = parse_test_outcome(
                    command, observation.content or "", getattr(observation, "exit_code", None),
                )
        except Exception:
            pass
    return ledger.append(
        iteration=iteration, action=name, target=target, success=observation.success, proof=proof, meta=meta,
    )


async def execute_with_observed_receipt(tools, ledger, name, args, *, caller, iteration: int):
    """Retain received facts before post-tool cancellation or a queued next call."""
    observation = await tools.execute(name, args, caller=caller)
    if isinstance(ledger, ExecutionLedger):
        if is_ide_tool_name(name):
            record_tool_observation(ledger, iteration=iteration, name=name, args=args or {}, observation=observation)
        elif name == "parallel_tools":
            for child in getattr(observation, "sub_results", ()) or ():
                child_name = getattr(child, "tool_name", "")
                if is_ide_tool_name(child_name):
                    record_tool_observation(
                        ledger, iteration=iteration, name=child_name,
                        args=getattr(child, "args", {}) or {}, observation=child,
                    )
    return observation


def successful_observation_names(name: str, observation) -> set[str]:
    if is_ide_tool_name(name):
        return {name} if structured_observation_success(observation, name) else set()
    if not observation.success:
        return set()
    names = {name}
    if name == "parallel_tools" and observation.content:
        names.update(
            child for child in re.findall(r"✅\s*\d+\.\s*([A-Za-z_]\w*)", observation.content)
            if not is_ide_tool_name(child)
        )
        names.update(
            child.tool_name for child in getattr(observation, "sub_results", ()) or ()
            if is_ide_tool_name(getattr(child, "tool_name", ""))
            and structured_observation_success(child, child.tool_name)
        )
    return names


def execution_guard_context(ledger) -> dict:
    if not isinstance(ledger, ExecutionLedger):
        return {}
    entries = ledger.recent(ledger.size)
    return {
        "execution_evidence": tuple(entry.evidence for entry in entries if evidence_is_current(entry.evidence)),
        "require_structured_effects": any(is_ide_tool_name(entry.action) for entry in entries),
    }


def plan_execution_fields(name: str, observation) -> dict:
    # Native call signatures stay exact, including third-party overrides.
    return {"execution_observation": observation} if is_ide_tool_name(name) else {}
