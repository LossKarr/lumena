"""Tests unitaires pour src/utils/errors.py"""
import pytest

from src.utils.errors import (
    LumenaError,
    ProviderError,
    ToolExecutionError,
    MemoryError_,
)


# ─── LumenaError ───────────────────────────────────────────────────────────

class TestLumenaError:
    def test_basic_message(self):
        e = LumenaError("something failed")
        assert str(e) == "something failed"
        assert e.category == "internal"
        assert e.retryable is False
        assert e.original is None

    def test_custom_category_and_retryable(self):
        e = LumenaError("network err", category="network", retryable=True)
        assert e.category == "network"
        assert e.retryable is True

    def test_with_original_exception(self):
        orig = ValueError("root cause")
        e = LumenaError("wrapper", original=orig)
        assert "root cause" in str(e)
        assert e.original is orig

    def test_is_exception(self):
        with pytest.raises(LumenaError):
            raise LumenaError("test")


# ─── ProviderError ─────────────────────────────────────────────────────────

class TestProviderError:
    def test_defaults(self):
        e = ProviderError("provider failed")
        assert e.provider == ""
        assert e.model == ""
        assert e.category == "network"
        assert e.retryable is True

    def test_with_provider_and_model(self):
        e = ProviderError("quota exceeded", provider="openai", model="gpt-4")
        assert e.provider == "openai"
        assert e.model == "gpt-4"

    def test_inherits_lumena_error(self):
        e = ProviderError("err")
        assert isinstance(e, LumenaError)

    def test_not_retryable_override(self):
        e = ProviderError("auth error", retryable=False)
        assert e.retryable is False

    def test_str_representation(self):
        e = ProviderError("timeout", original=TimeoutError("upstream"))
        assert "timeout" in str(e)
        assert "upstream" in str(e)


# ─── ToolExecutionError ────────────────────────────────────────────────────

class TestToolExecutionError:
    def test_defaults(self):
        e = ToolExecutionError("tool error")
        assert e.tool_name == ""
        assert e.category == "internal"
        assert e.retryable is False

    def test_with_tool_name(self):
        e = ToolExecutionError("file not found", tool_name="write_file")
        assert e.tool_name == "write_file"

    def test_inherits_lumena_error(self):
        e = ToolExecutionError("err")
        assert isinstance(e, LumenaError)

    def test_can_be_raised_and_caught(self):
        with pytest.raises(ToolExecutionError) as exc_info:
            raise ToolExecutionError("broken tool", tool_name="read_file")
        assert exc_info.value.tool_name == "read_file"


# ─── MemoryError_ ──────────────────────────────────────────────────────────

class TestMemoryError:
    def test_defaults(self):
        e = MemoryError_("memory failure")
        assert e.category == "internal"
        assert e.retryable is False

    def test_inherits_lumena_error(self):
        e = MemoryError_("err")
        assert isinstance(e, LumenaError)

    def test_does_not_conflict_with_builtin(self):
        # MemoryError_ should NOT be the same as built-in MemoryError
        e = MemoryError_("test")
        assert not isinstance(e, MemoryError)

    def test_str(self):
        e = MemoryError_("chromadb down")
        assert "chromadb down" in str(e)
