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
