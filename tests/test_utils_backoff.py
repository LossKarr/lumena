"""Tests unitaires pour src/utils/backoff.py"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.utils.backoff import (
    BackoffConfig,
    DEFAULT_CONFIG,
    API_CONFIG,
    LLM_CONFIG,
    calculate_delay,
    retry_async,
    retry_sync,
    with_retry,
)


# ─── calculate_delay ───────────────────────────────────────────────────────

class TestCalculateDelay:
    def test_attempt_zero_returns_initial_delay(self):
        """Le premier délai doit être proche du délai initial."""
        delay = calculate_delay(0, initial_delay=1.0, jitter=False)
        assert delay == pytest.approx(1.0)

    def test_exponential_growth(self):
        """Le délai double à chaque tentative (sans jitter)."""
        d0 = calculate_delay(0, initial_delay=1.0, exponential_base=2.0, jitter=False)
        d1 = calculate_delay(1, initial_delay=1.0, exponential_base=2.0, jitter=False)
        d2 = calculate_delay(2, initial_delay=1.0, exponential_base=2.0, jitter=False)
        assert d1 == pytest.approx(d0 * 2)
        assert d2 == pytest.approx(d0 * 4)

    def test_capped_by_max_delay(self):
        """Le délai ne dépasse jamais max_delay."""
        delay = calculate_delay(100, initial_delay=1.0, max_delay=10.0, jitter=False)
        assert delay == pytest.approx(10.0)

    def test_jitter_within_bounds(self):
        """Avec jitter le délai est dans [0.75*base, 1.25*base]."""
        for _ in range(50):
            delay = calculate_delay(0, initial_delay=1.0, jitter=True)
            assert 0.75 <= delay <= 1.25

    def test_no_jitter_deterministic(self):
        """Sans jitter le résultat est toujours identique."""
        delays = [calculate_delay(2, initial_delay=1.0, jitter=False) for _ in range(10)]
        assert len(set(delays)) == 1

    def test_custom_base(self):
        """Base exponentielle personnalisée."""
        delay = calculate_delay(2, initial_delay=1.0, exponential_base=3.0, jitter=False)
        assert delay == pytest.approx(9.0)


# ─── BackoffConfig ─────────────────────────────────────────────────────────

class TestBackoffConfig:
    def test_defaults(self):
        cfg = BackoffConfig()
        assert cfg.initial_delay == 1.0
        assert cfg.max_delay == 60.0
        assert cfg.max_retries == 5
        assert cfg.exponential_base == 2.0
        assert cfg.jitter is True

    def test_custom_config(self):
        cfg = BackoffConfig(initial_delay=0.1, max_retries=2, max_delay=5.0)
        assert cfg.initial_delay == 0.1
        assert cfg.max_retries == 2
        assert cfg.max_delay == 5.0

    def test_api_config(self):
        assert API_CONFIG.max_retries == 3
        assert API_CONFIG.initial_delay == 1.0

    def test_llm_config(self):
        assert LLM_CONFIG.max_retries == 5
        assert LLM_CONFIG.initial_delay == 2.0


# ─── retry_sync ────────────────────────────────────────────────────────────

class TestRetrySync:
    def test_success_on_first_try(self):
        func = MagicMock(return_value=42)
        config = BackoffConfig(max_retries=3, initial_delay=0.001)
        result = retry_sync(func, config=config)
        assert result == 42
        assert func.call_count == 1

    def test_retries_then_succeeds(self):
        func = MagicMock(side_effect=[Exception("fail"), Exception("fail"), 99])
        config = BackoffConfig(max_retries=3, initial_delay=0.001, jitter=False)
        result = retry_sync(func, config=config)
        assert result == 99
        assert func.call_count == 3

    def test_raises_after_max_retries(self):
        func = MagicMock(side_effect=ValueError("always fails"))
        config = BackoffConfig(max_retries=2, initial_delay=0.001, jitter=False)
        with pytest.raises(ValueError, match="always fails"):
            retry_sync(func, config=config)
        assert func.call_count == 2

    def test_only_retries_configured_exceptions(self):
        func = MagicMock(side_effect=TypeError("wrong type"))
        config = BackoffConfig(
            max_retries=3,
            initial_delay=0.001,
            jitter=False,
            retryable_exceptions=(ValueError,)
        )
        with pytest.raises(TypeError):
            retry_sync(func, config=config)
        assert func.call_count == 1

    def test_passes_args_and_kwargs(self):
        func = MagicMock(return_value="ok")
        config = BackoffConfig(max_retries=1, initial_delay=0.001)
        retry_sync(func, "arg1", config=config, kwarg1="val1")
        func.assert_called_with("arg1", kwarg1="val1")


# ─── retry_async ───────────────────────────────────────────────────────────

class TestRetryAsync:
    @pytest.mark.asyncio
    async def test_success_on_first_try(self):
        func = AsyncMock(return_value="hello")
        config = BackoffConfig(max_retries=3, initial_delay=0.001)
        result = await retry_async(func, config=config)
        assert result == "hello"
        assert func.call_count == 1

    @pytest.mark.asyncio
    async def test_retries_then_succeeds(self):
        func = AsyncMock(side_effect=[Exception("err"), Exception("err"), "ok"])
        config = BackoffConfig(max_retries=3, initial_delay=0.001, jitter=False)
        result = await retry_async(func, config=config)
        assert result == "ok"
        assert func.call_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_max_retries(self):
        func = AsyncMock(side_effect=RuntimeError("boom"))
        config = BackoffConfig(max_retries=2, initial_delay=0.001, jitter=False)
        with pytest.raises(RuntimeError, match="boom"):
            await retry_async(func, config=config)
        assert func.call_count == 2

    @pytest.mark.asyncio
    async def test_passes_args(self):
        func = AsyncMock(return_value="done")
        config = BackoffConfig(max_retries=1, initial_delay=0.001)
        await retry_async(func, "a", "b", config=config)
        func.assert_called_with("a", "b")


# ─── with_retry decorator ──────────────────────────────────────────────────

class TestWithRetryDecorator:
    @pytest.mark.asyncio
    async def test_decorator_success(self):
        call_count = 0

        @with_retry(max_retries=3, initial_delay=0.001)
        async def my_func():
            nonlocal call_count
            call_count += 1
            return "result"

        result = await my_func()
        assert result == "result"
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_decorator_retries(self):
        call_count = 0

        @with_retry(max_retries=3, initial_delay=0.001)
        async def my_func():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("not ready")
            return "done"

        result = await my_func()
        assert result == "done"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_decorator_raises_after_all_retries(self):
        @with_retry(max_retries=2, initial_delay=0.001)
        async def always_fail():
            raise TimeoutError("timeout")

        with pytest.raises(TimeoutError):
            await always_fail()
