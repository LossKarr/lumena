"""
Tests des Outils Avancés — V2 handlers (files.py)

Tests unitaires pour grep_search, find_files, edit_file, view_outline
et non-régression read_file, list_directory, write_file, get_time.
"""

import pytest
import asyncio
import tempfile
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.reasoning.handlers.context import HandlerContext
from src.reasoning.handlers.files import (
    grep_search_handler,
    find_files_handler,
    edit_file_handler,
    view_outline_handler,
    read_file_handler,
    list_directory_handler,
    write_file_handler,
)
from src.tools.file_guardrails import WorkspaceFileGuardrails


@pytest.fixture
def temp_dir():
    """Crée un dossier temporaire pour les tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def ctx(temp_dir):
    """HandlerContext minimal pointant sur temp_dir."""
    return HandlerContext(
        lumena_root=temp_dir,
        runtime_root=temp_dir,
        file_guardrails=WorkspaceFileGuardrails(temp_dir),
    )


@pytest.fixture
def sample_python_file(temp_dir):
    """Crée un fichier Python d'exemple pour les tests."""
    content = '''"""Module de test."""

class TestClass:
    """Une classe de test."""
    
    def __init__(self):
        self.value = 42
    
    def method_one(self):
        """Première méthode."""
        return self.value
    
    async def async_method(self):
        """Méthode async."""
        return await some_async_call()


def standalone_function():
    """Fonction standalone."""
    return "hello"


async def async_standalone():
    """Fonction async standalone."""
    pass
'''
    file_path = temp_dir / "sample.py"
    file_path.write_text(content, encoding='utf-8')
    return file_path


@pytest.fixture
def sample_text_files(temp_dir):
    """Crée plusieurs fichiers texte pour les tests."""
    (temp_dir / "file1.txt").write_text("Hello World\nThis is a test\nHello again", encoding='utf-8')
    (temp_dir / "file2.txt").write_text("Another file\nWith different content", encoding='utf-8')
    subdir = temp_dir / "subdir"
    subdir.mkdir()
    (subdir / "nested.txt").write_text("Nested file content\nHello from nested", encoding='utf-8')
    (temp_dir / "code.py").write_text("def hello():\n    print('Hello')", encoding='utf-8')
    return temp_dir


# ==================== TESTS GREP_SEARCH ====================

class TestGrepSearch:

    @pytest.mark.asyncio
    async def test_grep_simple_text(self, ctx, sample_text_files):
        r = await grep_search_handler(ctx, pattern="Hello", path=str(sample_text_files))
        assert "Hello" in r.output
        assert "résultat" in r.output

    @pytest.mark.asyncio
    async def test_grep_case_insensitive(self, ctx, sample_text_files):
        r = await grep_search_handler(ctx, pattern="hello", path=str(sample_text_files), ignore_case=True)
        assert "Hello" in r.output or "hello" in r.output

    @pytest.mark.asyncio
    async def test_grep_regex(self, ctx, sample_text_files):
        r = await grep_search_handler(ctx, pattern=r"def \w+\(", path=str(sample_text_files), is_regex=True)
        assert "def" in r.output

    @pytest.mark.asyncio
    async def test_grep_no_match(self, ctx, sample_text_files):
        r = await grep_search_handler(ctx, pattern="XXXXXXX_NOT_FOUND", path=str(sample_text_files))
        assert "Aucun résultat" in r.output

    @pytest.mark.asyncio
    async def test_grep_single_file(self, ctx, sample_text_files):
        r = await grep_search_handler(ctx, pattern="test", path=str(sample_text_files / "file1.txt"))
        assert "test" in r.output.lower()

    @pytest.mark.asyncio
    async def test_grep_invalid_path(self, ctx):
        r = await grep_search_handler(ctx, pattern="test", path="/nonexistent/path")
        assert "non trouvé" in r.output or "❌" in r.output or "accès refusé" in r.output.lower() or "hors des limites" in r.output.lower()


# ==================== TESTS FIND_FILES ====================

class TestFindFiles:

    @pytest.mark.asyncio
    async def test_find_by_extension(self, ctx, sample_text_files):
        r = await find_files_handler(ctx, pattern="*.txt", path=str(sample_text_files))
        assert "file1.txt" in r.output
        assert "file2.txt" in r.output

    @pytest.mark.asyncio
    async def test_find_python_files(self, ctx, sample_text_files):
        r = await find_files_handler(ctx, pattern="*.py", path=str(sample_text_files))
        assert "code.py" in r.output

    @pytest.mark.asyncio
    async def test_find_recursive(self, ctx, sample_text_files):
        r = await find_files_handler(ctx, pattern="*.txt", path=str(sample_text_files))
        assert "nested.txt" in r.output

    @pytest.mark.asyncio
    async def test_find_no_match(self, ctx, sample_text_files):
        r = await find_files_handler(ctx, pattern="*.xyz", path=str(sample_text_files))
        assert "Aucun fichier" in r.output or "0 fichier" in r.output

    @pytest.mark.asyncio
    async def test_find_invalid_directory(self, ctx):
        r = await find_files_handler(ctx, pattern="*", path="/nonexistent/directory")
        assert "non trouv" in r.output.lower() or "❌" in r.output or "0 " in r.output or "accès refusé" in r.output.lower() or "hors des limites" in r.output.lower()


