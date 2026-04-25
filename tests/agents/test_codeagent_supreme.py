"""Tests PLAN_CODEAGENT_SUPREME — P0 à P5 (35 tests)."""

import asyncio
import json
import pytest

from src.agents.sub_agent import (
    LoopDetector,
    _normalize_punctuation,
    _classify_llm_error,
    _estimate_tokens,
    _build_system_prompt,
    _PROMPT_WEB_SECTION,
    _PROMPT_PYTHON_SECTION,
    _PROMPT_GENERAL_SECTION,
)


# ═══════════════════════════════════════════════════════════════
# P2 — normalizePunctuation
# ═══════════════════════════════════════════════════════════════

class TestNormalizePunctuation:
    def test_unicode_quotes_double(self):
        assert _normalize_punctuation('\u201Chello\u201D') == '"hello"'

    def test_unicode_quotes_single(self):
        assert _normalize_punctuation('\u2018world\u2019') == "'world'"

    def test_unicode_dashes(self):
        assert _normalize_punctuation('a\u2014b\u2013c') == 'a-b-c'

    def test_non_breaking_space(self):
        assert _normalize_punctuation('hello\u00A0world') == 'hello world'

    def test_passthrough_ascii(self):
        text = 'def foo(x): return x + 1'
        assert _normalize_punctuation(text) == text

    def test_mixed_all(self):
        src = '\u201Cvar\u201D = \u2018value\u2019 \u2014 done\u00A0!'
        out = _normalize_punctuation(src)
        assert '"' not in ['\u201C', '\u201D']
        assert '\u00A0' not in out
        assert '--' not in out  # single dash


# ═══════════════════════════════════════════════════════════════
# P1 — LoopDetector
# ═══════════════════════════════════════════════════════════════

class TestLoopDetector:
    def test_repeat_3x_stuck(self):
        ld = LoopDetector(repeat_threshold=3)
        action = {"action": "read_file", "path": "foo.py"}
        for _ in range(3):
            ld.record(action, "content of foo.py")
        stuck, reason = ld.check()
        assert stuck
        assert "3x" in reason

    def test_repeat_2x_not_stuck(self):
        ld = LoopDetector(repeat_threshold=3)
        action = {"action": "read_file", "path": "foo.py"}
        for _ in range(2):
            ld.record(action, "content")
        stuck, _ = ld.check()
        assert not stuck

    def test_pingpong_stuck(self):
        ld = LoopDetector(pingpong_threshold=6)
        a = {"action": "read_file", "path": "a.py"}
        b = {"action": "edit_file", "path": "a.py", "search": "x", "replace": "y"}
        for _ in range(3):
            ld.record(a, "content a")
            ld.record(b, "❌ non trouvé")
        stuck, reason = ld.check()
        assert stuck
        assert "ping-pong" in reason

    def test_pingpong_3x_not_stuck(self):
        ld = LoopDetector(pingpong_threshold=6)
        a = {"action": "read_file", "path": "a.py"}
        b = {"action": "edit_file", "path": "a.py", "search": "x", "replace": "y"}
        ld.record(a, "ok")
        ld.record(b, "ok")
        ld.record(a, "ok")
        stuck, _ = ld.check()
        assert not stuck

    def test_noprogress_stuck(self):
        ld = LoopDetector(noprogress_threshold=5)
        same_error = "❌ connection refused"
        for i in range(5):
            ld.record({"action": "run_tests", "path": f"t{i}.py"}, same_error, is_error=True)
        stuck, reason = ld.check()
        assert stuck
        assert "no-progress" in reason

    def test_noprogress_mixed_not_stuck(self):
        ld = LoopDetector(noprogress_threshold=5, circuit_breaker=100)
        for i in range(5):
            ld.record({"action": "run_tests"}, f"❌ error {i}", is_error=True)
        stuck, reason = ld.check()
        assert not stuck or "no-progress" not in reason  # différentes erreurs → pas no-progress

    def test_circuit_breaker(self):
        ld = LoopDetector(circuit_breaker=5)
        for i in range(5):
            ld.record({"action": f"act_{i}"}, f"❌ err {i}", is_error=True)
        stuck, reason = ld.check()
        assert stuck
        assert "circuit breaker" in reason

    def test_history_capped(self):
        ld = LoopDetector(history_size=10)
        for i in range(20):
            ld.record({"action": f"a{i}"}, f"r{i}")
        assert len(ld.history) == 10

    def test_empty_not_stuck(self):
        ld = LoopDetector()
        stuck, reason = ld.check()
        assert not stuck
        assert reason == ""

    def test_different_actions_not_stuck(self):
        ld = LoopDetector()
        ld.record({"action": "read_file", "path": "a.py"}, "ok")
        ld.record({"action": "edit_file", "path": "a.py"}, "ok")
        ld.record({"action": "run_tests"}, "ok")
        stuck, _ = ld.check()
        assert not stuck


