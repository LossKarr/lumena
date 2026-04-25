"""
test_handlers_files.py - Tests fonctionnels des handlers fichiers fragmentés.

Teste chaque handler de files.py avec un HandlerContext de test.
"""

import pytest
from pathlib import Path

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.contracts import HandlerResult
from src.reasoning.handlers.files import (
    read_file_handler,
    write_file_handler,
    list_directory_handler,
    find_files_handler,
    open_file_handler,
    delete_file_handler,
    edit_file_handler,
    view_outline_handler,
    get_file_handler_defs,
)


@pytest.fixture
def ctx(tmp_path):
    """Crée un HandlerContext de test avec un workspace temporaire."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return HandlerContext.for_testing(
        lumena_root=tmp_path,
        runtime_root=workspace,
    )


@pytest.fixture
def sample_file(ctx):
    """Crée un fichier de test dans le workspace."""
    f = ctx.runtime_root / "hello.txt"
    f.write_text("ligne 1\nligne 2\nligne 3\nligne 4\nligne 5", encoding="utf-8")
    return f


@pytest.fixture
def sample_py(ctx):
    """Crée un fichier Python de test."""
    f = ctx.runtime_root / "sample.py"
    f.write_text(
        'class Foo:\n    def bar(self):\n        pass\n\ndef baz():\n    return 42\n',
        encoding="utf-8",
    )
    return f


# ─── read_file ─────────────────────────────────────────────────────────────

@pytest.fixture
def sample_js(ctx):
    """CrÃ©e un fichier JavaScript de test."""
    f = ctx.runtime_root / "sample.js"
    f.write_text(
        "class Widget {\n"
        "  render() { return true; }\n"
        "}\n\n"
        "function bootApp() {\n"
        "  return new Widget();\n"
        "}\n",
        encoding="utf-8",
    )
    return f


class TestReadFile:
    @pytest.mark.asyncio
    async def test_read_existing_file(self, ctx, sample_file):
        r = await read_file_handler(ctx, path="hello.txt")
        assert r.success
        assert "ligne 1" in r.output
        assert "ligne 5" in r.output

    @pytest.mark.asyncio
    async def test_read_missing_file(self, ctx):
        r = await read_file_handler(ctx, path="nonexistent.txt")
        assert "non trouvé" in r.output or "❌" in r.output

    @pytest.mark.asyncio
    async def test_read_with_line_range(self, ctx, sample_file):
        r = await read_file_handler(ctx, path="hello.txt", start_line=2, end_line=3)
        assert r.success
        assert "ligne 2" in r.output
        assert "ligne 3" in r.output

    @pytest.mark.asyncio
    async def test_read_empty_file(self, ctx):
        empty = ctx.runtime_root / "empty.txt"
        empty.write_text("", encoding="utf-8")
        r = await read_file_handler(ctx, path="empty.txt")
        assert r.success
        assert "vide" in r.output.lower()


# ─── write_file ────────────────────────────────────────────────────────────

class TestWriteFile:
    @pytest.mark.asyncio
    async def test_write_new_file(self, ctx):
        # En mode IDE avec chemin absolu, le handler écrit directement
        target = ctx.runtime_root / "new.txt"
        ctx_ide = HandlerContext.for_testing(
            lumena_root=ctx.lumena_root,
            runtime_root=ctx.runtime_root,
            ide_context={"workspace_path": str(ctx.runtime_root)},
        )
        r = await write_file_handler(ctx_ide, path=str(target), content="hello world")
        assert r.success, f"write_file failed: {r.output}"
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "hello world"

    @pytest.mark.asyncio
    async def test_write_missing_path(self, ctx):
        r = await write_file_handler(ctx, content="hello")
        assert "❌" in r.output

    @pytest.mark.asyncio
    async def test_write_missing_content(self, ctx):
        r = await write_file_handler(ctx, path="test.txt")
        assert "❌" in r.output


# ─── list_directory ────────────────────────────────────────────────────────

class TestListDirectory:
    @pytest.mark.asyncio
    async def test_list_existing_dir(self, ctx, sample_file):
        r = await list_directory_handler(ctx, path=".")
        assert r.success
        assert "hello.txt" in r.output

    @pytest.mark.asyncio
    async def test_list_workspace_prefixed_dir(self, ctx):
        nested = ctx.runtime_root / "nested"
        nested.mkdir()
        (nested / "a.txt").write_text("ok", encoding="utf-8")
        r = await list_directory_handler(ctx, path="workspace/nested")
        assert r.success
        assert "a.txt" in r.output

    @pytest.mark.asyncio
    async def test_list_missing_dir(self, ctx):
        r = await list_directory_handler(ctx, path="nonexistent_dir")
        assert "❌" in r.output or "non trouvé" in r.output

    @pytest.mark.asyncio
    async def test_list_empty_dir(self, ctx):
        empty = ctx.runtime_root / "empty_dir"
        empty.mkdir()
        r = await list_directory_handler(ctx, path="empty_dir")
        assert r.success
        assert "vide" in r.output.lower()


# ─── find_files ────────────────────────────────────────────────────────────

class TestFindFiles:
    @pytest.mark.asyncio
    async def test_find_by_name(self, ctx, sample_file):
        r = await find_files_handler(ctx, pattern="hello", path=".")
        assert r.success
        assert "hello.txt" in r.output

    @pytest.mark.asyncio
    async def test_find_by_glob(self, ctx, sample_file):
        r = await find_files_handler(ctx, pattern="*.txt", path=".")
        assert r.success
        assert "hello.txt" in r.output

    @pytest.mark.asyncio
    async def test_find_no_results(self, ctx):
        import src.utils.paths as _paths
        from unittest.mock import patch
        with patch.object(_paths, "ROOT_DIR", ctx.runtime_root):
            r = await find_files_handler(ctx, pattern="zzz_nonexistent", path=".")
        assert "Aucun" in r.output

    @pytest.mark.asyncio
    async def test_find_empty_pattern(self, ctx):
        r = await find_files_handler(ctx, pattern="")
        assert "vide" in r.output.lower() or "Pattern" in r.output


# ─── view_outline ──────────────────────────────────────────────────────────

class TestViewOutline:
    @pytest.mark.asyncio
    async def test_outline_python_file(self, ctx, sample_py):
        r = await view_outline_handler(ctx, path="sample.py")
        assert r.success
        assert "Foo" in r.output
        assert "baz" in r.output

    @pytest.mark.asyncio
    async def test_outline_javascript_file(self, ctx, sample_js):
        r = await view_outline_handler(ctx, path="sample.js")
        assert r.success
        assert "Widget" in r.output
        assert "bootApp" in r.output

    @pytest.mark.asyncio
    async def test_outline_non_python(self, ctx, sample_file):
        r = await view_outline_handler(ctx, path="hello.txt")
        assert "❌" in r.output

    @pytest.mark.asyncio
    async def test_outline_missing_file(self, ctx):
        r = await view_outline_handler(ctx, path="nope.py")
        assert "❌" in r.output


# ─── get_file_handler_defs ─────────────────────────────────────────────────

class TestHandlerDefs:
    def test_all_defs_have_required_fields(self):
        defs = get_file_handler_defs()
        assert len(defs) == 19  # +insert_at_anchor (18 → 19)
        for d in defs:
            assert d.name
            assert d.description
            assert d.handler is not None
            assert d.category == "files"
            assert d.source_module == "handlers.files"

    def test_unique_names(self):
        defs = get_file_handler_defs()
        names = [d.name for d in defs]
        assert len(names) == len(set(names)), f"Noms dupliqués: {names}"
