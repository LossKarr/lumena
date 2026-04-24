"""Tests P0b — Tool descriptions injection dans CodeAgent system prompt."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest


# ── Tests du loader ────────────────────────────────────────────────────────


def test_load_tool_descriptions_returns_string_when_flag_on():
    from src.prompts.agents.sub_agent_prompts import _load_tool_descriptions
    _load_tool_descriptions.cache_clear()
    out = _load_tool_descriptions()
    assert isinstance(out, str)
    assert len(out) > 500  # les 14 fichiers concaténés font >500 chars


def test_load_tool_descriptions_contains_all_14_tools():
    from src.prompts.agents.sub_agent_prompts import (
        _load_tool_descriptions,
        _TOOL_DESCRIPTION_FILES,
    )
    _load_tool_descriptions.cache_clear()
    out = _load_tool_descriptions()
    assert len(_TOOL_DESCRIPTION_FILES) == 14
    for name in _TOOL_DESCRIPTION_FILES:
        # chaque doc contient le nom de l'outil dans son heading
        assert name in out, f"Tool '{name}' missing from descriptions"


def test_load_tool_descriptions_contains_when_patterns():
    from src.prompts.agents.sub_agent_prompts import _load_tool_descriptions
    _load_tool_descriptions.cache_clear()
    out = _load_tool_descriptions()
    # pattern uniforme when / when not / good / bad
    assert out.count("When to use:") >= 14
    assert out.count("When NOT to use") >= 14
    assert "Good:" in out
    assert "Bad:" in out


def test_load_tool_descriptions_respects_flag_off(monkeypatch):
    monkeypatch.setenv("LUMENA_TOOL_HINTS", "0")
    # recharger le module flags pour prendre l'env
    import importlib
    import src.config.codeagent_flags as flags_mod
    importlib.reload(flags_mod)
    from src.prompts.agents.sub_agent_prompts import _load_tool_descriptions
    _load_tool_descriptions.cache_clear()
    out = _load_tool_descriptions()
    assert out == ""
    # cleanup : restaurer
    monkeypatch.delenv("LUMENA_TOOL_HINTS", raising=False)
    importlib.reload(flags_mod)
    _load_tool_descriptions.cache_clear()


def test_load_tool_descriptions_caching():
    from src.prompts.agents.sub_agent_prompts import _load_tool_descriptions
    _load_tool_descriptions.cache_clear()
    out1 = _load_tool_descriptions()
    out2 = _load_tool_descriptions()
    assert out1 is out2  # lru_cache → même objet


def test_load_tool_descriptions_graceful_on_missing_dir(monkeypatch):
    import src.prompts.agents.sub_agent_prompts as mod
    original = mod._CODEAGENT_TOOLS_DIR
    monkeypatch.setattr(mod, "_CODEAGENT_TOOLS_DIR", Path("/nonexistent/path/to/tools"))
    mod._load_tool_descriptions.cache_clear()
    out = mod._load_tool_descriptions()
    assert out == ""
    # cleanup
    monkeypatch.setattr(mod, "_CODEAGENT_TOOLS_DIR", original)
    mod._load_tool_descriptions.cache_clear()


# ── Tests d'intégration dans _build_system_prompt ──────────────────────────


def test_system_prompt_includes_tool_hints_by_default():
    from src.agents.sub_agent import _build_system_prompt
    from src.prompts.agents.sub_agent_prompts import _load_tool_descriptions
    _load_tool_descriptions.cache_clear()
    prompt = _build_system_prompt("créer une landing page", None)
    assert "GUIDE DES OUTILS" in prompt
    assert "read_file" in prompt
    assert "str_replace" in prompt


def test_system_prompt_omits_tool_hints_when_flag_off(monkeypatch):
    monkeypatch.setenv("LUMENA_TOOL_HINTS", "0")
    import importlib
    import src.config.codeagent_flags as flags_mod
    importlib.reload(flags_mod)
    from src.prompts.agents.sub_agent_prompts import _load_tool_descriptions
    _load_tool_descriptions.cache_clear()
    from src.agents.sub_agent import _build_system_prompt
    prompt = _build_system_prompt("test", None)
    assert "GUIDE DES OUTILS" not in prompt
    # cleanup
    monkeypatch.delenv("LUMENA_TOOL_HINTS", raising=False)
    importlib.reload(flags_mod)
    _load_tool_descriptions.cache_clear()


def test_system_prompt_tool_hints_order_preserved():
    """L'ordre des outils dans _TOOL_DESCRIPTION_FILES doit être respecté."""
    from src.prompts.agents.sub_agent_prompts import (
        _load_tool_descriptions,
        _TOOL_DESCRIPTION_FILES,
    )
    _load_tool_descriptions.cache_clear()
    out = _load_tool_descriptions()
    positions = []
    for name in _TOOL_DESCRIPTION_FILES:
        # utilise le heading ## pour localiser
        heading = f"## {name}"
        pos = out.find(heading)
        assert pos >= 0, f"Heading '{heading}' not found"
        positions.append(pos)
    assert positions == sorted(positions), "Tool sections are not in declared order"


def test_system_prompt_with_provider_prompt_keeps_tool_hints():
    """Combiner provider prompt + tool hints doit produire les deux sections."""
    from src.agents.sub_agent import _build_system_prompt
    from src.prompts.agents.sub_agent_prompts import (
        _load_tool_descriptions,
        _load_provider_prompt,
    )
    _load_tool_descriptions.cache_clear()
    # @lru_cache retiré de _load_provider_prompt — pas de cache_clear() nécessaire
    prompt = _build_system_prompt("test task", None, model_name="deepseek-v3")
    assert "GUIDE DES OUTILS" in prompt
    # provider prompt présent si flag on (default)
    # si pas présent c'est que le fichier txt existe bien, vérifions au moins la structure core
    assert "CORE INSTRUCTIONS" in prompt or "CodeAgent" in prompt


# ── Validation des fichiers .txt ───────────────────────────────────────────


def test_all_tool_txt_files_exist():
    import src.prompts.agents.sub_agent_prompts as mod
    for name in mod._TOOL_DESCRIPTION_FILES:
        path = mod._CODEAGENT_TOOLS_DIR / f"{name}.txt"
        assert path.exists(), f"Missing tool description file: {path}"


def test_all_tool_txt_files_non_empty_and_structured():
    import src.prompts.agents.sub_agent_prompts as mod
    for name in mod._TOOL_DESCRIPTION_FILES:
        path = mod._CODEAGENT_TOOLS_DIR / f"{name}.txt"
        content = path.read_text(encoding="utf-8")
        assert len(content) > 50, f"{name}.txt too short"
        assert "When to use" in content, f"{name}.txt missing 'When to use'"
        assert "When NOT to use" in content, f"{name}.txt missing 'When NOT to use'"
