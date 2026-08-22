"""LOT 1 clôture — M1 : stubs JS VALIDES (run MiniQuiz 2026-07-06).

Le contrat MiniQuiz portait des signatures hybrides (`function load_question()
-> void`, `submit_answer(answer: str)`) que `_js_stub` copiait littéralement en
tête d'un bloc « SIGNATURE FIGÉE — NE PAS MODIFIER » : le CodeAgent remplissait
les corps en GARDANT la signature invalide → `node --check static/script.js`
rouge (vérifié sur disque), livrable web mort-né.

M1 = `_normalize_js_signature` (helper pur) + branche JS de `validate_contract`
(nom nu refusé, erreur guidante) + `_js_stub` émet la forme normalisée.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.subagents.mission_contract import (
    _js_stub,
    _normalize_js_signature,
    generate_stub,
    validate_contract,
)


# ── _normalize_js_signature (pur) ───────────────────────────────────────────────

def test_normalize_miniquiz_hybrid_signatures():
    """Les DEUX signatures exactes du run MiniQuiz."""
    assert _normalize_js_signature("function load_question() -> void") == \
        "function load_question()"
    assert _normalize_js_signature("function submit_answer(answer: str) -> void") == \
        "function submit_answer(answer)"


def test_normalize_already_valid_unchanged():
    assert _normalize_js_signature("function load_question()") == "function load_question()"
    assert _normalize_js_signature("function f(a, b)") == "function f(a, b)"
    assert _normalize_js_signature("async function poll(url)") == "async function poll(url)"


def test_normalize_adds_function_prefix_and_keeps_defaults():
    assert _normalize_js_signature("submit_answer(answer)") == "function submit_answer(answer)"
    assert _normalize_js_signature("retry(n: int = 3)") == "function retry(n = 3)"


def test_normalize_impossible_forms_rejected():
    assert _normalize_js_signature("load_question") == ""      # nom nu
    assert _normalize_js_signature("") == ""
    assert _normalize_js_signature("if (x) {}") == ""


def test_normalize_arrow_form_kept():
    s = "const load = (url) =>"
    assert _normalize_js_signature(s) == s


# ── _js_stub émet du JS valide ─────────────────────────────────────────────────

_MINIQUIZ_JS_ENTRY = {
    "path": "static/script.js",
    "owner": "w_frontend",
    "desc": "JS: load_question() fetch, submit_answer(answer) POST",
    "api": [
        "function load_question() -> void",
        "function submit_answer(answer: str) -> void",
    ],
}


def test_js_stub_no_hybrid_leftovers():
    stub = _js_stub(_MINIQUIZ_JS_ENTRY)
    assert "-> void" not in stub
    assert ": str" not in stub
    assert "function load_question() {" in stub
    assert "function submit_answer(answer) {" in stub


@pytest.mark.skipif(shutil.which("node") is None, reason="node absent")
def test_js_stub_passes_node_check(tmp_path):
    """Le critère de sortie M1 du plan : le stub MiniQuiz passe `node --check`."""
    stub = generate_stub(_MINIQUIZ_JS_ENTRY)
    f = tmp_path / "script.js"
    f.write_text(stub, encoding="utf-8")
    r = subprocess.run(
        ["node", "--check", str(f)], capture_output=True, text=True, timeout=30,
        shell=False,
    )
    assert r.returncode == 0, f"node --check rouge sur le stub généré:\n{r.stderr}\n---\n{stub}"


# ── validate_contract : branche JS ─────────────────────────────────────────────

def _contract_with_js(exports):
    return {
        "project": "MiniQuiz",
        "files": [{
            "path": "static/script.js", "owner": "w_frontend",
            "desc": "frontend JS", "api": list(exports),
        }],
    }


def test_validate_js_bare_name_refused_with_guidance():
    errors = validate_contract(_contract_with_js(["load_question"]))
    assert errors, "un nom nu JS doit être refusé"
    joined = " ".join(errors)
    assert "function" in joined  # l'erreur guide vers une vraie signature


def test_validate_js_hybrid_forms_accepted():
    """Les formes hybrides sont NORMALISABLES → pas d'erreur (pas de retry lead)."""
    errors = validate_contract(_contract_with_js([
        "function load_question() -> void",
        "submit_answer(answer: str)",
    ]))
    assert errors == []


def test_validate_py_rules_unchanged():
    """Non-régression F.1 : la branche .py garde son comportement exact."""
    data = {"files": [{"path": "app.py", "owner": "w", "api": ["get_all"]}]}
    errors = validate_contract(data)
    assert any("signatures" in e for e in errors)
    data_ok = {"files": [{"path": "app.py", "owner": "w",
                          "api": ["def create_app() -> Flask"]}]}
    assert validate_contract(data_ok) == []
