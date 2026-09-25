"""Redacted steering events and bounded runtime metrics."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict


def publish_steering_event(stage: str, command: Dict[str, Any]) -> None:
    """Best-effort event without instruction text, identity or scope."""
    try:
        from src.telemetry import publish_trace

        publish_trace(
            stage=stage,
            status=str(command.get("status") or "ok"),
            mode="agent",
            task_id=str(command.get("target_task_id") or ""),
            summary=(
                f"command={command.get('command_id')} sequence={command.get('sequence')} "
                f"policy={command.get('delivery_policy')}"
            ),
        )
    except Exception:
        pass


def steering_metrics(orchestrator: Any) -> Dict[str, Any]:
    statuses: Dict[str, int] = {}
    delivery_latencies = []
    for task in orchestrator.list_all_tasks(limit=500):
        for command in (task.get("metadata") or {}).get("steering_commands") or []:
            if not isinstance(command, dict):
                continue
            status = str(command.get("status") or "unknown")
            statuses[status] = statuses.get(status, 0) + 1
            delivered_at = (command.get("delivery") or {}).get("delivered_at")
            created_at = command.get("created_at")
            if delivered_at and created_at:
                try:
                    delivery_latencies.append(
                        max(0.0, (
                            datetime.fromisoformat(str(delivered_at))
                            - datetime.fromisoformat(str(created_at))
                        ).total_seconds() * 1000.0)
                    )
                except (TypeError, ValueError):
                    pass
    return {
        "commands_total": sum(statuses.values()),
        "by_status": statuses,
        "pending": statuses.get("pending", 0),
        "late": statuses.get("late", 0),
        "rejected": statuses.get("rejected", 0),
        "delivery_latency_ms_max": max(delivery_latencies) if delivery_latencies else None,
    }
