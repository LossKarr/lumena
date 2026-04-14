"""
Tests P3 — Analyse sentimentale LLM.
"""
import asyncio
import json
from unittest.mock import AsyncMock, patch, MagicMock

import pytest


@pytest.fixture(autouse=True)
def _no_persist(tmp_path):
    """Isole les tests du state persisté sur disque."""
    with patch("src.utils.paths.EMOTION_STATE_FILE", tmp_path / "state.json"):
        with patch("src.utils.paths.EMOTION_HISTORY_FILE", tmp_path / "hist.jsonl"):
            yield


# ── SentimentResult dataclass ─────────────────────────────────────────────────

class TestSentimentResult:
    def test_default_values(self):
        from src.emotion import SentimentResult
        sr = SentimentResult()
        assert sr.pleasure_delta == 0.0
        assert sr.arousal_delta == 0.0
        assert sr.dominance_delta == 0.0
        assert sr.is_compliment is False
        assert sr.is_question is False
        assert sr.confidence == 0.0

    def test_custom_values(self):
        from src.emotion import SentimentResult
        sr = SentimentResult(
            pleasure_delta=0.2, arousal_delta=0.1, dominance_delta=-0.1,
            is_compliment=True, is_question=False, confidence=0.85,
        )
        assert sr.pleasure_delta == pytest.approx(0.2)
        assert sr.confidence == pytest.approx(0.85)
        assert sr.is_compliment is True


# ── analyze_sentiment_llm ─────────────────────────────────────────────────────

class TestAnalyzeSentimentLLM:
    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_returns_sentiment_result_on_success(self):
        from src.emotion import analyze_sentiment_llm, SentimentResult
        json_resp = json.dumps({
            "pleasure": 0.2, "arousal": 0.1, "dominance": 0.05,
            "compliment": True, "question": False, "confidence": 0.9,
        })
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value=json_resp)
        with patch("src.llm.multi_provider.MultiProviderLLM", return_value=mock_llm):
            result = self._run(analyze_sentiment_llm("Tu es géniale !"))
        assert isinstance(result, SentimentResult)
        assert result.confidence == pytest.approx(0.9)
        assert result.is_compliment is True
        assert result.pleasure_delta == pytest.approx(0.2)

    def test_returns_none_on_timeout(self):
        from src.emotion import analyze_sentiment_llm
        # Simuler un TimeoutError directement sans attendre 3 vraies secondes
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
            result = self._run(analyze_sentiment_llm("test"))
        assert result is None

    def test_returns_none_on_invalid_json(self):
        from src.emotion import analyze_sentiment_llm
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value="Désolé je ne comprends pas")
        with patch("src.llm.multi_provider.MultiProviderLLM", return_value=mock_llm):
            result = self._run(analyze_sentiment_llm("test"))
        assert result is None

    def test_returns_none_on_import_error(self):
        from src.emotion import analyze_sentiment_llm
        import builtins
        original_import = builtins.__import__

        def bad_import(name, *args, **kwargs):
            if "multi_provider" in name:
                raise ImportError("mock import error")
            return original_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=bad_import):
            result = self._run(analyze_sentiment_llm("test"))
        assert result is None

    def test_json_embedded_in_text(self):
        """LLM parfois entoure le JSON de texte."""
        from src.emotion import analyze_sentiment_llm, SentimentResult
        raw = 'Voici mon analyse : {"pleasure": 0.15, "arousal": 0.05, "dominance": 0.0, "compliment": false, "question": true, "confidence": 0.7} Fin.'
        mock_llm = MagicMock()
        mock_llm.chat = AsyncMock(return_value=raw)
        with patch("src.llm.multi_provider.MultiProviderLLM", return_value=mock_llm):
            result = self._run(analyze_sentiment_llm("pourquoi ?"))
        assert result is not None
        assert result.is_question is True
        assert result.confidence == pytest.approx(0.7)


# ── process_user_message async ────────────────────────────────────────────────

