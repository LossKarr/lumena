"""Tests unitaires pour src/context/code_index.py"""
import pytest
from pathlib import Path

try:
    import chromadb
    CHROMADB_AVAILABLE = True
except ImportError:
    CHROMADB_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not CHROMADB_AVAILABLE,
    reason="chromadb non disponible"
)

from src.context.code_index import CodeIndex, CodeSearchResult, CodeChunk


class TestCodeSearchResult:
    def test_fields_exist(self):
        fields = set(CodeSearchResult.__dataclass_fields__)
        assert "chunk" in fields
        assert "score" in fields
        assert "highlight" in fields


class TestCodeChunk:
    def test_create_chunk(self):
        chunk = CodeChunk(
            id="chunk_001",
            content="def main(): pass",
            file_path="main.py",
            symbol_name="main",
            symbol_type="function",
            line_start=1,
            line_end=1,
            language="Python",
        )
        assert chunk.content == "def main(): pass"
        assert chunk.file_path == "main.py"
        assert chunk.language == "Python"


class TestCodeIndex:
    @pytest.fixture
    def code_index(self, tmp_path):
        # Create a small test file
        py_file = tmp_path / "test_code.py"
        py_file.write_text(
            "def add(a, b):\n    return a + b\n\n"
            "class Calculator:\n    def multiply(self, x, y):\n        return x * y\n"
        )
        persist_dir = tmp_path / "chroma"
        return CodeIndex(project_root=tmp_path, persist_dir=persist_dir)

    def test_instantiation(self, code_index):
        assert code_index is not None

    def test_project_root(self, code_index, tmp_path):
        assert code_index.project_root == tmp_path

    def test_get_stats_returns_dict(self, code_index):
        stats = code_index.get_stats()
        assert isinstance(stats, dict)

    def test_search_returns_list(self, code_index):
        # Search without indexing should return empty list
        results = code_index.search("add function")
        assert isinstance(results, list)

    def test_get_context_for_query(self, code_index):
        ctx = code_index.get_context_for_query("multiply method")
        assert isinstance(ctx, str)
