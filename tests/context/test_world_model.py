"""Tests du WorldModel : modèle mental vivant des fichiers pendant une session agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.context.world_model import (
    WorldModel,
    FileModel,
    Section,
    get_world_model,
    reset_world_model,
    _parse_python,
    _parse_css,
    _parse_html,
    _parse_js,
)


# ── Parsers unitaires ───────────────────────────────────────────────────────


def test_parse_python_extracts_classes_and_methods():
    code = '''\
import os

def top_level():
    return 1

class Foo:
    def bar(self):
        return 2

    async def async_bar(self):
        return 3

async def top_async():
    return 4
'''
    secs = _parse_python(code)
    names = [s.name for s in secs]
    assert "def top_level" in names
    assert "class Foo" in names
    assert "  Foo.bar" in names
    assert "  Foo.async_bar" in names
    assert "def top_async" in names
    # Kinds corrects
    kinds = {s.name: s.kind for s in secs}
    assert kinds["def top_async"] == "async_function"
    assert kinds["  Foo.async_bar"] == "method"


def test_parse_python_syntax_error_returns_empty():
    assert _parse_python("def bad(:\n  pass") == []


def test_parse_css_detects_selectors_media_and_comments():
    code = """\
/* ===== Header ===== */
.header { color: red; }

#hero {
    padding: 20px;
}

/* ===== Responsive ===== */
@media (max-width: 768px) {
    .header { padding: 10px; }
}

.footer { background: black; }
"""
    secs = _parse_css(code)
    names = [s.name for s in secs]
    # Commentaires
    assert any("Header" in n for n in names)
    assert any("Responsive" in n for n in names)
    # Sélecteurs top-level (pas celui imbriqué dans @media)
    selector_names = [s.name for s in secs if s.kind == "selector"]
    assert ".header" in selector_names
    assert "#hero" in selector_names
    assert ".footer" in selector_names
    # @media détecté
    assert any(s.kind == "media" for s in secs)


def test_parse_css_nested_rules_not_duplicated_as_top_level():
    code = """\
@media (max-width: 768px) {
    .inner { color: red; }
    .nested { padding: 0; }
}
.outer { color: blue; }
"""
    secs = _parse_css(code)
    top_selectors = [s.name for s in secs if s.kind == "selector"]
    assert ".outer" in top_selectors
    # .inner/.nested ne doivent PAS apparaître comme top-level selector
    assert ".inner" not in top_selectors
    assert ".nested" not in top_selectors


def test_parse_html_detects_sections_and_headings():
    code = """\
<!DOCTYPE html>
<html>
<body>
    <header id="main-header">
        <h1>Title</h1>
    </header>
    <main>
        <section class="hero primary">
            <h2>Subtitle</h2>
        </section>
    </main>
    <footer>Footer</footer>
</body>
</html>
"""
    secs = _parse_html(code)
    names = [s.name for s in secs]
    assert "<header#main-header>" in names
    assert "<main>" in names
    assert "<section.hero>" in names
    assert "<footer>" in names
    # Headings
    assert any("Title" in n for n in names)
    assert any("Subtitle" in n for n in names)


def test_parse_js_detects_functions_and_classes():
    code = """\