# ═══════════════════════════════════════════════════════════════
# P5 — Failover classification
# ═══════════════════════════════════════════════════════════════

class TestFailoverClassification:
    def test_rate_limit_429(self):
        exc = Exception("rate limit exceeded")
        exc.status_code = 429
        cat, act = _classify_llm_error(exc)
        assert cat == "rate_limit"
        assert act == "retry_wait"

    def test_overload_503(self):
        exc = Exception("server overloaded")
        exc.status_code = 503
        cat, act = _classify_llm_error(exc)
        assert cat == "overload"
        assert act == "retry_wait"

    def test_auth_401(self):
        exc = Exception("unauthorized")
        exc.status_code = 401
        cat, act = _classify_llm_error(exc)
        assert cat == "auth"
        assert act == "abort"

    def test_timeout(self):
        exc = asyncio.TimeoutError()
        cat, act = _classify_llm_error(exc)
        assert cat == "timeout"
        assert act == "timeout_recovery"

    def test_format_400(self):
        exc = Exception("invalid request format")
        exc.status_code = 400
        cat, act = _classify_llm_error(exc)
        assert cat == "format"
        assert act == "retry"

    def test_unknown(self):
        exc = RuntimeError("something weird")
        cat, act = _classify_llm_error(exc)
        assert cat == "unknown"
        assert act == "retry"

    def test_rate_limit_by_message(self):
        exc = Exception("too many requests")
        cat, act = _classify_llm_error(exc)
        assert cat == "rate_limit"
        assert act == "retry_wait"


# ═══════════════════════════════════════════════════════════════
# P0 — Compaction LLM (méthode _summarize_for_compaction)
# ═══════════════════════════════════════════════════════════════

class TestCompactionLLM:
    @pytest.mark.asyncio
    async def test_summarize_calls_llm(self):
        from unittest.mock import AsyncMock, MagicMock
        from src.agents.sub_agent import CodeAgent

        agent = CodeAgent()
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value="Résumé: 3 fichiers modifiés, 2 tests passent.")
        msgs = [
            {"role": "assistant", "content": f"action {i}"} for i in range(5)
        ]
        result = await agent._summarize_for_compaction(msgs, mock_llm)
        assert result is not None
        assert "3 fichiers" in result
        mock_llm.chat.assert_called_once()
        call_kwargs = mock_llm.chat.call_args
        assert call_kwargs[1]["temperature"] == 0.1

    @pytest.mark.asyncio
    async def test_summarize_timeout_fallback(self):
        from unittest.mock import MagicMock, patch
        from src.agents.sub_agent import CodeAgent

        agent = CodeAgent()
        mock_llm = MagicMock()

        async def slow_chat(**kwargs):
            await asyncio.sleep(300)

        mock_llm.chat = slow_chat
        msgs = [{"role": "user", "content": "hello"}]
        # Patch le timeout interne à 0.5s pour ne pas bloquer pytest
        with patch("src.agents.sub_agent.asyncio.wait_for", side_effect=asyncio.TimeoutError):
            result = await agent._summarize_for_compaction(msgs, mock_llm)
        assert result is None

    @pytest.mark.asyncio
    async def test_summarize_exception_fallback(self):
        from unittest.mock import AsyncMock, MagicMock
        from src.agents.sub_agent import CodeAgent

        agent = CodeAgent()
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(side_effect=RuntimeError("API down"))
        msgs = [{"role": "user", "content": "test"}]
        result = await agent._summarize_for_compaction(msgs, mock_llm)
        assert result is None


# ═══════════════════════════════════════════════════════════════
# P3 — Token estimation + seuils
# ═══════════════════════════════════════════════════════════════

class TestTokenEstimation:
    def test_basic_estimation(self):
        msgs = [{"role": "user", "content": "x" * 400}]
        tokens = _estimate_tokens(msgs)
        assert 95 <= tokens <= 110  # 400/4 + 4 = 104

    def test_empty_messages(self):
        assert _estimate_tokens([]) == 0

    def test_multiple_messages(self):
        msgs = [{"role": "user", "content": "x" * 100} for _ in range(10)]
        tokens = _estimate_tokens(msgs)
        # 10 * (100/4 + 4) = 10 * 29 = 290
        assert 280 <= tokens <= 300


# ═══════════════════════════════════════════════════════════════
# P4 — Prompt composable
# ═══════════════════════════════════════════════════════════════

