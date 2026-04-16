"""Tests unitaires pour src/context/code_chunker.py"""
import hashlib
import pytest
from pathlib import Path

from src.context.code_chunker import CodeChunk, CodeChunker


# ─── CodeChunk ─────────────────────────────────────────────────────────────

class TestCodeChunk:
    def _make(self, **kwargs):
        defaults = dict(
            id="abc123",
            content="def foo(): pass",
            file_path="src/foo.py",
            symbol_name="foo",
            symbol_type="function",
            line_start=1,
            line_end=1,
            language="python",
        )
        defaults.update(kwargs)
        return CodeChunk(**defaults)

    def test_to_dict_keys(self):
        chunk = self._make()
        d = chunk.to_dict()
        for key in ["id", "content", "file_path", "symbol_name", "symbol_type",
                    "line_start", "line_end", "language", "imports", "parent_class"]:
            assert key in d

    def test_metadata_str_with_symbol(self):
        chunk = self._make(file_path="src/core.py", symbol_name="my_func",
                           symbol_type="function", line_start=10, line_end=20)
        meta = chunk.metadata_str
        assert "src/core.py" in meta
        assert "my_func" in meta
        assert "10-20" in meta

    def test_metadata_str_without_symbol(self):
        chunk = self._make(symbol_name=None, symbol_type="module",
                           line_start=1, line_end=50)
        meta = chunk.metadata_str
        assert "1-50" in meta

    def test_to_dict_roundtrip(self):
        chunk = self._make(imports=["os", "sys"], parent_class="MyClass")
        d = chunk.to_dict()
        assert d["imports"] == ["os", "sys"]
        assert d["parent_class"] == "MyClass"


# ─── CodeChunker._create_chunk ─────────────────────────────────────────────

class TestCodeChunkerCreateChunk:
    def setup_method(self, tmp_path_factory=None):
        import tempfile
        self._tmp = tempfile.mkdtemp()
        self.project_root = Path(self._tmp)
        self.chunker = CodeChunker(self.project_root)

    def test_creates_chunk_with_hash_id(self):
        chunk = self.chunker._create_chunk(
            content="def foo(): pass",
            file_path="src/foo.py",
            symbol_name="foo",
            symbol_type="function",
            line_start=1,
            line_end=1,
            language="python",
        )
        assert isinstance(chunk, CodeChunk)
        assert len(chunk.id) == 12
        assert chunk.symbol_name == "foo"

    def test_same_input_same_id(self):
        kwargs = dict(
            content="def bar(): pass",
            file_path="bar.py",
            symbol_name="bar",
            symbol_type="function",
            line_start=1,
            line_end=1,
            language="python",
        )
        c1 = self.chunker._create_chunk(**kwargs)
        c2 = self.chunker._create_chunk(**kwargs)
        assert c1.id == c2.id

    def test_different_content_different_id(self):
        c1 = self.chunker._create_chunk(
            content="def foo(): pass", file_path="a.py", symbol_name="foo",
            symbol_type="function", line_start=1, line_end=1, language="python"
        )
        c2 = self.chunker._create_chunk(
            content="def bar(): return 1", file_path="a.py", symbol_name="bar",
            symbol_type="function", line_start=2, line_end=3, language="python"
        )
        assert c1.id != c2.id


# ─── CodeChunker.chunk_file ────────────────────────────────────────────────

class TestCodeChunkerChunkFile:
    def test_chunk_python_file(self, tmp_path):
        code = (
            "def hello(name: str) -> str:\n"
            "    \"\"\"Greet someone by name.\"\"\"\n"
            "    return 'Hello ' + name\n\n"
            "class Dog:\n"
            "    \"\"\"A simple dog class.\"\"\"\n"
            "    def bark(self):\n"
            "        \"\"\"Make the dog bark loudly.\"\"\"\n"
            "        print('Woof!')\n"
        )
        file = tmp_path / "sample.py"
        file.write_text(code)
        chunker = CodeChunker(tmp_path)
        chunks = chunker.chunk_file(file)
        assert len(chunks) >= 2  # hello + Dog
        names = [c.symbol_name for c in chunks]
        assert "hello" in names
        assert "Dog" in names

    def test_skips_methods_as_top_level_chunks(self, tmp_path):
        code = "class MyClass:\n    def method_a(self): pass\n    def method_b(self): pass\n"
        file = tmp_path / "cls.py"
        file.write_text(code)
        chunker = CodeChunker(tmp_path)
        chunks = chunker.chunk_file(file)
        # Methods should NOT create independent chunks (parent is set)
        top_level = [c for c in chunks if c.symbol_type in ("function", "class")]
        method_chunks = [c for c in chunks if c.symbol_type == "method"]
        assert len(method_chunks) == 0

    def test_empty_file_returns_empty(self, tmp_path):
        file = tmp_path / "empty.py"
        file.write_text("")
        chunker = CodeChunker(tmp_path)
        chunks = chunker.chunk_file(file)
        assert chunks == []

    def test_invalid_file_returns_empty(self, tmp_path):
        file = tmp_path / "nonexistent.py"
        chunker = CodeChunker(tmp_path)
        chunks = chunker.chunk_file(file)
        assert chunks == []

    def test_truncates_large_functions(self, tmp_path):
        body = "    x = 1\n" * 300  # Very large function
        code = f"def big_function():\n{body}\n    return x\n"
        file = tmp_path / "big.py"
        file.write_text(code)
        chunker = CodeChunker(tmp_path)
        chunks = chunker.chunk_file(file)
        if chunks:
            assert len(chunks[0].content) <= CodeChunker.MAX_CHUNK_SIZE + 20


# ─── CodeChunker.search_by_name ────────────────────────────────────────────

class TestCodeChunkerSearchByName:
    def setup_method(self):
        import tempfile
        self.project_root = Path(tempfile.mkdtemp())
        self.chunker = CodeChunker(self.project_root)
        # Pre-populate chunks
        self.chunker._chunks = [
            CodeChunk("a1", "def fetch_user(): pass", "api.py",
                      "fetch_user", "function", 1, 2, "python"),
            CodeChunk("b2", "def get_config(): pass", "config.py",
                      "get_config", "function", 3, 5, "python"),
            CodeChunk("c3", "class UserService: pass", "service.py",
                      "UserService", "class", 1, 20, "python"),
        ]

    def test_search_by_partial_name(self):
        results = self.chunker.search_by_name("user")
        names = [r.symbol_name for r in results]
        assert "fetch_user" in names
        assert "UserService" in names

    def test_search_case_insensitive(self):
        results = self.chunker.search_by_name("USER")
        assert len(results) >= 1

    def test_no_match_returns_empty(self):
        results = self.chunker.search_by_name("zzznonexistent")
        assert results == []

    def test_search_on_empty_chunks(self):
        self.chunker._chunks = []
        results = self.chunker.search_by_name("anything")
        assert results == []
