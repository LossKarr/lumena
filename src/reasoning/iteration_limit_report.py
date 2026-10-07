"""Deterministic, honest report when a ReAct run exhausts its budget."""
from __future__ import annotations


def build_iteration_limit_report(
    max_iterations: int,
    task_plan,
    execution_ledger,
    last_observation: str | None,
) -> str:
    completed = [
        str(getattr(task, "description", "") or "").strip()
        for task in (task_plan or [])
        if getattr(task, "completed", False)
    ]
    pending = [
        str(getattr(task, "description", "") or "").strip()
        for task in (task_plan or [])
        if not getattr(task, "completed", False)
    ]
    mutations: list[str] = []
    try:
        for entry in execution_ledger.successful_mutations():
            action = str(getattr(entry, "action", "") or "").strip()
            if action and action not in mutations:
                mutations.append(action)
    except Exception:
        mutations = []

    lines = [
        f"J'ai atteint la limite configurée de {max(0, int(max_iterations))} itérations.",
        "La tâche reste incomplète ; je ne la présente pas comme terminée.",
    ]
    if completed:
        lines.extend(("", "Étapes terminées :"))
        lines.extend(f"- {item}" for item in completed[:8] if item)
    if mutations:
        lines.extend(("", "Actions réussies enregistrées :"))
        lines.extend(f"- {item}" for item in mutations[:12])
    if pending:
        lines.extend(("", "Étapes restantes :"))
        lines.extend(f"- {item}" for item in pending[:8] if item)
    observation = str(last_observation or "").strip()
    if observation:
        lines.extend(("", "Dernière observation utile :", observation[:2000]))
    return "\n".join(lines)
