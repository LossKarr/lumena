"""
🧪 Tests — AgentExecutionState (V1)

Couvre :
- LoopGuards, RepairTracking, CategoryBudget, RunMeta dataclasses
- RunMetaProxy (dict compat)
- AgentExecutionState reset() et snapshot()
- Intégration dans ReActLoop (exec_state présent, aliases fonctionnels)
"""

import pytest

from src.reasoning.agent_execution_state import (
    LoopGuards,
    RepairTracking,
    CategoryBudget,
    RunMeta,
    RunMetaProxy,
    AgentExecutionState,
)


# ── LoopGuards ───────────────────────────────────────────────────────────────

class TestLoopGuards:
    def test_defaults(self):
        g = LoopGuards()
        assert g.consecutive_same_action == 0
        assert g.stagnation_streak == 0
        assert g.browser_blind_streak == 0
        assert g.has_done_edits is False
        assert g.previous_thoughts == []
        assert g.listed_dirs == set()

    def test_mutable_containers(self):
        g = LoopGuards()
        g.previous_thoughts.append("thought1")
        g.listed_dirs.add("/tmp")
        g.read_file_path_counter["a.py"] = 3
        assert len(g.previous_thoughts) == 1
        assert "/tmp" in g.listed_dirs
        assert g.read_file_path_counter["a.py"] == 3


# ── RepairTracking ───────────────────────────────────────────────────────────

class TestRepairTracking:
    def test_defaults(self):
        r = RepairTracking()
        assert r.final_repair_attempts == 0
        assert r.thought_leak_repairs == 0
        assert r.ledger_final_guard_used is False
        assert r.pre_repair_answer is None
        assert r.after_delegate_success is False

    def test_increment(self):
        r = RepairTracking()
        r.final_repair_attempts += 1
        r.thought_leak_repairs += 1
        assert r.final_repair_attempts == 1
        assert r.thought_leak_repairs == 1


# ── CategoryBudget ───────────────────────────────────────────────────────────

class TestCategoryBudget:
    def test_defaults(self):
        b = CategoryBudget()
        assert b.iter_counts == {}

    def test_increment(self):
        b = CategoryBudget()
        b.iter_counts["web"] = b.iter_counts.get("web", 0) + 1
        assert b.iter_counts["web"] == 1


# ── RunMeta ──────────────────────────────────────────────────────────────────

class TestRunMeta:
    def test_defaults(self):
        rm = RunMeta()
        assert rm.agent_output_incomplete is False
        assert rm.agent_output_warning is None
        assert rm.agent_repair_attempts == 0
        assert rm.agent_final_finish_reason is None

    def test_to_dict(self):
        rm = RunMeta(agent_output_incomplete=True, agent_output_warning="test")
        d = rm.to_dict()
        assert d["agent_output_incomplete"] is True
        assert d["agent_output_warning"] == "test"
        assert d["agent_repair_attempts"] == 0
        assert d["agent_final_finish_reason"] is None

    def test_fields_tuple(self):
        assert "agent_output_incomplete" in RunMeta._FIELDS
        # Lot RF-8-FIX-2 (2026-08-28) : 4 -> 10 champs. Six cles etaient
        # ECRITES par le code sans etre declarees ; `RunMetaProxy` levait
        # `KeyError` et les `except` avalaient la perte — 9 ecritures sur
        # 24. La plus grave, `mission_truth_lock_overclaim`, laissait une
        # mission au FINAL retrograde se cloturer `completed`.
        assert len(RunMeta._FIELDS) == 10


# ── RunMetaProxy ─────────────────────────────────────────────────────────────

class TestRunMetaProxy:
    def test_getitem(self):
        rm = RunMeta(agent_output_incomplete=True)
        proxy = RunMetaProxy(rm)
        assert proxy["agent_output_incomplete"] is True

    def test_setitem(self):
        rm = RunMeta()
        proxy = RunMetaProxy(rm)
        proxy["agent_output_incomplete"] = True
        assert rm.agent_output_incomplete is True

    def test_getitem_unknown_key(self):
        proxy = RunMetaProxy(RunMeta())
        with pytest.raises(KeyError):
            _ = proxy["nonexistent"]

    def test_setitem_unknown_key(self):
        proxy = RunMetaProxy(RunMeta())
        with pytest.raises(KeyError):
            proxy["nonexistent"] = True

    def test_contains(self):
        proxy = RunMetaProxy(RunMeta())
        assert "agent_output_incomplete" in proxy
        assert "nonexistent" not in proxy

    def test_get_with_default(self):
        proxy = RunMetaProxy(RunMeta())
        assert proxy.get("agent_output_incomplete") is False
        assert proxy.get("nonexistent", "default") == "default"

    def test_dict_conversion(self):
        rm = RunMeta(agent_output_warning="test_warning")
        proxy = RunMetaProxy(rm)
        d = dict(proxy)
        assert d["agent_output_warning"] == "test_warning"
        assert len(d) == 10  # RF-8-FIX-2 : +6 champs

    def test_len(self):
        proxy = RunMetaProxy(RunMeta())
        assert len(proxy) == 10  # RF-8-FIX-2 : +6 champs

    def test_iter(self):
        proxy = RunMetaProxy(RunMeta())
        keys = list(proxy)
        assert "agent_output_incomplete" in keys
        assert len(keys) == 10  # RF-8-FIX-2 : +6 champs

    def test_items(self):
        rm = RunMeta(agent_repair_attempts=3)
        proxy = RunMetaProxy(rm)
        items = dict(proxy.items())
        assert items["agent_repair_attempts"] == 3

    def test_write_through(self):
        """Verify that proxy writes go through to the underlying RunMeta."""
        rm = RunMeta()
        proxy = RunMetaProxy(rm)
        proxy["agent_output_incomplete"] = True
        proxy["agent_output_warning"] = "loop_detected"
        proxy["agent_repair_attempts"] = 2
        proxy["agent_final_finish_reason"] = "length"
        assert rm.agent_output_incomplete is True
        assert rm.agent_output_warning == "loop_detected"
        assert rm.agent_repair_attempts == 2
        assert rm.agent_final_finish_reason == "length"