# ==================== TESTS EDIT_FILE ====================

class TestEditFile:

    @pytest.mark.asyncio
    async def test_edit_single_occurrence(self, ctx, temp_dir):
        file_path = temp_dir / "edit_test.txt"
        file_path.write_text("Hello World", encoding='utf-8')
        r = await edit_file_handler(ctx, file_path=str(file_path), old_content="World", new_content="Universe")
        assert "✅" in r.output
        assert "Universe" in file_path.read_text()

    @pytest.mark.asyncio
    async def test_edit_not_found(self, ctx, temp_dir):
        file_path = temp_dir / "edit_test.txt"
        file_path.write_text("Hello World", encoding='utf-8')
        r = await edit_file_handler(ctx, file_path=str(file_path), old_content="NOTFOUND", new_content="replacement")
        assert "non trouvé" in r.output or "❌" in r.output or "introuvable" in r.output.lower()

    @pytest.mark.asyncio
    async def test_edit_invalid_file(self, ctx):
        r = await edit_file_handler(ctx, file_path="/nonexistent/file.txt", old_content="test", new_content="new")
        assert "non trouvé" in r.output or "❌" in r.output or "introuvable" in r.output.lower()

    @pytest.mark.asyncio
    async def test_edit_idempotent_append_by_replace(self, ctx, temp_dir):
        """Vérifie que appliquer deux fois le même edit (old ⊂ new) ne duplique pas le contenu."""
        file_path = temp_dir / "idempotent_test.js"
        file_path.write_text("  doA();\n  doB();", encoding='utf-8')
        old = "  doA();\n  doB();"
        new = "  doA();\n  doB();\n  doC();"
        r1 = await edit_file_handler(ctx, file_path=str(file_path), old_content=old, new_content=new)
        assert "✅" in r1.output
        r2 = await edit_file_handler(ctx, file_path=str(file_path), old_content=old, new_content=new)
        # Le 2ème appel doit être idempotent (pas d'erreur, pas de doublon)
        assert "✅" in r2.output
        content = file_path.read_text(encoding='utf-8')
        assert content.count("doC()") == 1, f"Doublon détecté : doC() présent {content.count('doC()')} fois"


# ==================== TESTS VIEW_FILE_OUTLINE ====================

class TestViewFileOutline:

    @pytest.mark.asyncio
    async def test_outline_python_file(self, ctx, sample_python_file):
        r = await view_outline_handler(ctx, path=str(sample_python_file))
        assert "TestClass" in r.output
        assert "standalone_function" in r.output

    @pytest.mark.asyncio
    async def test_outline_shows_methods(self, ctx, sample_python_file):
        r = await view_outline_handler(ctx, path=str(sample_python_file))
        assert "method_one" in r.output
        assert "__init__" in r.output

    @pytest.mark.asyncio
    async def test_outline_shows_async(self, ctx, sample_python_file):
        r = await view_outline_handler(ctx, path=str(sample_python_file))
        assert "async" in r.output

    @pytest.mark.asyncio
    async def test_outline_invalid_file(self, ctx):
        r = await view_outline_handler(ctx, path="/nonexistent/file.py")
        assert "non trouvé" in r.output or "❌" in r.output or "introuvable" in r.output.lower()


# ==================== TESTS NON-RÉGRESSION ====================

class TestNonRegression:

    @pytest.mark.asyncio
    async def test_read_file_still_works(self, ctx, temp_dir):
        file_path = temp_dir / "test.txt"
        file_path.write_text("Test content", encoding='utf-8')
        r = await read_file_handler(ctx, path=str(file_path))
        assert "Test content" in r.output

    @pytest.mark.asyncio
    async def test_list_directory_still_works(self, ctx, temp_dir):
        (temp_dir / "file1.txt").write_text("test", encoding='utf-8')
        (temp_dir / "file2.txt").write_text("test", encoding='utf-8')
        r = await list_directory_handler(ctx, path=str(temp_dir))
        assert "file1.txt" in r.output
        assert "file2.txt" in r.output

    @pytest.mark.asyncio
    async def test_write_file_still_works(self, ctx, temp_dir):
        file_path = temp_dir / "new_file.txt"
        r = await write_file_handler(ctx, path=str(file_path), content="New content")
        assert "✅" in r.output
        assert file_path.exists()
        assert file_path.read_text() == "New content"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
