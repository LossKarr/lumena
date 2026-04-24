"""Tests P0 — feature flags + provider prompt loader (PLAN_SUPREME_CODEAGENT)."""
from __future__ import annotations

import importlib
import os
from unittest.mock import patch

import pytest


# ── codeagent_flags ─────────────────────────────────────────────────────────


def test_flags_module_imports():
    from src.config import codeagent_flags
    assert hasattr(codeagent_flags, "PROVIDER_PROMPTS")
    assert hasattr(codeagent_flags, "TOOL_HINTS")
    assert hasattr(codeagent_flags, "DESTRUCTIVE_CONFIRM")


def test_flag_defaults_opt_out():
    """Tous les flags doivent être opt-OUT (default=True) sauf DESTRUCTIVE_CONFIRM."""
    # Nettoyage des env vars avant import
    keys_to_clear = [
        "LUMENA_PROVIDER_PROMPTS", "LUMENA_TOOL_HINTS", "LUMENA_FUZZY_REPLACE",
        "LUMENA_COMPACTION_PRUNE", "LUMENA_PLAN_MODE", "LUMENA_TRUNCATION_SAVE",
        "LUMENA_MAX_STEPS_GRACEFUL", "LUMENA_AUTO_FORMAT", "LUMENA_REACT_QUALITY_GATES",
        "LUMENA_DID_YOU_MEAN", "LUMENA_MODEL_TEMPERATURES", "LUMENA_COMPACTION_REPLAY",
        "LUMENA_INVALID_TOOL_CATCH", "LUMENA_CRLF_NORMALIZE", "LUMENA_ENV_CONTEXT",
        "LUMENA_SSE_TIMEOUT", "LUMENA_PROMPT_CACHE", "LUMENA_CODING_METRICS",
        "LUMENA_DESTRUCTIVE_CONFIRM", "LUMENA_FRENCH_ERRORS",
    ]
    env_clean = {k: v for k, v in os.environ.items() if k not in keys_to_clear}
    with patch.dict(os.environ, env_clean, clear=True):
        from src.config import codeagent_flags as mod
        importlib.reload(mod)
        # Tous les flags opt-OUT True par défaut
        assert mod.PROVIDER_PROMPTS is True
        assert mod.TOOL_HINTS is True
        assert mod.FUZZY_REPLACE is True
        assert mod.COMPACTION_PRUNE is True
        assert mod.REACT_QUALITY_GATES is True
        assert mod.FRENCH_ERRORS is True
        # Seul flag opt-IN
        assert mod.DESTRUCTIVE_CONFIRM is False


@pytest.mark.parametrize("falsy", ["0", "false", "no", "off", "False", "OFF"])
def test_flag_can_be_disabled(falsy):
    with patch.dict(os.environ, {"LUMENA_PROVIDER_PROMPTS": falsy}):
        from src.config import codeagent_flags as mod
        importlib.reload(mod)
        assert mod.PROVIDER_PROMPTS is False


@pytest.mark.parametrize("truthy", ["1", "true", "yes", "on", "True"])
def test_destructive_confirm_can_be_enabled(truthy):
    with patch.dict(os.environ, {"LUMENA_DESTRUCTIVE_CONFIRM": truthy}):
        from src.config import codeagent_flags as mod
        importlib.reload(mod)
        assert mod.DESTRUCTIVE_CONFIRM is True


# ── _load_provider_prompt ───────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_loader_cache():
    """Fixture vide — @lru_cache retiré de _load_provider_prompt, aucun reset nécessaire."""
    yield


@pytest.mark.parametrize(
    "model,expected_marker",
    [
        ("deepseek-v3", "REASONER"),
        ("deepseek-reasoner", "REASONER"),
        ("claude-3-5-sonnet", "professionnel"),
        ("claude-opus-4", "professionnel"),
        ("anthropic/claude-3", "professionnel"),
        ("gpt-4o", "WORKFLOW"),
        ("gpt-4.1", "WORKFLOW"),
        ("o3-mini", "WORKFLOW"),
        ("o4-mini", "WORKFLOW"),
        ("gemini-2.0-flash", "GEMINI"),
        ("gemini-pro", "GEMINI"),
    ],
)
def test_provider_prompt_routing(model, expected_marker):
    from src.prompts.agents.sub_agent_prompts import _load_provider_prompt
    content = _load_provider_prompt(model)
    assert content, f"prompt vide pour {model}"
    assert expected_marker.lower() in content.lower(), (
        f"marker '{expected_marker}' absent du prompt pour {model}"
    )


def test_provider_prompt_unknown_falls_back_to_default():
    from src.prompts.agents.sub_agent_prompts import _load_provider_prompt
    content = _load_provider_prompt("some-random-model-xyz")
    assert content
    # Le default.txt doit contenir les sections génériques
    assert "PERSÉVÉRANCE" in content or "RÈGLES ABSOLUES" in content


def test_provider_prompt_empty_model_returns_empty():
    from src.prompts.agents.sub_agent_prompts import _load_provider_prompt
    assert _load_provider_prompt("") == ""


def test_provider_prompt_disabled_by_flag():
    """Si flag PROVIDER_PROMPTS=False, le loader retourne ''."""
    import importlib as _il
    from src.config import codeagent_flags as flags_mod
    try:
        with patch.dict(os.environ, {"LUMENA_PROVIDER_PROMPTS": "0"}):
            _il.reload(flags_mod)
            from src.prompts.agents.sub_agent_prompts import _load_provider_prompt
            # @lru_cache retiré — pas de cache_clear() nécessaire
            assert _load_provider_prompt("deepseek-v3") == ""
    finally:
        # Toujours restaurer, même si le test échoue
        with patch.dict(os.environ, {"LUMENA_PROVIDER_PROMPTS": "1"}):
            _il.reload(flags_mod)


# ── Intégration _build_system_prompt ────────────────────────────────────────


def test_build_system_prompt_with_model_prepends_provider_prefix():
    from src.agents.sub_agent import _build_system_prompt
    prompt = _build_system_prompt(
        "créer une page", workspace_files=None, mode="create",
        model_name="deepseek-v3",
    )
    assert "PROVIDER-SPECIFIC" in prompt
    assert "CORE INSTRUCTIONS" in prompt
    # Le contenu provider doit apparaître AVANT le prompt core
    idx_provider = prompt.find("PROVIDER-SPECIFIC")
    idx_core = prompt.find("CORE INSTRUCTIONS")
    assert idx_provider < idx_core


def test_build_system_prompt_no_model_keeps_legacy_behavior():
    """Sans model_name, comportement legacy 100% identique (hors ENV_CONTEXT optionnel)."""
    from src.agents.sub_agent import _build_system_prompt
    prompt = _build_system_prompt("créer une page", workspace_files=None, mode="create")
    assert "PROVIDER-SPECIFIC" not in prompt
    # Le prompt commence par "Tu es CodeAgent" (ou par ENV_CONTEXT, puis CodeAgent)
    assert "Tu es CodeAgent" in prompt[:2000]


def test_build_system_prompt_backward_compat_kwargs():
    """Vérifie que la signature reste backward-compatible (kwargs only)."""
    from src.agents.sub_agent import _build_system_prompt
    # Appel positional 2-arg (legacy) doit toujours fonctionner
    p1 = _build_system_prompt("test", None)
    assert p1
    # Appel avec mode kwarg
    p2 = _build_system_prompt("test", None, mode="create")
    assert p2
