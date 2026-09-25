"""Observable IDE attempts with no parameters, output, paths or model text."""

from __future__ import annotations

from ..telemetry.trace_bus import publish_trace
from ..runtime.context import get_current_runtime_context


def trace_ide_attempt(
    attempt: str, *, name: str = "ide__unresolved", semantics=None, result=None, reason: str = "preparing"
) -> None:
    status = result.status.value if result is not None else "queued" if reason == "preparing" else "failed"
    execution = {
        "attempt_id": attempt,
        "effect": semantics.effect.value if semantics else "UNKNOWN",
        "status": status,
        "verified": False,
        "reason": reason,
    }
    if semantics is not None:
        execution.update(catalog_revision=semantics.catalog_revision)
        if len(semantics.provider_instance_id) == 32:
            execution["instance_id"] = semantics.provider_instance_id
    if result is not None:
        execution.update(operation_id=result.operation_id, workspace_id=result.workspace_id)
    try:
        context = get_current_runtime_context()
        publish_trace(
            stage="ide_tool_prepare" if reason == "preparing" else "ide_tool_result",
            status=status,
            tool_name=name,
            provider="lumena.ide",
            execution=execution,
            channel=context.channel if context else None,
            request_id=context.request_id if context else None,
            conversation_id=context.conversation_id if context else None,
            task_id=context.task_id if context else None,
        )
    except Exception:
        pass  # An unavailable observer must not replace an execution result.
