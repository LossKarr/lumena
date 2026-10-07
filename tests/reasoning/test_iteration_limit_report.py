from dataclasses import dataclass

from src.reasoning.iteration_limit_report import build_iteration_limit_report
from src.runtime.execution_ledger import ExecutionLedger


@dataclass
class _Task:
    description: str
    completed: bool


def test_iteration_limit_report_is_honest_and_actionable():
    ledger = ExecutionLedger()
    ledger.append(
        iteration=1,
        action="mcp__studio__execute_luau",
        success=True,
    )
    report = build_iteration_limit_report(
        max_iterations=100,
        task_plan=[_Task("Construire les véhicules", True), _Task("Play test", False)],
        execution_ledger=ledger,
        last_observation="Les trois véhicules existent.",
    )
    assert "100 itérations" in report
    assert "reste incomplète" in report
    assert "Construire les véhicules" in report
    assert "Play test" in report
    assert "mcp__studio__execute_luau" in report
    assert "Les trois véhicules existent" in report
