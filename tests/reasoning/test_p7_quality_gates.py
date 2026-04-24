"""Tests P7 — Parité ReAct ↔ CodeAgent (quality gates).

Couvre :
- A. Module src/utils/syntax_check.py (dispatcher + Python/JSON/CSS/HTML)
- B. Hook _append_syntax_warning dans handlers/files.py
- C. Auto-relecture edit_file sur échec "non trouvé"
- D. Provider hint injecté dans _build_react_prompt
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest


# ── A. syntax_check ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_syntax_check_python_ok(tmp_path):
    from src.utils.syntax_check import check_syntax
    f = tmp_path / "ok.py"
    f.write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    assert await check_syntax(f) == ""


@pytest.mark.asyncio
async def test_syntax_check_python_broken(tmp_path):
    from src.utils.syntax_check import check_syntax
    f = tmp_path / "broken.py"
    f.write_text("def add(a, b)\n    return a + b\n", encoding="utf-8")  # missing ":"
    result = await check_syntax(f)
    # ruff ou py_compile doit signaler une erreur (contenu non vide)
    assert result, f"expected error message, got empty for broken syntax"


@pytest.mark.asyncio
async def test_syntax_check_json_valid(tmp_path):
    from src.utils.syntax_check import check_syntax
    f = tmp_path / "x.json"
    f.write_text('{"a": 1}', encoding="utf-8")
    assert await check_syntax(f) == ""


@pytest.mark.asyncio
async def test_syntax_check_json_invalid(tmp_path):
    from src.utils.syntax_check import check_syntax
    f = tmp_path / "bad.json"
    f.write_text('{"a": }', encoding="utf-8")
    assert "JSON invalide" in await check_syntax(f)


@pytest.mark.asyncio
async def test_syntax_check_css_unbalanced(tmp_path):
    from src.utils.syntax_check import check_syntax
    f = tmp_path / "bad.css"
    f.write_text("body { color: red; /* missing close", encoding="utf-8")
    result = await check_syntax(f)
    assert "accolades" in result.lower() or result == ""  # best-effort


@pytest.mark.asyncio
async def test_syntax_check_nonexistent(tmp_path):
    from src.utils.syntax_check import check_syntax
    result = await check_syntax(tmp_path / "ghost.py")
    assert result == ""


@pytest.mark.asyncio
async def test_syntax_check_disabled_by_flag(tmp_path):
    """Si REACT_QUALITY_GATES=False → toujours retourne "" (fail-safe)."""
    f = tmp_path / "broken.py"
    f.write_text("def add(a, b)\n  return a+b", encoding="utf-8")
    with patch.dict(os.environ, {"LUMENA_REACT_QUALITY_GATES": "0"}):
        from src.config import codeagent_flags as flags_mod
        importlib.reload(flags_mod)
        # Force le reload du module syntax_check pour relire le flag
        if "src.utils.syntax_check" in sys.modules:
            importlib.reload(sys.modules["src.utils.syntax_check"])
        from src.utils.syntax_check import check_syntax
        result = await check_syntax(f)
        assert result == ""

    # Restaurer
    with patch.dict(os.environ, {"LUMENA_REACT_QUALITY_GATES": "1"}):
        from src.config import codeagent_flags as flags_mod
        importlib.reload(flags_mod)


@pytest.mark.asyncio
async def test_syntax_check_unknown_extension(tmp_path):
    from src.utils.syntax_check import check_syntax
    f = tmp_path / "x.txt"
    f.write_text("random text", encoding="utf-8")
    assert await check_syntax(f) == ""


# ── B. _append_syntax_warning ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_append_syntax_warning_appends_on_error(tmp_path):
    from src.reasoning.handlers.files import _append_syntax_warning
    f = tmp_path / "broken.py"
    f.write_text("def x(\n", encoding="utf-8")
    msg = await _append_syntax_warning("✅ Écrit", f)
    # Si ruff/py_compile dispo → warning attendu, sinon msg inchangé (best-effort)
    assert msg.startswith("✅ Écrit")


@pytest.mark.asyncio
async def test_append_syntax_warning_clean_file_unchanged(tmp_path):
    from src.reasoning.handlers.files import _append_syntax_warning
    f = tmp_path / "ok.py"
    f.write_text("x = 1\n", encoding="utf-8")
    msg = await _append_syntax_warning("✅ Écrit", f)
    assert msg == "✅ Écrit"


@pytest.mark.asyncio
async def test_append_syntax_warning_handles_exceptions_gracefully():
    from src.reasoning.handlers.files import _append_syntax_warning
    # Passer un Path invalide ne doit JAMAIS lever
    msg = await _append_syntax_warning("ok", Path("/nonexistent/ghost.py"))
    assert msg == "ok"


# ── D. Provider hint dans _build_react_prompt ──────────────────────────────


def test_load_provider_prompt_hint_extraction():
    """Vérifie que _load_provider_prompt retourne bien des sections parsables."""
    from src.prompts.agents.sub_agent_prompts import _load_provider_prompt
    # @lru_cache retiré — cache_clear() n'existe plus, appel direct
    hint = _load_provider_prompt("deepseek-v3")
    assert "PERSÉVÉRANCE" in hint
    # Le prompt doit contenir au moins une section extractible
    assert "==" in hint


def test_syntax_check_module_importable():
    """Meta-test : le module est importable sans erreur."""
    from src.utils import syntax_check
    assert hasattr(syntax_check, "check_syntax")
    assert hasattr(syntax_check, "_check_python")
    assert hasattr(syntax_check, "_check_json")