export function foo() { return 1; }
class Bar {}
const baz = async (x) => x + 1;
function unused() {}
"""
    secs = _parse_js(code)
    names = [s.name for s in secs]
    assert "function foo" in names
    assert "class Bar" in names
    assert "function baz" in names
    assert "function unused" in names


# ── WorldModel API ──────────────────────────────────────────────────────────


@pytest.fixture
def tmp_ws(tmp_path: Path) -> Path:
    return tmp_path


def test_update_from_write_marks_new_sections_with_iter(tmp_ws: Path):
    wm = WorldModel(tmp_ws)
    css = ".header { color: red; }\n#hero { padding: 10px; }\n"
    fm = wm.update_from_write("style.css", css, iter_num=2)
    assert fm.language == "css"
    assert fm.last_edit_iter == 2
    assert fm.last_action == "write_file"
    assert fm.version == 1
    assert all(s.added_at_iter == 2 for s in fm.sections)
    assert fm.total_lines == 2


def test_update_from_edit_with_new_content_reparses_and_flags_new_sections(tmp_ws: Path):
    wm = WorldModel(tmp_ws)
    wm.update_from_write("style.css", ".header {color:red;}\n", iter_num=1)
    # Ajoute une section
    after = ".header {color:red;}\n.new-section {color:blue;}\n"
    fm = wm.update_from_edit("style.css", iter_num=5, content_after=after,
                             action="str_replace")
    assert fm.version == 2
    assert fm.last_edit_iter == 5
    assert fm.last_action == "str_replace"
    # La section .header garde son iter=1, la nouvelle est iter=5
    by_name = {s.name: s for s in fm.sections}
    assert by_name[".header"].added_at_iter == 1
    assert by_name[".new-section"].added_at_iter == 5


def test_update_from_edit_without_content_marks_stale(tmp_ws: Path):
    wm = WorldModel(tmp_ws)
    wm.update_from_write("style.css", ".header {}\n", iter_num=1)
    fm = wm.update_from_edit("style.css", iter_num=4, action="edit_lines")
    assert fm is not None
    assert fm.version == 2
    assert fm.last_edit_iter == 4
    # Sections inchangées (pas de reparse)
    assert [s.name for s in fm.sections] == [".header"]


def test_path_normalization_relative_and_absolute(tmp_ws: Path):
    wm = WorldModel(tmp_ws)
    abs_path = tmp_ws / "sub" / "file.py"
    abs_path.parent.mkdir(parents=True)
    abs_path.write_text("def x(): pass\n")
    wm.update_from_write(str(abs_path), "def x(): pass\n", iter_num=1)
    # Doit être stocké en relatif
    stored = list(wm._files.keys())
    assert any(k.endswith("sub/file.py") for k in stored)
    # Retrieval via absolu ou relatif
    assert wm.get_file(str(abs_path)) is not None
    assert wm.get_file("sub/file.py") is not None


def test_active_files_ordered_by_iter_desc(tmp_ws: Path):
    wm = WorldModel(tmp_ws)
    wm.update_from_write("a.py", "def a(): pass\n", iter_num=1)
    wm.update_from_write("b.py", "def b(): pass\n", iter_num=3)
    wm.update_from_write("c.py", "def c(): pass\n", iter_num=2)
    ordered = [f.path for f in wm.active_files()]
    assert ordered == ["b.py", "c.py", "a.py"]


def test_get_compact_respects_budget(tmp_ws: Path):
    wm = WorldModel(tmp_ws)
    for i in range(20):
        wm.update_from_write(f"f{i}.py", f"def f{i}(): pass\n", iter_num=i)
    txt = wm.get_compact(max_files=10, max_tokens=100)
    assert "WORLD MODEL" in txt
    # Budget limité doit tronquer
    assert len(txt) <= 100 * 4 + 200  # marge header


def test_get_compact_empty_returns_empty_string(tmp_ws: Path):
    wm = WorldModel(tmp_ws)
    assert wm.get_compact() == ""


def test_forget_and_clear(tmp_ws: Path):
    wm = WorldModel(tmp_ws)
    wm.update_from_write("a.py", "def a(): pass\n", iter_num=1)
    wm.update_from_write("b.py", "def b(): pass\n", iter_num=2)
    wm.forget("a.py")
    assert wm.get_file("a.py") is None
    assert wm.get_file("b.py") is not None
    wm.clear()
    assert wm.active_files() == []


def test_singleton_per_workspace(tmp_path: Path):
    ws1 = tmp_path / "ws1"
    ws2 = tmp_path / "ws2"
    ws1.mkdir()
    ws2.mkdir()
    reset_world_model()
    a = get_world_model(ws1)
    b = get_world_model(ws1)
    c = get_world_model(ws2)
    assert a is b
    assert a is not c
    reset_world_model(ws1)
    d = get_world_model(ws1)
    assert d is not a
    reset_world_model()


def test_file_model_to_compact_format(tmp_ws: Path):
    wm = WorldModel(tmp_ws)
    wm.update_from_write(
        "style.css",
        "/* === Header === */\n.header { color: red; }\n@media (max-width: 768px) { .x {} }\n",
        iter_num=3,
    )
    fm = wm.get_file("style.css")
    assert fm is not None
    txt = fm.to_compact()
    assert "style.css" in txt
    assert "iter 3" in txt
    assert "├─" in txt or "└─" in txt


def test_unknown_language_fallback(tmp_ws: Path):
    wm = WorldModel(tmp_ws)
    fm = wm.update_from_write("notes.txt", "hello world\nline two\n", iter_num=1)
    assert fm.language == "unknown"
    assert fm.sections == []
    assert fm.total_lines == 2


def test_compact_output_contains_new_section_iter_markers(tmp_ws: Path):
    wm = WorldModel(tmp_ws)
    wm.update_from_write("s.css", ".a{}\n", iter_num=1)
    wm.update_from_edit("s.css", iter_num=5, content_after=".a{}\n.b{}\n")
    txt = wm.get_compact()
    assert "[+ iter 5]" in txt


def test_version_increments_on_each_update(tmp_ws: Path):
    wm = WorldModel(tmp_ws)
    wm.update_from_write("x.py", "def a(): pass\n", iter_num=1)
    wm.update_from_edit("x.py", iter_num=2)
    wm.update_from_edit("x.py", iter_num=3, content_after="def a(): pass\ndef b(): pass\n")
    fm = wm.get_file("x.py")
    assert fm is not None
    assert fm.version == 3
