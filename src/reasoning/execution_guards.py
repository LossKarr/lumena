"""Effect-aware evidence decisions shared by ledger and final guards."""

from __future__ import annotations

import hashlib
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from .execution_evidence import VerifiedExecutionEvidence
from .plan_evidence import ProofCapability
from .tool_semantics import ToolEffect
from .tool_result import ToolExecutionResult


@lru_cache(maxsize=128)
def _legacy_family_capabilities(family: frozenset[str]) -> frozenset[ProofCapability]:
    """Interpret an existing native family; never manufacture native tool aliases."""
    from ..runtime.execution_ledger import INTENT_TO_MUTATION_FAMILY

    result = set()
    if family & INTENT_TO_MUTATION_FAMILY["code_edit"]:
        result.add(ProofCapability.FILE_WRITE)
    if family & {"run_command", "run_shell", "exec_command", "run_tests"}:
        result.update({ProofCapability.PROCESS_LAUNCH, ProofCapability.TEST_EXECUTION})
    return frozenset(result)


def verified_family_matches(evidence, family: frozenset[str]) -> bool:
    return (
        type(evidence) is VerifiedExecutionEvidence
        and evidence.success
        and bool(evidence.capabilities & _legacy_family_capabilities(frozenset(family)))
    )


def evidence_is_current(evidence) -> bool:
    if type(evidence) is not VerifiedExecutionEvidence or not evidence.success:
        return False
    if evidence.effect is not ToolEffect.FILE_WRITE:
        return True
    if not evidence.target or not evidence.after_sha256:
        return False
    path = Path(evidence.target)
    try:
        if not path.is_absolute() or path.resolve(strict=True) != path or not path.is_file():
            return False
        before = path.stat()
        if before.st_size > 16 * 1024 * 1024:
            return False
        with path.open("rb") as stream:
            data = stream.read(16 * 1024 * 1024 + 1)
        after = path.stat()

        def identity(value):
            return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns

        return (
            identity(before) == identity(after)
            and path.resolve(strict=True) == path
            and hashlib.sha256(data).hexdigest() == evidence.after_sha256
        )
    except (OSError, ValueError, RuntimeError):
        return False


def latest_file_evidence_is_current(entries) -> bool:
    latest = {}
    for entry in entries:
        evidence = getattr(entry, "evidence", None)
        if (
            type(evidence) is VerifiedExecutionEvidence
            and evidence.success
            and evidence.effect is ToolEffect.FILE_WRITE
            and evidence.target
        ):
            key = Path(evidence.target)
            previous = latest.get(key)
            if (previous is None or datetime.fromisoformat(evidence.completed_at.replace("Z", "+00:00"))
                    >= datetime.fromisoformat(previous.completed_at.replace("Z", "+00:00"))):
                latest[key] = evidence
    return all(evidence_is_current(evidence) for evidence in latest.values())


def structured_observation_success(observation, tool_name: str) -> bool:
    """Accepted/running replies and readonly tools are not completed actions."""
    from ..utils.external_tool_names import is_ide_tool_name

    if not is_ide_tool_name(tool_name):
        return bool(getattr(observation, "success", False))
    evidence = getattr(observation, "execution_evidence", None)
    result = getattr(observation, "execution", None)
    return (
        getattr(observation, "success", False) is True
        and type(result) is ToolExecutionResult
        and type(evidence) is VerifiedExecutionEvidence
        and evidence.tool_name == tool_name
        and all(
            getattr(evidence, key) == getattr(result, key)
            for key in (
                "tool_name",
                "operation_id",
                "provider_instance_id",
                "catalog_revision",
                "workspace_id",
                "effect",
            )
        )
        and evidence.success
        and bool(evidence.capabilities)
        and evidence_is_current(evidence)
    )


def lock_ide_execution_message(final_text: str, ledger) -> tuple[str, dict]:
    from ..runtime.execution_ledger import ExecutionLedger
    from ..utils.external_tool_names import is_ide_tool_name
    from .final_guards import apply_ide_execution_truth_lock
    from .hallucination_guard import hallucination_retry_query

    unchanged = (final_text, {"changed": False, "overclaim": False})
    if not final_text or not isinstance(ledger, ExecutionLedger):
        return unchanged
    entries = ledger.recent(ledger.size)
    if not any(is_ide_tool_name(entry.action) for entry in entries):
        return unchanged
    try:
        verified = tuple(entry.evidence for entry in entries if evidence_is_current(entry.evidence))
        action_refusal, _ = hallucination_retry_query(
            final_text,
            "",
            set(ledger.successful_actions()),
            0,
            execution_evidence=verified,
            require_structured_effects=True,
        )
        return apply_ide_execution_truth_lock(
            final_text,
            action_unproven=action_refusal is not None,
            has_fresh_green_test=ledger.has_fresh_green_test_run(),
            last_test_outcome=ledger.last_test_outcome(),
            server_verified=any(
                e.capabilities & {ProofCapability.HTTP_PROBE, ProofCapability.BROWSER_PROBE} for e in verified
            ),
            published=ledger.has_published(),
        )
    except Exception:
        return (
            "Je ne peux pas confirmer l’exécution : la vérification des preuves a échoué.",
            {"changed": True, "overclaim": True, "ide_execution_unproven": True},
        )