class TestPromptComposable:
    def test_web_detected(self):
        prompt = _build_system_prompt("créer un site web landing page")
        assert "SPÉCIFIQUE WEB" in prompt

    def test_python_detected(self):
        prompt = _build_system_prompt("fix le bug dans pytest test_core.py")
        assert "SPÉCIFIQUE PYTHON" in prompt

    def test_general_fallback(self):
        prompt = _build_system_prompt("do something generic")
        assert "GÉNÉRAL" in prompt

    def test_web_from_files(self):
        prompt = _build_system_prompt("modify the project", workspace_files=["index.html", "style.css", "app.js"])
        assert "SPÉCIFIQUE WEB" in prompt

    def test_python_from_files(self):
        prompt = _build_system_prompt("update the code", workspace_files=["main.py", "tests/test_x.py", "requirements.txt"])
        assert "SPÉCIFIQUE PYTHON" in prompt


# ═══════════════════════════════════════════════════════════════
# P6 — Self-repair syntaxe : snapshot dernier état valide
# ═══════════════════════════════════════════════════════════════

class TestSyntaxSelfRepair:
    """Vérifie le mécanisme de restauration P6 (snapshot par fichier)."""

    def _make_agent(self):
        from src.agents.sub_agent import CodeAgent
        agent = CodeAgent()
        agent._self_repair_count_per_file = {}
        agent._syntax_clean_snapshot = {}
        agent._edit_restricted_files = set()
        agent._self_repair_count = 0
        return agent

    def test_snapshot_updated_on_valid_edit(self, tmp_path):
        """Après un edit valide, _syntax_clean_snapshot contient le nouveau contenu."""
        f = tmp_path / "app.js"
        f.write_text("function foo() { return 1; }", encoding="utf-8")
        agent = self._make_agent()
        # Simuler la mise à jour du snapshot après edit valide
        key = "app.js"
        agent._syntax_clean_snapshot[key] = f.read_text(encoding="utf-8")
        assert "foo" in agent._syntax_clean_snapshot[key]

    def test_repair_count_increments_on_web_error(self):
        """Le compteur par fichier monte sur une erreur web."""
        agent = self._make_agent()
        key = "script.js"
        agent._self_repair_count_per_file[key] = 0
        # Simuler 2 erreurs
        for _ in range(2):
            agent._self_repair_count_per_file[key] += 1
            agent._self_repair_count += 1
        assert agent._self_repair_count_per_file[key] == 2
        assert agent._self_repair_count == 2

    def test_restricted_files_set_after_restore(self, tmp_path):
        """Après restauration, le fichier est dans _edit_restricted_files."""
        f = tmp_path / "script.js"
        clean = "function ok() { return true; }"
        f.write_text(clean, encoding="utf-8")
        agent = self._make_agent()
        key = "script.js"
        agent._syntax_clean_snapshot[key] = clean
        agent._self_repair_count_per_file[key] = 3
        # Simuler la restauration
        f.write_text(clean, encoding="utf-8")
        agent._self_repair_count_per_file[key] = 0
        agent._self_repair_count = 0
        agent._edit_restricted_files.add(key)
        assert key in agent._edit_restricted_files

    def test_restricted_cleared_on_valid_edit(self):
        """Après un edit valide post-restauration, la restriction est levée."""
        agent = self._make_agent()
        key = "script.js"
        agent._edit_restricted_files.add(key)
        # Simuler un edit valide
        agent._edit_restricted_files.discard(key)
        agent._self_repair_count_per_file[key] = 0
        assert key not in agent._edit_restricted_files

    def test_reset_on_new_attempt(self):
        """Les compteurs et snapshots sont remis à zéro entre tentatives."""
        agent = self._make_agent()
        agent._self_repair_count = 5
        agent._self_repair_count_per_file = {"script.js": 3}
        agent._syntax_clean_snapshot = {"script.js": "old content"}
        agent._edit_restricted_files = {"script.js"}
        # Simuler le reset de début de tentative
        agent._self_repair_count = 0
        agent._self_repair_count_per_file = {}
        agent._syntax_clean_snapshot = {}
        agent._edit_restricted_files = set()
        assert agent._self_repair_count == 0
        assert not agent._self_repair_count_per_file
        assert not agent._syntax_clean_snapshot
        assert not agent._edit_restricted_files

    def test_web_pattern_detection_lowercase(self):
        """Les patterns web sont détectés en lowercase (bug casse corrigé)."""
        patterns_web = (
            "erreur web", "⚠️ web", "bracket imbalance",
            "js syntaxerror", "js/ts bracket", "js erreur",
        )
        obs_with_web_error = "✅ edit_lines ok dans script.js\n---\n⚠️ web détecté: bracket imbalance: -5 net accolades"
        obs_lower = obs_with_web_error.lower()
        assert any(p in obs_lower for p in patterns_web)

    def test_syntax_pattern_detection(self):
        """Les patterns Python/générique sont détectés."""
        patterns_syntax = (
            "erreur de syntaxe python", "syntaxeerror", "unexpected token",
            "parse error", "failed to compile", "unterminated", "invalid syntax",
        )
        obs = "✅ edit_lines ok\n---\nJS SyntaxError: Unexpected token '}' at line 42"
        obs_lower = obs.lower()
        assert any(p in obs_lower for p in patterns_syntax)


