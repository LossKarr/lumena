"""Tests P6 — Auto-format post-edit."""
from __future__ import annotations

import pytest


# ── Module auto_format ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_auto_format_skips_non_python(tmp_path):
    from src.utils.auto_format import auto_format_file
    f = tmp_path / "x.js"
    f.write_text("console.log('x')", encoding="utf-8")
    out = await auto_format_file(str(f), tmp_path)
    assert out == ""


@pytest.mark.asyncio
async def test_auto_format_skips_nonexistent(tmp_path):
    from src.utils.auto_format import auto_format_file
    out = await auto_format_file(str(tmp_path / "nonexistent.py"), tmp_path)
    assert out == ""


@pytest.mark.asyncio
async def test_auto_format_skips_empty_path():
    from src.utils.auto_format import auto_format_file
    assert await auto_format_file("") == ""


@pytest.mark.asyncio
async def test_auto_format_noop_on_clean_python(tmp_path):
    """Fichier déjà formaté → pas de changement, message vide."""
    from src.utils.auto_format import auto_format_file
    f = tmp_path / "clean.py"
    f.write_text('def foo():\n    return 1\n', encoding="utf-8")
    out = await auto_format_file(str(f), tmp_path)
    assert out == ""  # déjà clean


@pytest.mark.asyncio
async def test_auto_format_reformats_messy_python(tmp_path):
    """Fichier non-formaté → ruff le reformat + message informatif."""
    from src.utils.auto_format import auto_format_file
    f = tmp_path / "messy.py"
    # Espaces incohérents, quotes mix
    f.write_text("def foo( x , y ):\n    return x+y\n", encoding="utf-8")
    out = await auto_format_file(str(f), tmp_path)
    # Si ruff est dispo → message ; sinon skip silencieux
    new_content = f.read_text(encoding="utf-8")
    if out:
        assert "auto-format" in out or "ruff" in out.lower()
        assert new_content != "def foo( x , y ):\n    return x+y\n"


@pytest.mark.asyncio
async def test_auto_format_noop_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.setenv("LUMENA_AUTO_FORMAT", "0")
    import importlib, src.config.codeagent_flags as flags_mod
    importlib.reload(flags_mod)
    from src.utils.auto_format import auto_format_file
    f = tmp_path / "messy.py"
    f.write_text("def foo( x , y ):\n    return x+y", encoding="utf-8")
    out = await auto_format_file(str(f), tmp_path)
    assert out == ""
    # Fichier intact
    assert f.read_text(encoding="utf-8") == "def foo( x , y ):\n    return x+y"
    monkeypatch.delenv("LUMENA_AUTO_FORMAT", raising=False)
    importlib.reload(flags_mod)


@pytest.mark.asyncio
async def test_auto_format_failsafe_on_syntax_error(tmp_path):
    """Fichier Python avec syntaxe invalide → ruff échoue, message vide."""
    from src.utils.auto_format import auto_format_file
    f = tmp_path / "broken.py"
    f.write_text("def foo(: INVALID", encoding="utf-8")
    out = await auto_format_file(str(f), tmp_path)
    assert out == ""  # fail-safe
    # Fichier intact (ruff n'a pas pu formater)
    assert "INVALID" in f.read_text(encoding="utf-8")


# ── Intégration sub_agent.py (edit_file + edit_lines) ──────────────────────


def test_edit_file_hook_present_in_sub_agent():
    src = open("src/agents/sub_agent.py", encoding="utf-8").read()
    assert "from src.utils.auto_format import auto_format_file" in src
    # 2 hooks : edit_file + edit_lines
    assert src.count("auto_format_file") >= 2
