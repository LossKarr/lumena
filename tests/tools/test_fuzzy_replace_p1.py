"""Tests P1 — fuzzy_replace module (8-pass matching)."""
from __future__ import annotations

import pytest


# ── Pass 1 : exact ──────────────────────────────────────────────────────────


def test_exact_match():
    from src.tools.fuzzy_replace import fuzzy_replace
    content = "def foo():\n    return 1\n"
    m = fuzzy_replace(content, "return 1", "return 2")
    assert m is not None
    assert m.method == "exact"
    assert "return 2" in m.new_content
    assert "return 1" not in m.new_content


def test_exact_match_always_works_even_flag_off(monkeypatch):
    monkeypatch.setenv("LUMENA_FUZZY_REPLACE", "0")
    import importlib, src.config.codeagent_flags as flags_mod
    importlib.reload(flags_mod)
    from src.tools.fuzzy_replace import fuzzy_replace
    m = fuzzy_replace("hello world", "world", "everyone")
    assert m is not None and m.method == "exact"
    monkeypatch.delenv("LUMENA_FUZZY_REPLACE", raising=False)
    importlib.reload(flags_mod)


def test_fuzzy_passes_disabled_when_flag_off(monkeypatch):
    monkeypatch.setenv("LUMENA_FUZZY_REPLACE", "0")
    import importlib, src.config.codeagent_flags as flags_mod
    importlib.reload(flags_mod)
    from src.tools.fuzzy_replace import fuzzy_replace
    # CRLF pattern qui aurait matché en flag on
    m = fuzzy_replace("hello\r\nworld", "hello\nworld", "hello\neveryone")
    assert m is None
    monkeypatch.delenv("LUMENA_FUZZY_REPLACE", raising=False)
    importlib.reload(flags_mod)


# ── Pass 2 : CRLF ──────────────────────────────────────────────────────────


def test_crlf_normalization():
    from src.tools.fuzzy_replace import fuzzy_replace
    content = "line1\r\nline2\r\nline3"
    m = fuzzy_replace(content, "line1\nline2", "new1\nnew2")
    assert m is not None
    assert m.method == "crlf"
    assert "new1\nnew2" in m.new_content


# ── Pass 3 : rstrip ────────────────────────────────────────────────────────


def test_rstrip_trailing_whitespace():
    from src.tools.fuzzy_replace import fuzzy_replace
    content = "def foo():   \n    return 1   \n"
    m = fuzzy_replace(content, "def foo():\n    return 1", "def foo():\n    return 2")
    assert m is not None
    assert m.method in ("rstrip", "strip", "full")  # rstrip-family
    assert "return 2" in m.new_content


# ── Pass 4 : strip ─────────────────────────────────────────────────────────


def test_strip_both_sides():
    from src.tools.fuzzy_replace import fuzzy_replace
    content = "    def foo():\n        return 1\n"
    m = fuzzy_replace(content, "def foo():\n    return 1", "def bar():\n    return 2")
    assert m is not None
    # peut être strip, indent ou full
    assert m.method in ("strip", "indent", "full")


# ── Pass 5 : punct Unicode ─────────────────────────────────────────────────


def test_smart_quotes_normalization():
    from src.tools.fuzzy_replace import fuzzy_replace
    content = 'print(\u201chello\u201d)'  # smart quotes
    m = fuzzy_replace(content, 'print("hello")', 'print("world")')
    assert m is not None
    assert m.method in ("punct", "full")
    assert "world" in m.new_content


def test_unicode_dash_normalization():
    from src.tools.fuzzy_replace import fuzzy_replace
    content = "price: 10\u201320 USD"  # en-dash
    m = fuzzy_replace(content, "price: 10-20 USD", "price: 15-25 USD")
    assert m is not None
    assert m.method in ("punct", "full")


# ── Pass 6 : collapse_ws ───────────────────────────────────────────────────


def test_collapse_multiple_spaces():
    from src.tools.fuzzy_replace import fuzzy_replace
    content = "if    x    ==    1:"
    m = fuzzy_replace(content, "if x == 1:", "if x == 2:")
    assert m is not None
    assert m.method in ("collapse_ws", "full")


# ── Pass 8 : indent ────────────────────────────────────────────────────────


def test_indent_tolerant_match():
    from src.tools.fuzzy_replace import fuzzy_replace
    content = "        for i in range(10):\n            print(i)\n"
    m = fuzzy_replace(content, "for i in range(10):\n    print(i)", "for i in range(5):\n    print(i)")
    assert m is not None
    # strip ou indent
    assert m.method in ("strip", "indent", "full")


# ── Edge cases ─────────────────────────────────────────────────────────────


def test_empty_old_str_returns_none():
    from src.tools.fuzzy_replace import fuzzy_replace
    assert fuzzy_replace("content", "", "new") is None


def test_empty_content_returns_none():
    from src.tools.fuzzy_replace import fuzzy_replace
    assert fuzzy_replace("", "old", "new") is None


def test_no_match_returns_none():
    from src.tools.fuzzy_replace import fuzzy_replace
    m = fuzzy_replace("def foo():\n    return 1\n", "def completely_unrelated():", "x")
    assert m is None


def test_replaces_only_first_occurrence():
    from src.tools.fuzzy_replace import fuzzy_replace
    content = "a = 1\nb = 1\nc = 1"
    m = fuzzy_replace(content, "= 1", "= 2")
    assert m is not None
    # Seule la première occurrence doit être remplacée
    assert m.new_content.count("= 2") == 1
    assert m.new_content.count("= 1") == 2


def test_new_str_can_be_empty():
    """Suppression via new_str vide."""
    from src.tools.fuzzy_replace import fuzzy_replace
    content = "keep\nDELETE_ME\nkeep"
    m = fuzzy_replace(content, "DELETE_ME\n", "")
    assert m is not None
    assert "DELETE_ME" not in m.new_content


def test_fuzzymatch_namedtuple_structure():
    from src.tools.fuzzy_replace import fuzzy_replace, FuzzyMatch
    m = fuzzy_replace("hello", "hello", "world")
    assert isinstance(m, FuzzyMatch)
    assert hasattr(m, "new_content")
    assert hasattr(m, "method")
    assert hasattr(m, "matched_text")


# ── Intégration sub_agent.py ───────────────────────────────────────────────


def test_fuzzy_replace_imported_by_sub_agent():
    """Le module est importable depuis sub_agent (wire-up P1 correct)."""
    src = open("src/agents/sub_agent.py", encoding="utf-8").read()
    assert "from src.tools.fuzzy_replace import fuzzy_replace" in src
    assert "fuzzy_replace 8-pass" in src  # message d'erreur mentionne fuzzy


def test_fuzzy_replace_handles_real_crlf_to_lf_case():
    """Cas réel : contenu CRLF sur disque, pattern LF du LLM."""
    from src.tools.fuzzy_replace import fuzzy_replace
    content_crlf = "def foo():\r\n    return 1\r\n"
    pattern_lf = "def foo():\n    return 1"
    m = fuzzy_replace(content_crlf, pattern_lf, "def foo():\n    return 42")
    assert m is not None
    assert "return 42" in m.new_content