# ── AgentExecutionState ──────────────────────────────────────────────────────

class TestAgentExecutionState:
    def test_defaults(self):
        s = AgentExecutionState()
        assert s.guards.consecutive_same_action == 0
        assert s.repairs.final_repair_attempts == 0
        assert s.budget.iter_counts == {}
        assert s.run_meta.agent_output_incomplete is False
        assert s.all_session_tools == set()
        assert s.last_llm_meta == {}

    def test_reset(self):
        s = AgentExecutionState()
        s.guards.consecutive_same_action = 5
        s.guards.stagnation_streak = 3
        s.repairs.final_repair_attempts = 2
        s.repairs.ledger_final_guard_used = True
        s.budget.iter_counts["web"] = 4
        s.run_meta.agent_output_incomplete = True
        s.all_session_tools.add("write_file")
        s.last_llm_meta["finish_reason"] = "stop"

        s.reset()

        assert s.guards.consecutive_same_action == 0
        assert s.guards.stagnation_streak == 0
        assert s.repairs.final_repair_attempts == 0
        assert s.repairs.ledger_final_guard_used is False
        assert s.budget.iter_counts == {}
        assert s.run_meta.agent_output_incomplete is False
        assert s.last_llm_meta == {}
        # all_session_tools survives reset (by design)
        assert "write_file" in s.all_session_tools

    def test_snapshot(self):
        s = AgentExecutionState()
        s.guards.consecutive_same_action = 2
        s.repairs.thought_leak_repairs = 1
        s.budget.iter_counts["browser"] = 3
        s.all_session_tools.add("read_file")
        snap = s.snapshot()

        assert snap["guards"]["consecutive_same_action"] == 2
        assert snap["repairs"]["thought_leak_repairs"] == 1
        assert snap["budget"]["iter_counts"] == {"browser": 3}
        assert "read_file" in snap["all_session_tools"]
        assert isinstance(snap["run_meta"], dict)

    def test_snapshot_is_serializable(self):
        import json
        s = AgentExecutionState()
        s.all_session_tools.add("write_file")
        snap = s.snapshot()
        # Must not raise
        json.dumps(snap, default=str)


# ── ReActLoop Integration ────────────────────────────────────────────────────

class TestReActLoopExecStateIntegration:
    def test_has_exec_state(self):
        from src.reasoning.react import ReActLoop
        loop = ReActLoop()
        assert hasattr(loop, 'exec_state')
        assert isinstance(loop.exec_state, AgentExecutionState)

    def test_alias_consecutive_same_action(self):
        from src.reasoning.react import ReActLoop
        loop = ReActLoop()
        loop._consecutive_same_action = 5
        assert loop.exec_state.guards.consecutive_same_action == 5
        assert loop._consecutive_same_action == 5

    def test_alias_final_repair_attempts(self):
        from src.reasoning.react import ReActLoop
        loop = ReActLoop()
        loop._final_repair_attempts = 3
        assert loop.exec_state.repairs.final_repair_attempts == 3

    def test_alias_run_meta_write_through(self):
        from src.reasoning.react import ReActLoop
        loop = ReActLoop()
        loop._run_meta["agent_output_incomplete"] = True
        assert loop.exec_state.run_meta.agent_output_incomplete is True

    def test_alias_run_meta_setter(self):
        from src.reasoning.react import ReActLoop
        loop = ReActLoop()
        loop._run_meta = {
            "agent_output_incomplete": True,
            "agent_output_warning": "test",
            "agent_repair_attempts": 1,
            "agent_final_finish_reason": "length",
        }
        assert loop.exec_state.run_meta.agent_output_incomplete is True
        assert loop.exec_state.run_meta.agent_output_warning == "test"

    def test_alias_category_iter_counts(self):
        from src.reasoning.react import ReActLoop
        loop = ReActLoop()
        loop._category_iter_counts["web"] = 3
        assert loop.exec_state.budget.iter_counts["web"] == 3

    def test_alias_all_session_tools(self):
        from src.reasoning.react import ReActLoop
        loop = ReActLoop()
        loop._all_session_tools.add("write_file")
        assert "write_file" in loop.exec_state.all_session_tools

    def test_get_run_meta_compat(self):
        from src.reasoning.react import ReActLoop
        loop = ReActLoop()
        loop._run_meta["agent_output_warning"] = "test_compat"
        meta = loop.get_run_meta()
        assert meta["agent_output_warning"] == "test_compat"

    def test_object_new_lazy_init(self):
        """Tests that bypass __init__ via object.__new__ still work."""
        from src.reasoning.react import ReActLoop
        loop = object.__new__(ReActLoop)
        # Should not raise — _ensure_exec_state kicks in
        loop._consecutive_same_action = 10
        assert loop._consecutive_same_action == 10
        loop._run_meta = {
            "agent_output_incomplete": False,
            "agent_output_warning": None,
            "agent_repair_attempts": 0,
            "agent_final_finish_reason": None,
        }
        assert loop._run_meta["agent_output_incomplete"] is False

    def test_snapshot_accessible(self):
        from src.reasoning.react import ReActLoop
        loop = ReActLoop()
        loop._consecutive_same_action = 2
        loop._final_repair_attempts = 1
        snap = loop.exec_state.snapshot()
        assert snap["guards"]["consecutive_same_action"] == 2
        assert snap["repairs"]["final_repair_attempts"] == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
