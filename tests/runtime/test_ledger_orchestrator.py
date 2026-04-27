"""
🧪 Tests — ExecutionLedger → TaskOrchestrator projection (V1)

Couvre :
- ExecutionLedger.checkpoint_projection() structure et contenu
- TaskOrchestrator.enrich_checkpoint() merge correct
- Intégration : ReActLoop._mark_task_checkpoint enrichit via le ledger
- Intégration : ReActLoop._mark_task_done émet un checkpoint final enrichi
- Fallback : pas de ledger → checkpoint non enrichi
"""

import pytest

from src.runtime.execution_ledger import ExecutionLedger
from src.runtime.task_orchestrator import TaskOrchestrator


# ── ExecutionLedger.checkpoint_projection ────────────────────────────────────

class TestCheckpointProjection:
    def test_empty_ledger(self):
        ledger = ExecutionLedger()
        proj = ledger.checkpoint_projection()
        assert proj["total_actions"] == 0
        assert proj["successful_mutations"] == 0
        assert proj["success_rate"] == 0.0
        assert proj["recent"] == []

    def test_single_action(self):
        ledger = ExecutionLedger()
        ledger.append(iteration=1, action="read_file", success=True)
        proj = ledger.checkpoint_projection()
        assert proj["total_actions"] == 1
        assert proj["successful_mutations"] == 0
        assert proj["success_rate"] == 1.0
        assert len(proj["recent"]) == 1
        assert proj["recent"][0]["action"] == "read_file"
        assert proj["recent"][0]["success"] is True
        assert proj["recent"][0]["iteration"] == 1

    def test_mutations_counted(self):
        ledger = ExecutionLedger()
        ledger.append(iteration=1, action="write_file", target="a.py", success=True)
        ledger.append(iteration=2, action="read_file", success=True)
        ledger.append(iteration=3, action="edit_file", target="b.py", success=False)
        proj = ledger.checkpoint_projection()
        assert proj["total_actions"] == 3
        assert proj["successful_mutations"] == 1  # only write_file succeeded
        assert proj["success_rate"] == round(2 / 3, 2)

    def test_max_recent_limits_output(self):
        ledger = ExecutionLedger()
        for i in range(10):
            ledger.append(iteration=i, action=f"tool_{i}", success=True)
        proj = ledger.checkpoint_projection(max_recent=3)
        assert len(proj["recent"]) == 3
        assert proj["recent"][0]["action"] == "tool_7"
        assert proj["recent"][2]["action"] == "tool_9"

    def test_target_propagated(self):
        ledger = ExecutionLedger()
        ledger.append(iteration=1, action="write_file", target="/tmp/x.py", success=True)
        proj = ledger.checkpoint_projection()
        assert proj["recent"][0]["target"] == "/tmp/x.py"

    def test_projection_is_serializable(self):
        import json
        ledger = ExecutionLedger()
        ledger.append(iteration=1, action="write_file", target="a.py", success=True, proof="ok")
        proj = ledger.checkpoint_projection()
        json.dumps(proj)  # must not raise


# ── TaskOrchestrator.enrich_checkpoint ───────────────────────────────────────

class TestEnrichCheckpoint:
    def test_no_projection(self):
        payload = {"phase": "iteration", "iteration": 3}
        result = TaskOrchestrator.enrich_checkpoint(payload, None)
        assert result == payload
        assert "ledger" not in result

    def test_empty_projection(self):
        payload = {"phase": "iteration"}
        result = TaskOrchestrator.enrich_checkpoint(payload, {})
        assert "ledger" not in result

    def test_with_projection(self):
        payload = {"phase": "iteration", "iteration": 3}
        proj = {"total_actions": 5, "successful_mutations": 2, "success_rate": 0.8, "recent": []}
        result = TaskOrchestrator.enrich_checkpoint(payload, proj)
        assert result["phase"] == "iteration"
        assert result["ledger"] == proj
        assert result["ledger"]["total_actions"] == 5

    def test_does_not_mutate_original(self):
        payload = {"phase": "start"}
        proj = {"total_actions": 1, "recent": []}
        result = TaskOrchestrator.enrich_checkpoint(payload, proj)
        assert "ledger" not in payload  # original untouched
        assert "ledger" in result


# ── Integration: enriched checkpoints in TaskOrchestrator ────────────────────

