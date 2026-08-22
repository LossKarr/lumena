"""M105 - complete-only mission invariants found by the AquaDose canary."""
from __future__ import annotations

from types import SimpleNamespace

from src.agents.sub_agent import _is_simple_test
from src.reasoning.react import ReActLoop, TaskItem
from src.reasoning.plan_progress import (
    delegation_task_blocks,
    final_requires_operational_proof,
    tool_explicit_task_blocks,
)
from src.subagents.mission_budget import (
    deadline_final_exit_allowed,
    deadline_hard_net_fires,
    extract_unambiguous_target_file,
)


def test_codeagent_complex_implementation_with_tests_is_not_test_fast_path():
    assert not _is_simple_test("Implemente l'application Flask et ses tests pytest")
    assert not _is_simple_test("Ecris puis remplis tests/test_app.py")
    assert not _is_simple_test("Redige le test pytest de l'API")
    assert _is_simple_test("Lance les tests")


def test_writing_pytest_tests_is_mutation_not_pytest_execution():
    task = "Ecrire les tests pytest dans tests/test_app.py"
    assert not tool_explicit_task_blocks("edit_file", task)
    assert tool_explicit_task_blocks("read_file", "Lancer pytest jusqu'au vert")
    assert not tool_explicit_task_blocks("run_command", "Lancer pytest jusqu'au vert")


def test_delegation_requires_a_real_delegation_tool_and_blocks_final():
    task = "Deleguer la creation du code Flask au CodeAgent"
    assert delegation_task_blocks("write_file", task)
    assert not delegation_task_blocks("delegate_task", task)
    assert final_requires_operational_proof(task)


def test_multifile_objective_has_no_single_artifact_escape_hatch():
    objective = "Ecris SOURCES.md, data/cultures.csv et rapport_aquadose.pdf"
    assert extract_unambiguous_target_file(objective) is None
    assert extract_unambiguous_target_file("Ecris seulement rapport.pdf") == "rapport.pdf"


def test_partial_never_relaxes_or_disarms_deadline_guards():
    assert not deadline_final_exit_allowed(
        partial_due_to_deadline=True,
        target_file="rapport.pdf",
        artifact_written=True,
    )
    assert deadline_hard_net_fires(
        steered=True,
        remaining_s=-300,
        grace_s=120,
        artifact_written=True,
    )


def _loop_with_task(description: str) -> ReActLoop:
    loop = ReActLoop(llm_chat_func=None)
    loop._task_plan = [TaskItem(description=description)]
    loop._last_auto_advance_iter = -1
    return loop


def test_react_delegation_credit_requires_delegate_tool_end_to_end():
    loop = _loop_with_task("Deleguer la creation du backend au CodeAgent")
    loop._update_plan_progress(
        "write_file", {"path": "app.py"}, "Fichier ecrit: app.py", 1,
    )
    assert not loop._task_plan[0].completed

    loop._update_plan_progress(
        "delegate_task", {"description": "creer le backend"},
        "CodeAgent termine avec une mutation prouvee", 2,
    )
    assert loop._task_plan[0].completed


def test_react_edit_can_complete_writing_pytest_file_end_to_end():
    loop = _loop_with_task("Ecrire les tests pytest dans tests/test_app.py")
    loop._update_plan_progress(
        "edit_file", {"path": "tests/test_app.py"},
        "Fichier modifie: tests/test_app.py", 1,
    )
    assert loop._task_plan[0].completed


def _contract_code_worker() -> ReActLoop:
    loop = ReActLoop(llm_chat_func=None)
    loop.task_id = "task_worker"
    loop.task_orchestrator = SimpleNamespace(
        get_task=lambda task_id: {
            "task_id": task_id,
            "metadata": {"kind": "mission"},
        }
    )
    loop._original_query = (
        "⚙️ CODE PAR DÉLÉGATION : délègue au CodeAgent via delegate_task avant "
        "tout repli manuel."
    )
    loop._mission_allowed_files_meta = lambda: ["app.py"]
    return loop


def test_contract_code_worker_must_attempt_codeagent_before_direct_mutation():
    loop = _contract_code_worker()

    blocked = loop._worker_codeagent_first_gate("edit_file")

    assert blocked is not None
    assert blocked.success is False
    assert "delegate_task" in blocked.content
    assert loop._worker_codeagent_first_gate("read_file") is None
    assert loop._worker_codeagent_first_gate("delegate_task") is None


def test_contract_code_worker_can_fallback_after_real_codeagent_failure():
    loop = _contract_code_worker()
    loop.execution_ledger.append(
        iteration=1,
        action="delegate_task",
        target="app.py",
        success=False,
        proof="CodeAgent indisponible",
    )

    assert loop._worker_codeagent_first_gate("edit_file") is None


def test_codeagent_first_gate_is_inert_outside_scoped_contract_code_worker():
    chat = _contract_code_worker()
    chat.task_id = None
    assert chat._worker_codeagent_first_gate("edit_file") is None

    lead = _contract_code_worker()
    lead._mission_allowed_files_meta = lambda: []
    assert lead._worker_codeagent_first_gate("edit_file") is None

    document_worker = _contract_code_worker()
    document_worker._mission_allowed_files_meta = lambda: ["README.md"]
    assert document_worker._worker_codeagent_first_gate("edit_file") is None

    legacy_worker = _contract_code_worker()
    legacy_worker._original_query = "ancien worker sans marqueur"
    assert legacy_worker._worker_codeagent_first_gate("edit_file") is None
