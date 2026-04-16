"""Tests unitaires pour src/llm/model_manager.py"""
import pytest
from datetime import datetime, timedelta

from src.llm.model_manager import ModelHealth, ModelStatus, ModelManager


class TestModelHealth:
    def test_default_available(self):
        mh = ModelHealth(model_name="gpt-4", provider="openai")
        assert mh.is_available() is True

    def test_disabled_not_available(self):
        mh = ModelHealth(model_name="gpt-4", provider="openai",
                         status=ModelStatus.DISABLED)
        assert mh.is_available() is False

    def test_rate_limited_with_future_expiry(self):
        mh = ModelHealth(
            model_name="gpt-4", provider="openai",
            status=ModelStatus.RATE_LIMITED,
            rate_limit_until=datetime.now() + timedelta(minutes=10)
        )
        assert mh.is_available() is False

    def test_rate_limited_expired_becomes_available(self):
        mh = ModelHealth(
            model_name="gpt-4", provider="openai",
            status=ModelStatus.RATE_LIMITED,
            rate_limit_until=datetime.now() - timedelta(seconds=1)
        )
        assert mh.is_available() is True

    def test_record_success(self):
        mh = ModelHealth(model_name="gpt-4", provider="openai")
        mh.record_success()
        assert mh.request_count == 1
        assert mh.error_count == 0
        assert mh.status == ModelStatus.AVAILABLE

    def test_record_error_increments(self):
        mh = ModelHealth(model_name="gpt-4", provider="openai")
        mh.record_error("some error")
        assert mh.error_count == 1

    def test_record_rate_limit_error(self):
        mh = ModelHealth(model_name="gpt-4", provider="openai")
        mh.record_error("429 rate limit exceeded")
        assert mh.status == ModelStatus.RATE_LIMITED

    def test_three_errors_become_unavailable(self):
        mh = ModelHealth(model_name="gpt-4", provider="openai")
        mh.record_error("error 1")
        mh.record_error("error 2")
        mh.record_error("error 3")
        assert mh.is_available() is False


class TestModelManager:
    @pytest.fixture
    def mm(self):
        return ModelManager()

    def test_init_empty(self, mm):
        assert mm is not None
        assert isinstance(mm.model_health, dict)

    def test_add_model(self, mm):
        mm.add_to_fallback_chain("claude-3")
        assert "claude-3" in mm.model_health

    def test_get_best_model_no_preference(self, mm):
        mm.add_to_fallback_chain("deepseek-v3")
        best = mm.get_next_available_model()
        assert best is not None

    def test_record_success(self, mm):
        mm.add_to_fallback_chain("test-model-s")
        mm.record_success("test-model-s")
        assert mm.model_health["test-model-s"].request_count == 1

    def test_record_error(self, mm):
        mm.add_to_fallback_chain("test-model-e")
        mm.record_error("test-model-e", "server error")
        assert mm.model_health["test-model-e"].error_count == 1

    def test_get_stats(self, mm):
        mm.add_to_fallback_chain("m1")
        stats = mm.get_health_report()
        assert isinstance(stats, dict)
        assert "models" in stats or "current_model" in stats