class TestProcessUserMessageAsync:
    def _run(self, coro):
        return asyncio.get_event_loop().run_until_complete(coro)

    def test_is_coroutine(self):
        from src.emotion import EmotionManager
        import inspect
        mgr = EmotionManager()
        result = mgr.process_user_message("hello")
        assert inspect.isawaitable(result)
        # Nettoyer la coroutine non consommée
        result.close()

    def test_llm_disabled_uses_keyword(self):
        """LUMENA_EMOTION_LLM_ANALYSIS=0 → keyword fallback."""
        from src.emotion import EmotionManager
        mgr = EmotionManager()
        with patch.dict("os.environ", {"LUMENA_EMOTION_LLM_ANALYSIS": "0"}):
            with patch("src.emotion.analyze_sentiment_llm") as mock_llm:
                self._run(mgr.process_user_message("tu es super géniale merci !"))
                mock_llm.assert_not_called()

    def test_compliment_increments_counter_via_llm(self):
        from src.emotion import EmotionManager, SentimentResult
        mgr = EmotionManager()
        initial_count = mgr.state.compliments_received
        mock_result = SentimentResult(
            pleasure_delta=0.2, arousal_delta=0.1, dominance_delta=0.0,
            is_compliment=True, confidence=0.9,
        )
        with patch("src.emotion.analyze_sentiment_llm", return_value=mock_result):
            with patch.dict("os.environ", {"LUMENA_EMOTION_LLM_ANALYSIS": "1"}):
                self._run(mgr.process_user_message("tu es géniale !"))
        assert mgr.state.compliments_received == initial_count + 1

    def test_question_increments_counter_via_llm(self):
        from src.emotion import EmotionManager, SentimentResult
        mgr = EmotionManager()
        initial_q = mgr.state.questions_asked
        mock_result = SentimentResult(
            is_question=True, confidence=0.8,
        )
        with patch("src.emotion.analyze_sentiment_llm", return_value=mock_result):
            with patch.dict("os.environ", {"LUMENA_EMOTION_LLM_ANALYSIS": "1"}):
                self._run(mgr.process_user_message("pourquoi ?"))
        assert mgr.state.questions_asked == initial_q + 1

    def test_low_confidence_falls_back_to_keyword(self):
        """LLM retourne confidence < 0.5 → fallback keyword."""
        from src.emotion import EmotionManager, SentimentResult
        mgr = EmotionManager()
        mock_result = SentimentResult(confidence=0.3)  # trop bas
        with patch("src.emotion.analyze_sentiment_llm", return_value=mock_result):
            with patch.dict("os.environ", {"LUMENA_EMOTION_LLM_ANALYSIS": "1"}):
                with patch.object(mgr.analyzer, "analyze_message", wraps=mgr.analyzer.analyze_message) as m:
                    self._run(mgr.process_user_message("test message"))
                    m.assert_called_once()

    def test_interactions_count_incremented(self):
        from src.emotion import EmotionManager
        mgr = EmotionManager()
        initial = mgr.state.interactions_count
        with patch.dict("os.environ", {"LUMENA_EMOTION_LLM_ANALYSIS": "0"}):
            self._run(mgr.process_user_message("bonjour"))
        assert mgr.state.interactions_count == initial + 1


# ── process_own_response async ────────────────────────────────────────────────

class TestProcessOwnResponseAsync:
    def test_is_coroutine(self):
        from src.emotion import EmotionManager
        import inspect
        mgr = EmotionManager()
        result = mgr.process_own_response("réponse test")
        assert inspect.isawaitable(result)
        result.close()

    def test_task_completed_increments_counter(self):
        from src.emotion import EmotionManager
        import asyncio
        mgr = EmotionManager()
        initial = mgr.state.tasks_completed
        asyncio.get_event_loop().run_until_complete(
            mgr.process_own_response("C'est fait !", task_completed=True)
        )
        assert mgr.state.tasks_completed == initial + 1


# ── Config schema ─────────────────────────────────────────────────────────────

class TestConfigSchema:
    def test_lumena_emotion_llm_analysis_in_schema(self):
        """LUMENA_EMOTION_LLM_ANALYSIS doit être dans le schéma config."""
        from web.routes.config import _CONFIG_SCHEMA
        keys = [e["key"] for e in _CONFIG_SCHEMA]
        assert "LUMENA_EMOTION_LLM_ANALYSIS" in keys

    def test_default_is_1(self):
        from web.routes.config import _CONFIG_SCHEMA
        entry = next(e for e in _CONFIG_SCHEMA if e["key"] == "LUMENA_EMOTION_LLM_ANALYSIS")
        assert entry["default"] == "1"
        assert entry["type"] == "bool"