class TestEnrichedCheckpointLifecycle:
    def test_enriched_checkpoint_stored(self):
        orch = TaskOrchestrator()
        task = orch.start_task(
            conversation_id="conv_1",
            channel="ide",
            message_preview="test",
        )
        orch.mark_running(task.task_id)

        ledger = ExecutionLedger()
        ledger.append(iteration=1, action="write_file", target="a.py", success=True, proof="ok")
        ledger.append(iteration=2, action="read_file", success=True)

        enriched = TaskOrchestrator.enrich_checkpoint(
            {"phase": "iteration", "iteration": 2},
            ledger.checkpoint_projection(),
        )
        orch.mark_checkpoint(task.task_id, enriched)

        data = orch.get_task(task.task_id)
        assert data is not None
        cp = data["last_checkpoint"]
        assert cp["phase"] == "iteration"
        assert "ledger" in cp
        assert cp["ledger"]["total_actions"] == 2
        assert cp["ledger"]["successful_mutations"] == 1
        assert len(cp["ledger"]["recent"]) == 2

    def test_non_enriched_checkpoint_still_works(self):
        orch = TaskOrchestrator()
        task = orch.start_task(
            conversation_id="conv_2",
            channel="web",
            message_preview="plain",
        )
        orch.mark_checkpoint(task.task_id, {"phase": "iteration", "iteration": 1})

        data = orch.get_task(task.task_id)
        cp = data["last_checkpoint"]
        assert cp["phase"] == "iteration"
        assert "ledger" not in cp

    def test_enriched_checkpoint_persisted(self, tmp_path):
        state_path = tmp_path / "enriched.json"
        orch = TaskOrchestrator(persistence_path=state_path)
        task = orch.start_task(
            conversation_id="conv_persist",
            channel="ide",
            message_preview="persist enriched",
        )
        ledger = ExecutionLedger()
        ledger.append(iteration=1, action="edit_file", target="x.py", success=True)
        enriched = TaskOrchestrator.enrich_checkpoint(
            {"phase": "done"},
            ledger.checkpoint_projection(),
        )
        orch.mark_checkpoint(task.task_id, enriched)
        orch.mark_done(task.task_id, result_summary="ok")

        # Restore from disk
        restored = TaskOrchestrator(persistence_path=state_path)
        data = restored.get_task(task.task_id)
        assert data is not None
        assert data["state"] == "done"
        cp = data["last_checkpoint"]
        assert cp["ledger"]["total_actions"] == 1
        assert cp["ledger"]["successful_mutations"] == 1


# ── Integration: ReActLoop wiring ────────────────────────────────────────────

class TestReActLoopLedgerCheckpointWiring:
    def test_mark_task_checkpoint_enriches_when_ledger_has_entries(self):
        from src.reasoning.react import ReActLoop

        orch = TaskOrchestrator()
        task = orch.start_task(
            conversation_id="conv_react",
            channel="ide",
            message_preview="test",
        )

        loop = ReActLoop(task_orchestrator=orch, task_id=task.task_id)
        orch.mark_running(task.task_id)

        # Populate the ledger
        loop.execution_ledger.append(iteration=1, action="write_file", target="a.py", success=True)

        # Trigger a checkpoint
        loop._mark_task_checkpoint({"phase": "iteration", "iteration": 1})

        data = orch.get_task(task.task_id)
        cp = data["last_checkpoint"]
        assert "ledger" in cp
        assert cp["ledger"]["total_actions"] == 1

    def test_mark_task_checkpoint_no_ledger_entries(self):
        from src.reasoning.react import ReActLoop

        orch = TaskOrchestrator()
        task = orch.start_task(
            conversation_id="conv_react2",
            channel="ide",
            message_preview="test2",
        )

        loop = ReActLoop(task_orchestrator=orch, task_id=task.task_id)
        orch.mark_running(task.task_id)

        # Empty ledger → no enrichment
        loop._mark_task_checkpoint({"phase": "start"})

        data = orch.get_task(task.task_id)
        cp = data["last_checkpoint"]
        assert "ledger" not in cp

    def test_mark_task_done_emits_final_enriched_checkpoint(self):
        from src.reasoning.react import ReActLoop

        orch = TaskOrchestrator()
        task = orch.start_task(
            conversation_id="conv_done",
            channel="ide",
            message_preview="done test",
        )

        loop = ReActLoop(task_orchestrator=orch, task_id=task.task_id)
        orch.mark_running(task.task_id)

        loop.execution_ledger.append(iteration=1, action="write_file", target="f.py", success=True)
        loop.execution_ledger.append(iteration=2, action="edit_file", target="f.py", success=True)

        loop._mark_task_done("task completed")

        data = orch.get_task(task.task_id)
        assert data["state"] == "done"
        assert data["result_summary"] == "task completed"

        # The last checkpoint should be the enriched "done" checkpoint
        cp = data["last_checkpoint"]
        assert cp["phase"] == "done"
        assert "ledger" in cp
        assert cp["ledger"]["total_actions"] == 2
        assert cp["ledger"]["successful_mutations"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
