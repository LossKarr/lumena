"""M106 - proof-driven mission closure and durable terminal facts."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.reasoning.handlers import missions as mission_handlers
from src.reasoning.plan_progress import (
    correction_task_blocks_readonly,
    mission_evidence_finalizable,
    pytest_plan_task_proven,
    worker_evidence_finalizable,
)
from src.reasoning.react import (
    ReActLoop,
    TaskItem,
    _advance_manual_browser_flow,
    _browser_state_fingerprint,
    _manual_browser_flow_proves_interaction,
)
from src.runtime.execution_ledger import ExecutionLedger
from src.runtime.task_orchestrator import TaskOrchestrator
from src.subagents.mission_budget import deadline_hard_net_fires
from src.subagents.mission_contract import (
    generate_stub,
    inspect_worker_deliverables,
)
from src.tools.file_guardrails import WorkspaceFileGuardrails


def test_partial_artifact_still_cannot_disarm_deadline_but_completion_can():
    assert deadline_hard_net_fires(
        steered=True,
        remaining_s=-180,
        grace_s=120,
        artifact_written=True,
    )
    assert not deadline_hard_net_fires(
        steered=True,
        remaining_s=-180,
        grace_s=120,
        artifact_written=False,
        completion_proven=True,
    )


def test_multi_file_evidence_closes_despite_stale_administrative_plan_ticks():
    plan = [
        TaskItem(description="Recherche sourcée", completed=False),
        TaskItem(description="Fusionner les résultats", completed=False),
        TaskItem(description="Publier le livrable", completed=False),
    ]
    assert mission_evidence_finalizable(
        plan,
        delivery_proven=True,
        delegation_complete=True,
        tests_required=True,
        tests_green=True,
        browser_required=True,
        browser_proven=True,
    )


def test_unresolved_external_side_effect_still_blocks_evidence_closure():
    plan = [TaskItem(description="Envoyer le rapport par email", completed=False)]
    assert not mission_evidence_finalizable(
        plan,
        delivery_proven=True,
        delegation_complete=True,
        tests_required=False,
        tests_green=False,
        browser_required=False,
        browser_proven=True,
    )


def test_worker_evidence_closes_a_stale_plan_only_after_owned_tests_are_green():
    stale_plan = [
        TaskItem(description="Remplir tests/test_calculator.py", completed=False),
        TaskItem(description="Lancer pytest", completed=False),
    ]
    assert worker_evidence_finalizable(
        stale_plan,
        assigned_files_ready=True,
        tests_required=True,
        tests_green=True,
    )
    assert not worker_evidence_finalizable(
        stale_plan,
        assigned_files_ready=True,
        tests_required=True,
        tests_green=False,
    )


def test_worker_evidence_never_waives_an_external_side_effect():
    plan = [TaskItem(description="Envoyer le resultat par email", completed=False)]
    assert not worker_evidence_finalizable(
        plan,
        assigned_files_ready=True,
        tests_required=False,
        tests_green=False,
    )


def test_reading_a_contract_stub_never_completes_a_fill_task():
    assert correction_task_blocks_readonly("read_file", "Remplir README.md")
    assert correction_task_blocks_readonly("read_files_batch", "Implementer calculator.py")
    assert not correction_task_blocks_readonly("edit_file", "Remplir README.md")


def test_parsed_pytest_outcome_credits_only_a_real_execution_task():
    green = {
        "is_test_cmd": True,
        "ran_something": True,
        "green": True,
        "passed": 4,
    }
    assert pytest_plan_task_proven("Lancer pytest pour verifier", "run_command", green)
    assert pytest_plan_task_proven("Relancer pytest jusqu'au vert", "run_command", green)
    assert not pytest_plan_task_proven(
        "Relancer pytest jusqu'au vert",
        "run_command",
        {**green, "green": False},
    )
    assert not pytest_plan_task_proven(
        "Remplir tests/test_calculator.py", "run_command", green
    )


def test_worker_deliverable_inspection_rejects_exact_stub_and_missing_file(tmp_path):
    contract = {
        "project": "PulseMeter",
        "files": [
            {
                "path": "calculator.py",
                "owner": "worker",
                "desc": "Calculator",
                "exports": ["def compute_total(a: float, b: float) -> float"],
            },
            {
                "path": "tests/test_calculator.py",
                "owner": "worker",
                "desc": "Tests",
                "exports": [],
            },
        ],
    }
    (tmp_path / "contract.json").write_text(
        __import__("json").dumps(contract), encoding="utf-8"
    )
    (tmp_path / "calculator.py").write_text(
        generate_stub(contract["files"][0], contract["files"], "PulseMeter"),
        encoding="utf-8",
    )

    state = inspect_worker_deliverables(
        tmp_path, ["calculator.py", "tests/test_calculator.py"]
    )

    assert state["ready"] is False
    assert state["stubs"] == ["calculator.py"]
    assert state["missing"] == ["tests/test_calculator.py"]


def test_worker_completion_snapshot_uses_disk_and_persisted_green_test(
    tmp_path, monkeypatch
):
    import src.utils.paths as paths_module

    monkeypatch.setattr(paths_module, "WORKSPACE_DIR", tmp_path)
    mission_dir = tmp_path / "missions" / "lead"
    (mission_dir / "tests").mkdir(parents=True)
    contract = {
        "project": "PulseMeter",
        "files": [
            {
                "path": "calculator.py",
                "owner": "worker",
                "desc": "Calculator",
                "exports": ["def compute_total(a: float, b: float) -> float"],
            },
            {
                "path": "tests/test_calculator.py",
                "owner": "worker",
                "desc": "Tests",
                "exports": [],
            },
        ],
    }
    (mission_dir / "contract.json").write_text(
        __import__("json").dumps(contract), encoding="utf-8"
    )
    (mission_dir / "calculator.py").write_text(
        "def compute_total(a: float, b: float) -> float:\n    return round(a * b, 2)\n",
        encoding="utf-8",
    )
    (mission_dir / "tests" / "test_calculator.py").write_text(
        "def test_total():\n    assert 2 * 3 == 6\n", encoding="utf-8"
    )

    loop = ReActLoop.__new__(ReActLoop)
    loop.task_id = "worker"
    loop._task_plan = [
        TaskItem(description="Remplir tests/test_calculator.py", completed=False),
        TaskItem(description="Lancer pytest", completed=False),
    ]
    loop._orchestrator_enabled = lambda: True
    loop._mission_allowed_files_meta = lambda: [
        "calculator.py",
        "tests/test_calculator.py",
    ]
    loop.task_orchestrator = SimpleNamespace(
        get_task=lambda task_id: {
            "task_id": task_id,
            "metadata": {
                "kind": "mission",
                "mission_workspace": "missions/lead",
                "allowed_files": ["calculator.py", "tests/test_calculator.py"],
                "last_test_outcome": {
                    "is_test_cmd": True,
                    "green": True,
                    "passed": 4,
                    "failed": 0,
                    "errors": 0,
                },
            },
        }
    )
    loop.execution_ledger = ExecutionLedger()

    facts = loop._mission_completion_evidence()

    assert facts["scope"] == "worker"
    assert facts["complete"] is True
    assert facts["delivery_proven"] is True
    assert facts["tests_required"] is True
    assert facts["tests_green"] is True


def test_manual_browser_proof_requires_read_action_and_changed_followup_dom():
    before = _browser_state_fingerprint("Distance 0 | Resultat vide")
    assert not _manual_browser_flow_proves_interaction(
        before, mutation_seen=False, current_observation="Distance 25 | Resultat 1000"
    )
    assert not _manual_browser_flow_proves_interaction(
        before, mutation_seen=True, current_observation="Distance 0 | Resultat vide"
    )
    assert _manual_browser_flow_proves_interaction(
        before, mutation_seen=True, current_observation="Distance 25 | Resultat 1000"
    )


def test_manual_browser_mutation_survives_an_unchanged_first_read():
    before = _browser_state_fingerprint("Resultat vide")
    proven, fingerprint, pending = _advance_manual_browser_flow(
        before,
        mutation_pending=False,
        tool_name="browser_click_index",
        observation="Clic effectue",
    )
    assert proven is False
    assert fingerprint == before
    assert pending is True

    proven, fingerprint, pending = _advance_manual_browser_flow(
        fingerprint,
        mutation_pending=pending,
        tool_name="browser_dom_state",
        observation="Resultat vide",
    )
    assert proven is False
    assert pending is True

    proven, fingerprint, pending = _advance_manual_browser_flow(
        fingerprint,
        mutation_pending=pending,
        tool_name="browser_get_content",
        observation="Resultat : 87.50 EUR",
    )
    assert proven is True
    assert pending is False


def test_manual_browser_proof_rejects_query_only_form_reload():
    before = _browser_state_fingerprint(
        "Page: FocusForge\n"
        "URL: http://localhost:8081/\n"
        "[1] button Evaluer\n"
        "Form state: filled=0, checked=0, disabled_buttons=0, "
        "enabled_submit_buttons=0, controls=3"
    )
    after = (
        "Page: FocusForge\n"
        "URL: http://localhost:8081/?impact=8&effort=4\n"
        "[1] button Evaluer\n"
        "Form state: filled=0, checked=0, disabled_buttons=0, "
        "enabled_submit_buttons=0, controls=3"
    )

    assert not _manual_browser_flow_proves_interaction(
        before, mutation_seen=True, current_observation=after
    )


def test_browser_interactions_reuse_the_last_observed_page_url():
    source = __import__("inspect").getsource(ReActLoop._run_internal)
    assert "self._last_browser_page_url = _page_url" in source
    assert 'getattr(self, "_last_browser_page_url", "")' in source


def test_completion_snapshot_accepts_persisted_publication_test_and_dom_proof():
    loop = ReActLoop.__new__(ReActLoop)
    loop.task_id = "lead"
    loop._task_plan = [TaskItem(description="Fusionner les résultats", completed=False)]
    loop._mission_allowed_files_meta = lambda: []
    loop._mission_tests_present_for_gate = lambda: "tests dans la mission"
    loop._truth_lock_web_flag = lambda: True
    loop._truth_lock_interaction_flag = lambda: True
    loop._truth_lock_game_flag = lambda: False
    loop._truth_lock_interaction_proven = lambda: True
    loop.task_orchestrator = SimpleNamespace(
        get_task=lambda task_id: {
            "task_id": task_id,
            "metadata": {
                "kind": "mission",
                "mission_published": True,
                "children": ["w1", "w2", "w3"],
                "last_delegate_progress": {
                    "done": 3,
                    "total": 3,
                    "failed": 0,
                    "timed_out": False,
                    "cancelled": False,
                },
                "last_test_outcome": {
                    "is_test_cmd": True,
                    "green": True,
                    "passed": 8,
                },
            },
        }
    )
    loop.execution_ledger = ExecutionLedger()

    facts = loop._mission_completion_evidence()

    assert facts["complete"] is True
    assert facts["delivery_proven"] is True
    assert facts["tests_green"] is True
    assert facts["browser_proven"] is True


def _mission_ctx(tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "tasks.json"))
    core = SimpleNamespace(task_orchestrator=orch)
    ctx = SimpleNamespace(
        lumena=core,
        runtime_task_id=None,
        file_guardrails=WorkspaceFileGuardrails(tmp_path),
    )
    return ctx, orch


@pytest.mark.asyncio
async def test_publish_persists_authoritative_delivery_facts(tmp_path):
    ctx, orch = _mission_ctx(tmp_path)
    lead = orch.start_task(
        conversation_id="__missions__",
        channel="mission",
        message_preview="lead",
        metadata={"kind": "mission", "depth": 1},
    )
    ctx.runtime_task_id = lead.task_id
    mission_dir = tmp_path / "missions" / lead.task_id
    mission_dir.mkdir(parents=True)
    (mission_dir / "result.txt").write_text("done", encoding="utf-8")

    result = await mission_handlers.publish_mission_workspace_handler(
        ctx, target="proof_project"
    )

    assert result.success
    meta = orch.get_task(lead.task_id)["metadata"]
    assert meta["mission_published"] is True
    assert meta["published_workspace"] == "workspace/proof_project"
    assert meta["published_files"] == ["result.txt"]
    assert meta["published_at"]


def test_terminal_facts_expose_deadline_reason_and_persisted_proofs():
    task = {
        "state": "cancelled",
        "created_at": "2026-08-08T20:49:48+00:00",
        "updated_at": "2026-08-08T21:42:16+00:00",
        "metadata": {
            "deadline_expired": True,
            "mission_published": True,
            "published_workspace": "workspace/terrafleet_v1_final",
            "last_test_outcome": {
                "is_test_cmd": True,
                "green": True,
                "passed": 8,
            },
            "tests_green": True,
            "browser_interaction_verified": True,
            "last_delegate_progress": {"done": 3, "total": 3},
        },
    }

    facts = mission_handlers.mission_terminal_facts(task)
    rendered = mission_handlers._mission_facts_text(task)

    assert facts["reason_code"] == "deadline_expired"
    assert facts["published"] is True
    assert facts["tests_green"] is True
    assert "cause=deadline_expired" in rendered
    assert "publie=workspace/terrafleet_v1_final" in rendered
    assert "tests=verts (8 passed)" in rendered
    assert "workers=3/3" in rendered


def test_latency_marker_is_present_at_the_iteration_boundary():
    source = __import__("pathlib").Path(
        "src/reasoning/react.py"
    ).read_text(encoding="utf-8")
    assert "[REACT LATENCY] post_observation_to_next_iteration_s=" in source


def test_provider_finish_reason_error_can_never_become_a_model_final():
    detail = ReActLoop._llm_provider_error_detail(
        "[Erreur] Client error '402 Payment Required'",
        {"finish_reason": "error", "provider_used": "deepseek"},
    )
    assert detail == "[Erreur] Client error '402 Payment Required'"
    assert ReActLoop._llm_provider_error_detail(
        "Une reponse normale qui parle d'une erreur.",
        {"finish_reason": "stop", "provider_used": "deepseek"},
    ) is None
    run_source = __import__("inspect").getsource(ReActLoop._run_internal)
    assert "_llm_provider_error_detail(" in run_source
    assert 'raise RuntimeError(f"llm_provider_error:' in run_source


def test_orchestrator_adds_terminal_reason_to_every_mission_transition(tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "tasks.json"))
    done = orch.start_task(
        conversation_id="__missions__",
        channel="mission",
        message_preview="done",
        metadata={"kind": "mission"},
    )
    failed = orch.start_task(
        conversation_id="__missions__",
        channel="mission",
        message_preview="failed",
        metadata={"kind": "mission"},
    )
    cancelled = orch.start_task(
        conversation_id="__missions__",
        channel="mission",
        message_preview="cancelled",
        metadata={"kind": "mission"},
    )

    orch.mark_done(done.task_id, result_summary="ok")
    orch.mark_failed(failed.task_id, "provider unavailable")
    orch.cancel_task(cancelled.task_id)

    assert orch.get_task(done.task_id)["metadata"]["terminal_reason_code"] == "completed"
    assert orch.get_task(failed.task_id)["metadata"]["terminal_reason_code"] == "failed"
    assert "provider unavailable" in orch.get_task(failed.task_id)["metadata"]["terminal_reason_detail"]
    assert orch.get_task(cancelled.task_id)["metadata"]["terminal_reason_code"] == "cancelled"
    assert all(
        orch.get_task(task_id)["metadata"].get("terminal_at")
        for task_id in (done.task_id, failed.task_id, cancelled.task_id)
    )


def test_orchestrator_records_parent_cancel_for_mission_children(tmp_path):
    orch = TaskOrchestrator(persistence_path=str(tmp_path / "tasks.json"))
    parent = orch.start_task(
        conversation_id="__missions__",
        channel="mission",
        message_preview="parent",
        metadata={"kind": "mission"},
    )
    child = orch.start_task(
        conversation_id="__missions__",
        channel="mission",
        message_preview="child",
        metadata={"kind": "mission", "parent_id": parent.task_id},
    )

    orch.cancel_task(parent.task_id, propagate=True)

    assert orch.get_task(parent.task_id)["metadata"]["terminal_reason_code"] == "cancelled"
    assert orch.get_task(child.task_id)["metadata"]["terminal_reason_code"] == "parent_cancelled"
