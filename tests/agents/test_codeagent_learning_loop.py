"""Tests pour l'intégration SuccessStore + auto-eval + triggers Reflexion élargis."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

from src.agents.sub_agent import CodeAgent


# ── Helper ──────────────────────────────────────────────────────────────────


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
    agent._applied_success_ids = []
    agent._success_generated_count = 0
    agent._tools_used_this_task = []
    agent._auto_eval_triggered = False
    return agent


# ── Init CodeAgent ──────────────────────────────────────────────────────────


class TestCodeAgentInit:
    def test_init_defines_success_counters(self):
        agent = CodeAgent()
        assert agent._applied_success_ids == []
        assert agent._success_generated_count == 0
        assert agent._tools_used_this_task == []
        assert agent._auto_eval_triggered is False


# ── Métriques étendues ──────────────────────────────────────────────────────


class TestMetricsExtras:
    def test_finalize_metrics_includes_success_and_autoeval(self):
        agent = _make_agent()
        agent._success_generated_count = 2
        agent._applied_success_ids = ["succ_a"]
        agent._auto_eval_triggered = True

        result = MagicMock()
        result.success = True
        result.status_code = "SUCCESS"
        result.meta = {"iterations": 5, "stuck": False}

        task = MagicMock()
        task.task_id = "t-1"
        llm = MagicMock()
        llm.model_name = "deepseek-v3"

        captured = {}

        def fake_record(**kwargs):
            captured.update(kwargs)

        with patch("src.utils.metrics.record_task_metrics", side_effect=fake_record), \
             patch("src.config.codeagent_flags.CODING_METRICS", True):
            agent._finalize_metrics(result, task, llm, 0.0, 1)

        extra = captured.get("extra", {})
        assert extra.get("successes_applied") == 1
        assert extra.get("successes_generated") == 2
        assert extra.get("auto_eval") is True


# ── _maybe_generate_success_pattern (P1) ────────────────────────────────────


class TestMaybeGenerateSuccessPattern:
    @pytest.mark.asyncio
    async def test_skips_when_already_generated(self):
        agent = _make_agent()
        agent._success_generated_count = 1
        agent.client = MagicMock()
        # Doit retourner sans appeler le LLM
        with patch("src.learning.success_store.get_success_store") as m_store:
            await agent._maybe_generate_success_pattern(
                task_description="Une tâche longue",
                tools_used=["read_file"],
                iterations=5,
                outcome_summary="ok",
            )
            m_store.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_when_no_llm(self):
        """Phase 0 — Adapté : on n'utilise plus self.client mais self._get_llm().

        Si _get_llm() retourne None (cas edge), la fonction doit skip silencieusement.
        """
        agent = _make_agent()
        # Patch _get_llm pour qu'il retourne None
        with patch.object(agent, "_get_llm", return_value=None):
            await agent._maybe_generate_success_pattern(
                task_description="Tâche qui a réussi plein de code",
                tools_used=["read_file"],
                iterations=3,
                outcome_summary="Tests passent",
            )
        assert agent._success_generated_count == 0

    @pytest.mark.asyncio
    async def test_skips_short_description(self):
        agent = _make_agent()
        agent.client = MagicMock()
        await agent._maybe_generate_success_pattern(
            task_description="ok",  # < 12 chars
            tools_used=["x"],
            iterations=1,
            outcome_summary="y",
        )
        assert agent._success_generated_count == 0

    @pytest.mark.asyncio
    async def test_generates_on_valid_llm_response(self, tmp_path):
        """Phase 0 — Adapté : on utilise self._get_llm() + AsyncMock.

        Phase 0.5 — Le mot "bug" déclenche le filtre anti-pollution (strict
        volontaire). On utilise "problème" dans la description pour valider
        qu'une tâche légitime SANS mot destructif passe normalement.
        """
        from src.learning.success_store import (
            get_success_store, reset_success_store, SuccessStore,
        )
        reset_success_store()
        # Redirige le store vers tmp_path
        with patch.object(SuccessStore, "DEFAULT_PATH", tmp_path / "s.jsonl"):
            agent = _make_agent()

            # Mock LLM avec API standard Lumena : await llm.chat(messages=..., ...)
            mock_llm = MagicMock()
            mock_llm.chat = AsyncMock(return_value=(
                '{"task_type":"bugfix","summary":"Fix X","approach":"read then edit",'
                '"apply_when":"auth issue","tags":["x"],"confidence":0.8}'
            ))

            with patch.object(agent, "_get_llm", return_value=mock_llm):
                await agent._maybe_generate_success_pattern(
                    task_description="Corriger problème authentification critique",
                    tools_used=["read_file", "edit_file"],
                    iterations=4,
                    outcome_summary="Tests passent",
                )
            assert agent._success_generated_count == 1
            store = get_success_store()
            assert len(store) == 1
        reset_success_store()


# ── _maybe_auto_evaluate_success (P2) ───────────────────────────────────────


class TestAutoEvaluate:
    @pytest.mark.asyncio
    async def test_sets_triggered_flag(self):
        agent = _make_agent()
        agent.client = None  # pas de LLM, mais le flag doit passer
        await agent._maybe_auto_evaluate_success("desc", [])
        assert agent._auto_eval_triggered is True

    @pytest.mark.asyncio
    async def test_runs_only_once(self):
        agent = _make_agent()
        agent._auto_eval_triggered = True
        agent.client = MagicMock()  # si appelé, ça ferait qqch
        await agent._maybe_auto_evaluate_success("desc longue", ["file.py"])
        # pas de create call
        agent.client.chat.completions.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_empty_edits(self):
        agent = _make_agent()
        agent.client = MagicMock()
        await agent._maybe_auto_evaluate_success("desc", [])
        agent.client.chat.completions.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_generates_preventive_reflexion_on_issue(self, tmp_path):
        """Phase 0 — Adapté : on utilise self._get_llm() au lieu de self.client.

        Le mock LLM expose `chat()` async au lieu de `client.chat.completions.create`.
        """
        from src.learning.reflexion_store import (
            get_reflexion_store, reset_reflexion_store, ReflexionStore,
        )
        reset_reflexion_store()

        # Crée un fichier pour que edits_done soit résolu
        f = tmp_path / "test.py"
        f.write_text("def foo(): return 1", encoding="utf-8")

        agent = _make_agent()
        agent._resolve_path = lambda p: Path(p)

        # Mock LLM avec API standard Lumena : await llm.chat(messages=..., ...)
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value=(
            '{"has_issue":true,"issue":"edge case non géré",'
            '"severity":"medium","lesson":"Tester aussi None"}'
        ))

        with patch.object(agent, "_get_llm", return_value=mock_llm), \
             patch.object(ReflexionStore, "DEFAULT_PATH", tmp_path / "r.jsonl"):
            await agent._maybe_auto_evaluate_success(
                task_description="Implémenter fonction foo",
                edits_done=[str(f)],
            )
            store = get_reflexion_store()
            assert len(store) == 1
            r = store.all()[0]
            assert "auto-eval" in " ".join(r.tags)
            assert agent._reflexion_generated_count == 1
        reset_reflexion_store()

    @pytest.mark.asyncio
    async def test_no_reflexion_when_has_issue_false(self, tmp_path):
        from src.learning.reflexion_store import (
            get_reflexion_store, reset_reflexion_store, ReflexionStore,
        )
        reset_reflexion_store()

        f = tmp_path / "clean.py"
        f.write_text("ok", encoding="utf-8")

        agent = _make_agent()
        agent._resolve_path = lambda p: Path(p)

        fake_resp = MagicMock()
        fake_resp.choices = [MagicMock()]
        fake_resp.choices[0].message.content = (
            '{"has_issue":false,"issue":"","severity":"low","lesson":""}'
        )
        client = MagicMock()
        client.chat.completions.create.return_value = fake_resp
        agent.client = client
        agent.model = "deepseek-chat"

        with patch.object(ReflexionStore, "DEFAULT_PATH", tmp_path / "r.jsonl"):
            await agent._maybe_auto_evaluate_success(
                task_description="Tâche propre",
                edits_done=[str(f)],
            )
            store = get_reflexion_store()
            assert len(store) == 0
        reset_reflexion_store()
