"""Tests pour insert_at_anchor (core + handler).

Vérifie l'insertion language-agnostic: HTML, Python, JS, Java, CSS, Go.
"""

import pytest
from pathlib import Path

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.files import (
    insert_at_anchor_core,
    insert_at_anchor_handler,
    get_file_handler_defs,
)


# ───────────────────────── Core (pure function) ─────────────────────────


def test_core_before_html():
    txt = "<html>\n<body>\n<main>hi</main>\n</body>\n</html>\n"
    out = insert_at_anchor_core(txt, anchor="</main>", content="<section>X</section>", position="before")
    assert "<section>X</section>" in out
    assert out.index("<section>X</section>") < out.index("</main>")


def test_core_after_python_comment():
    txt = "import os\n# END IMPORTS\n\ndef main():\n    pass\n"
    out = insert_at_anchor_core(txt, anchor="# END IMPORTS", content="from src.x import Y", position="after")
    assert "from src.x import Y" in out
    assert out.index("# END IMPORTS") < out.index("from src.x import Y")
    assert out.index("from src.x import Y") < out.index("def main()")


def test_core_replace():
    txt = "hello WORLD foo"
    out = insert_at_anchor_core(txt, anchor="WORLD", content="EARTH", position="replace")
    assert out == "hello EARTH foo"


def test_core_anchor_missing_raises():
    with pytest.raises(ValueError, match="introuvable"):
        insert_at_anchor_core("hello", anchor="</main>", content="x")


def test_core_invalid_position_raises():
    with pytest.raises(ValueError, match="position invalide"):
        insert_at_anchor_core("a b c", anchor="b", content="x", position="sideways")


def test_core_empty_anchor_raises():
    with pytest.raises(ValueError, match="vide"):
        insert_at_anchor_core("abc", anchor="", content="x")


def test_core_occurrence_first_vs_last():
    txt = "TAG\nmid\nTAG\n"
    out_first = insert_at_anchor_core(txt, anchor="TAG", content="BEFORE", position="before", occurrence="first")
    assert out_first.startswith("BEFORE")
    out_last = insert_at_anchor_core(txt, anchor="TAG", content="BEFORE", position="before", occurrence="last")
    # La dernière occurrence de TAG vient après "mid"
    assert out_last.index("mid") < out_last.index("BEFORE")


def test_core_occurrence_numeric():
    txt = "X\nX\nX\n"
    out = insert_at_anchor_core(txt, anchor="X", content="Y", position="before", occurrence=2)
    # Y doit être avant le 2e X
    lines = out.split("\n")
    # Structure attendue: X, Y, X, X, ""
    assert lines[0] == "X"
    assert lines[1] == "Y"
    assert lines[2] == "X"
    assert lines[3] == "X"


def test_core_occurrence_out_of_bounds():
    txt = "X\nX\n"
    with pytest.raises(ValueError, match="hors bornes"):
        insert_at_anchor_core(txt, anchor="X", content="Y", occurrence=5)


def test_core_preserves_indentation():
    txt = "class Foo:\n    def bar(self):\n        pass\n"
    out = insert_at_anchor_core(txt, anchor="def bar", content="def new_method(self): pass", position="before")
    # L'insertion doit conserver l'indentation de 4 espaces
    assert "    def new_method(self): pass" in out


def test_core_js_export_default():
    txt = "import x from 'y';\n\nexport default function App() { return null; }\n"
    out = insert_at_anchor_core(txt, anchor="export default", content="function helper() { return 1; }", position="before")
    assert "function helper()" in out
    assert out.index("function helper()") < out.index("export default")


def test_core_java_class_close():
    txt = "public class Foo {\n    public void a() {}\n} // end class\n"
    out = insert_at_anchor_core(
        txt, anchor="} // end class", content="    public void b() {}", position="before"
    )
    assert "public void b()" in out
    assert out.index("public void b()") < out.index("} // end class")


def test_core_css_end_marker():
    txt = ".foo { color: red; }\n/* END */\n"
    out = insert_at_anchor_core(txt, anchor="/* END */", content=".bar { color: blue; }", position="before")
    assert ".bar" in out
    assert out.index(".bar") < out.index("/* END */")


def test_core_go_main():
    txt = "package main\n\nfunc main() {\n    println(\"hi\")\n}\n"
    out = insert_at_anchor_core(txt, anchor="func main()", content="func helper() {}", position="before")
    assert "func helper()" in out
    assert out.index("func helper()") < out.index("func main()")


# ───────────────────────── Async handler ─────────────────────────


@pytest.fixture
def ctx(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return HandlerContext.for_testing(
        lumena_root=tmp_path,
        runtime_root=workspace,
    )


@pytest.mark.asyncio
async def test_handler_before_html(ctx):
    f = ctx.runtime_root / "index.html"
    f.write_text("<html>\n<main>hi</main>\n</html>\n", encoding="utf-8")
    res = await insert_at_anchor_handler(
        ctx,
        path="index.html",
        anchor="</main>",
        content="<section>NEW</section>",
        position="before",
    )
    assert res.success
    assert "✅" in res.output
    content = f.read_text(encoding="utf-8")
    assert "<section>NEW</section>" in content
    assert content.index("<section>NEW</section>") < content.index("</main>")


@pytest.mark.asyncio
async def test_handler_file_not_found(ctx):
    res = await insert_at_anchor_handler(
        ctx, path="missing.html", anchor="</main>", content="x"
    )
    assert res.success  # HandlerResult.ok (non-fail) mais contenu ❌
    assert "introuvable" in res.output.lower() or "❌" in res.output


@pytest.mark.asyncio
async def test_handler_anchor_missing(ctx):
    f = ctx.runtime_root / "a.txt"
    f.write_text("hello world\n", encoding="utf-8")
    res = await insert_at_anchor_handler(
        ctx, path="a.txt", anchor="</main>", content="x"
    )
    assert "❌" in res.output
    # Le fichier n'est pas modifié
    assert f.read_text(encoding="utf-8") == "hello world\n"


@pytest.mark.asyncio
async def test_handler_replace_position(ctx):
    f = ctx.runtime_root / "config.py"
    f.write_text("VERSION = 'OLD'\n", encoding="utf-8")
    res = await insert_at_anchor_handler(
        ctx, path="config.py", anchor="OLD", content="NEW", position="replace"
    )
    assert res.success
    assert f.read_text(encoding="utf-8") == "VERSION = 'NEW'\n"


# ───────────────────────── Registry ─────────────────────────


def test_handler_registered_in_defs():
    defs = get_file_handler_defs()
    names = [d.name for d in defs]
    assert "insert_at_anchor" in names


def test_handler_def_required_params():
    defs = {d.name: d for d in get_file_handler_defs()}
    d = defs["insert_at_anchor"]
    assert set(d.parameters["required"]) == {"path", "anchor", "content"}
    assert "position" in d.parameters["properties"]
    assert "occurrence" in d.parameters["properties"]