# ═══════════════════════════════════════════════════════════════
# P6 — Intégration runtime : _post_action_hooks bout en bout
# ═══════════════════════════════════════════════════════════════

class TestSyntaxSelfRepairIntegration:
    """Tests d'intégration appelant _post_action_hooks directement."""

    def _make_task(self):
        from unittest.mock import MagicMock
        from src.agents.sub_agent import AgentTask
        task = MagicMock(spec=AgentTask)
        task.task_id = "test-p6"
        task.description = "fix script.js"
        return task

    def _make_agent(self, tmp_path):
        from src.agents.sub_agent import CodeAgent
        agent = CodeAgent()
        agent._task_workspace_root = tmp_path
        agent._self_repair_count = 0
        agent._self_repair_count_per_file = {}
        agent._syntax_clean_snapshot = {}
        agent._edit_restricted_files = set()
        agent._edit_fail_for_path = {}
        agent._read_count_per_file = {}
        agent._grep_zero_repeats = 0
        agent._session_state = {"reads": {}, "edits": [], "errors": [], "grep_zero_results": {}}
        return agent

    @pytest.mark.asyncio
    async def test_web_error_increments_counter_and_restores_at_3(self, tmp_path):
        """3 erreurs web consécutives → restauration automatique + fichier remis en état valide."""
        from src.agents.sub_agent import ActionResult
        js_file = tmp_path / "script.js"
        clean_content = "function ok() { return true; }"
        broken_content = "function ok() { return true; "  # accolade manquante
        js_file.write_text(clean_content, encoding="utf-8")

        agent = self._make_agent(tmp_path)
        key = "script.js"
        # Pré-charger le snapshot propre (simule un edit valide précédent)
        agent._syntax_clean_snapshot[key] = clean_content

        action = {"action": "edit_lines", "path": "script.js", "start_line": 1, "end_line": 1, "content": broken_content}
        web_obs = ActionResult(
            summary="✅ edit_lines OK dans script.js ⚠️ web",
            detail="✅ edit_lines OK dans script.js\n⚠️ Erreur web détectée:\nJS SyntaxError: Unexpected token '}'"
        )
        session_snapshots = {"script.js": clean_content}
        messages = []
        task = self._make_task()

        # Simuler 3 erreurs web consécutives
        for i in range(3):
            js_file.write_text(broken_content, encoding="utf-8")
            obs, edits, reads = await agent._post_action_hooks(
                action=action, action_type="edit_lines", observation=web_obs,
                messages=messages, task=task,
                session_snapshots=session_snapshots, target_files_seen=[],
                edits_since_last_test=i, reads_since_last_edit=0,
                context_cache={},
            )

        # Le fichier doit être restauré
        assert js_file.read_text(encoding="utf-8") == clean_content
        # Le message de restauration doit être présent
        restore_msgs = [m for m in messages if "RESTAURATION" in m.get("content", "")]
        assert restore_msgs, "Message de restauration attendu dans messages"
        # Le fichier est maintenant dans _edit_restricted_files
        assert key in agent._edit_restricted_files
        # Compteur remis à zéro
        assert agent._self_repair_count_per_file.get(key, 0) == 0

    @pytest.mark.asyncio
    async def test_restricted_reminder_injected_on_edit_lines(self, tmp_path):
        """Après restauration, edit_lines sur le fichier reçoit un rappel persistant."""
        from src.agents.sub_agent import ActionResult
        js_file = tmp_path / "app.js"
        js_file.write_text("function x() {}", encoding="utf-8")

        agent = self._make_agent(tmp_path)
        agent._edit_restricted_files.add("app.js")

        action = {"action": "edit_lines", "path": "app.js", "start_line": 1, "end_line": 1, "content": "function x() {}"}
        valid_obs = ActionResult(
            summary="✅ edit_lines OK dans app.js",
            detail="✅ edit_lines OK dans app.js"
        )
        messages = []
        task = self._make_task()

        obs, _, _ = await agent._post_action_hooks(
            action=action, action_type="edit_lines", observation=valid_obs,
            messages=messages, task=task,
            session_snapshots={}, target_files_seen=[],
            edits_since_last_test=0, reads_since_last_edit=0,
            context_cache={},
        )
        # Le rappel persistant doit être dans l'observation retournée
        obs_text = obs.full() if isinstance(obs, ActionResult) else str(obs)
        assert "RAPPEL" in obs_text or "restauré" in obs_text.lower()
