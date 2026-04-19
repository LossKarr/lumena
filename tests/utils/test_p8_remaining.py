"""Tests P8.5-8 : MODEL_TEMPERATURES, COMPACTION_REPLAY, SSE_TIMEOUT, PROMPT_CACHE."""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest


# ══════════════════════════════════════════════════════════
# P8.MODEL_TEMPERATURES
# ══════════════════════════════════════════════════════════

def test_model_temperature_deepseek_low():
    from src.utils.model_temperatures import get_model_temperature
    assert get_model_temperature("deepseek-v3") == 0.05
    assert get_model_temperature("deepseek-reasoner") == 0.0


def test_model_temperature_claude():
    from src.utils.model_temperatures import get_model_temperature
    assert get_model_temperature("claude-opus-4") == 0.1
    assert get_model_temperature("claude-sonnet-4") == 0.1
    # Fallback claude générique
    assert get_model_temperature("claude-haiku") == 0.15


def test_model_temperature_reasoning_models():
    from src.utils.model_temperatures import get_model_temperature
    assert get_model_temperature("o1-preview") == 1.0
    assert get_model_temperature("o3-mini") == 1.0
    assert get_model_temperature("o4-mini") == 1.0


def test_model_temperature_fallback_unknown():
    from src.utils.model_temperatures import get_model_temperature
    assert get_model_temperature("totally-unknown-model-xyz", fallback=0.25) == 0.25


def test_model_temperature_flag_off_returns_fallback(monkeypatch):
    monkeypatch.setenv("LUMENA_MODEL_TEMPERATURES", "false")
    import src.config.codeagent_flags as cf
    importlib.reload(cf)
    from src.utils.model_temperatures import get_model_temperature
    assert get_model_temperature("deepseek-v3", fallback=0.7) == 0.7
    importlib.reload(cf)


def test_model_temperature_hook_in_sub_agent():
    src = Path(__file__).resolve().parents[2] / "src" / "agents" / "sub_agent.py"
    content = src.read_text(encoding="utf-8")
    assert "from src.utils.model_temperatures import get_model_temperature" in content
    assert "MODEL_TEMPERATURES" in content


# ══════════════════════════════════════════════════════════
# P8.COMPACTION_REPLAY
# ══════════════════════════════════════════════════════════

def test_compaction_replay_hook_in_sub_agent():
    src = Path(__file__).resolve().parents[2] / "src" / "agents" / "sub_agent.py"
    content = src.read_text(encoding="utf-8")
    assert "COMPACTION_REPLAY" in content
    assert "compaction_" in content  # filename pattern


def test_compaction_replay_flag_default_on():
    from src.config.codeagent_flags import COMPACTION_REPLAY
    assert COMPACTION_REPLAY is True


# ══════════════════════════════════════════════════════════
# P8.SSE_TIMEOUT
# ══════════════════════════════════════════════════════════

def test_sse_timeout_hook_in_sub_agent():
    src = Path(__file__).resolve().parents[2] / "src" / "agents" / "sub_agent.py"
    content = src.read_text(encoding="utf-8")
    assert "SSE_TIMEOUT" in content
    assert "asyncio.wait_for" in content
    assert "LUMENA_SSE_TIMEOUT_SECONDS" in content


def test_sse_timeout_flag_default_on():
    from src.config.codeagent_flags import SSE_TIMEOUT
    assert SSE_TIMEOUT is True


# ══════════════════════════════════════════════════════════
# P8.PROMPT_CACHE
# ══════════════════════════════════════════════════════════

def test_prompt_cache_flag_default_on():
    from src.config.codeagent_flags import PROMPT_CACHE
    assert PROMPT_CACHE is True


def test_prompt_cache_env_context_caches_results(monkeypatch):
    """Avec PROMPT_CACHE, 2 appels identiques → même objet (lru_cache)."""
    import src.utils.env_context as ec
    importlib.reload(ec)
    ec._build_cached.cache_clear()

    r1 = ec.build_env_context_block("/tmp/ws")
    r2 = ec.build_env_context_block("/tmp/ws")
    assert r1 == r2
    # Le cache a été utilisé (au moins 1 hit après le 1er appel)
    info = ec._build_cached.cache_info()
    assert info.hits >= 1


def test_prompt_cache_flag_off_clears_cache(monkeypatch):
    monkeypatch.setenv("LUMENA_PROMPT_CACHE", "false")
    import src.config.codeagent_flags as cf
    importlib.reload(cf)
    import src.utils.env_context as ec
    importlib.reload(ec)

    r1 = ec.build_env_context_block("/tmp/ws1")
    r2 = ec.build_env_context_block("/tmp/ws2")
    # Résultats différents (CWD différent)
    assert r1 != r2
    importlib.reload(cf)
    importlib.reload(ec)
