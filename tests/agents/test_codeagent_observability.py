"""Tests pour grep 0-result tracking + metrics observability (World Model / Reflexion)."""
from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch

from src.agents.sub_agent import CodeAgent


def _make_agent() -> CodeAgent:
    agent = CodeAgent.__new__(CodeAgent)
    agent._task_workspace_root = None
    agent._session_memory = {
        "files_read": {},
        "errors_seen": [],
        "edits_done": [],
        "grep_zero_results": {},
    }
    agent._session_memory_last_used = 0.0
    agent._SESSION_MEMORY_TTL = 4 * 3600
    agent._reflexion_generated_count = 0
    agent._grep_zero_repeats = 0
    agent._applied_reflexion_ids = []
    return agent


# ── Grep zero-result tracking ───────────────────────────────────────────────


class TestGrepZeroResultTracking:
    def test_record_first_time_returns_1(self):
        agent = _make_agent()
        n = agent._record_grep_zero_result("foo", "src/")
        assert n == 1

    def test_record_repeated_increments(self):
        agent = _make_agent()
        agent._record_grep_zero_result("foo", "src/")
        agent._record_grep_zero_result("foo", "src/")
        n = agent._record_grep_zero_result("foo", "src/")
        assert n == 3

    def test_record_different_patterns_independent(self):
        agent = _make_agent()
        agent._record_grep_zero_result("foo", "src/")
        agent._record_grep_zero_result("bar", "src/")
        n = agent._record_grep_zero_result("foo", "src/")
        assert n == 2

    def test_record_different_paths_independent(self):
        agent = _make_agent()
        agent._record_grep_zero_result("foo", "src/")
        n = agent._record_grep_zero_result("foo", "tests/")
        assert n == 1

    def test_record_empty_pattern_returns_0(self):
        agent = _make_agent()
        n = agent._record_grep_zero_result("", "src/")
        assert n == 0

    def test_record_creates_key_if_missing(self):
        """Si session memory provient d'une ancienne version sans grep_zero_results."""
        agent = _make_agent()
        # Simule vieille session memory (clé manquante)
        del agent._session_memory["grep_zero_results"]
        n = agent._record_grep_zero_result("foo", "src/")
        assert n == 1
        assert "grep_zero_results" in agent._session_memory

    def test_default_path(self):
        agent = _make_agent()
        agent._record_grep_zero_result("foo", "")
        agent._record_grep_zero_result("foo", ".")
        # Les deux doivent aller sur la même clé (. par défaut)
        key = "foo|."
        assert agent._session_memory["grep_zero_results"][key] == 2


# ── Session memory init ─────────────────────────────────────────────────────


class TestSessionMemoryInit:
    def test_grep_zero_results_key_present_on_init(self):
        agent = CodeAgent()
        assert "grep_zero_results" in agent._session_memory

    def test_observability_counters_initialized(self):
        agent = CodeAgent()
        assert agent._reflexion_generated_count == 0
        assert agent._grep_zero_repeats == 0
        assert agent._applied_reflexion_ids == []

    def test_ttl_reset_preserves_grep_zero_key(self):
        agent = _make_agent()
        agent._record_grep_zero_result("foo", "src/")
        # Force expiration TTL
        import time
        agent._session_memory_last_used = time.time() - agent._SESSION_MEMORY_TTL - 10
        agent._refresh_session_memory()
        # Après reset, la clé doit exister (vide)
        assert "grep_zero_results" in agent._session_memory
        assert agent._session_memory["grep_zero_results"] == {}


# ── Metrics observability ───────────────────────────────────────────────────


class TestMetricsExtraFields:
    """Vérifie que _finalize_metrics propage les nouveaux champs dans extra."""

    def test_metrics_includes_reflexion_and_world_model_stats(self):
        agent = _make_agent()
        agent._reflexion_generated_count = 2
        agent._grep_zero_repeats = 1
        agent._applied_reflexion_ids = ["refl_a", "refl_b", "refl_c"]

        # Mock du résultat et du llm
        result = MagicMock()
        result.success = True
        result.status_code = "SUCCESS"
        result.meta = {"iterations": 7, "stuck": False}

        task = MagicMock()
        task.task_id = "task-123"
        llm = MagicMock()
        llm.model_name = "deepseek-v3"

        captured = {}

        def fake_record(**kwargs):
            captured.update(kwargs)

        with patch("src.utils.metrics.record_task_metrics", side_effect=fake_record), \
             patch("src.config.codeagent_flags.CODING_METRICS", True):
            agent._finalize_metrics(result, task, llm, 1000.0, 1)

        extra = captured.get("extra", {})
        assert extra.get("reflexions_applied") == 3
        assert extra.get("reflexions_generated") == 2
        assert extra.get("grep_zero_repeats") == 1
        assert "world_model_files" in extra
        assert isinstance(extra["world_model_files"], int)
        # Les champs d'origine restent présents
        assert "stuck" in extra

    def test_metrics_tolerates_missing_attributes(self):
        """Si getattr renvoie des valeurs par défaut (ancienne instance), ne crash pas."""
        agent = CodeAgent.__new__(CodeAgent)
        # Volontairement minimal : pas de _reflexion_generated_count etc.
        agent._task_workspace_root = None

        result = MagicMock()
        result.success = False
        result.status_code = "FAIL"
        result.meta = {"iterations": 3}

        task = MagicMock()
        task.task_id = "task-x"
        llm = MagicMock()
        llm.model_name = "deepseek-v3"

        # Ne doit lever aucune exception (best-effort)
        agent._finalize_metrics(result, task, llm, 0.0, 1)
