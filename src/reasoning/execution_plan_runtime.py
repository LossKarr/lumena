"""Conservative plan completion from a bound execution, never IDE prose."""

from __future__ import annotations

from pathlib import Path
import re

from .execution_guards import structured_observation_success
from .execution_observation_runtime import bound_observation_evidence
from .plan_evidence import has_verified_execution_proof, TaskCompletionStatus
from .tool_semantics import ToolEffect


_FILES = re.compile(
    r"(?<![\w])(?:[A-Za-z]:[/\\]|[/\\])?(?:[\w.-]+[/\\])*[\w.-]+"
    r"\.(?:py|js|mjs|jsx|ts|tsx|json|html|css|md|txt|toml|ya?ml|csv|xml)\b",
    re.IGNORECASE,
)
_WRITE = re.compile(r"\b(?:ecri\w*|écri\w*|crée\w*|cree\w*|crea\w*|modif\w*|sauvegard\w*|"
                    r"enregistr\w*|write|writ\w*|sav\w*|updat\w*|corrig\w*|fix\w*)\b", re.IGNORECASE)
_TESTS = re.compile(r"\b(?:tests?|pytest|vitest|jest)\b", re.IGNORECASE)


def _file_references(description: str) -> list[str]:
    references = []

    def quoted(match):
        value = match.group(1) or match.group(2)
        if _FILES.search(value) and re.search(r"\.[a-zA-Z0-9]+$", value):
            references.append(value)
            return " "
        return match.group()

    remaining = re.sub(r'`([^`\n]{1,1024})`|"([^"\n]{1,1024})"', quoted, description)
    return references + _FILES.findall(remaining)


def _same_file(reference: str, target: str) -> bool:
    requested = Path(reference.replace("\\", "/"))
    actual = Path(target)
    if ".." in requested.parts:
        return False
    if requested.is_absolute():
        return requested == actual
    return len(requested.parts) <= len(actual.parts) and Path(*actual.parts[-len(requested.parts):]) == requested


def apply_verified_execution_plan(entry) -> None:
    observation = entry.execution_observation
    if not structured_observation_success(observation, entry.tool_name):
        return
    evidence = bound_observation_evidence(entry.tool_name, observation)
    proof_summary = f"{evidence.effect.value}: operation {evidence.operation_id}, preuve {evidence.proof_digest}"
    if any(task.completed and task.completion_evidence == proof_summary for task in entry.task_plan):
        return
    for task in entry.task_plan:
        if task.completed or not has_verified_execution_proof(evidence, task.description):
            continue
        files = _file_references(task.description)
        if evidence.effect is ToolEffect.FILE_WRITE:
            if (not _WRITE.search(task.description) or not files
                    or not all(_same_file(reference, evidence.target) for reference in files)):
                continue
            status = TaskCompletionStatus.CREATED
        elif evidence.effect is ToolEffect.TEST_EXECUTION:
            # A runner's green report does not prove a named file subset. Scope
            # aggregation and explicit runner-to-task mappings belong to CONN-5.
            if files or not _TESTS.search(task.description) or not entry.obtenir_ledger().has_fresh_green_test_run():
                continue
            status = TaskCompletionStatus.VERIFIED
        else:
            continue
        task.completed = True
        task.completed_at_iteration = entry.iteration
        task.completed_by_tool = entry.tool_name
        task.completion_status = status
        task.completion_evidence = proof_summary
        task.completion_confidence = "strong"
        break  # One observation cannot close several unrelated plan steps.
