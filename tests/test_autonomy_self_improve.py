"""Tests unitaires pour src/autonomy/self_improve.py (CodeAnalyzer)"""
import ast
import pytest
from pathlib import Path

from src.autonomy.self_improve import CodeAnalyzer


@pytest.fixture
def py_file(tmp_path):
    f = tmp_path / "sample.py"
    f.write_text(
        'import os\nimport sys\n\nclass Foo:\n    def bar(self):\n        pass\n\ndef top_func(x, y):\n    return x + y\n',
        encoding="utf-8"
    )
    return f


@pytest.fixture
def non_py_file(tmp_path):
    f = tmp_path / "data.json"
    f.write_text('{"key": "value"}')
    return f


class TestCodeAnalyzerGetFileInfo:
    def test_existing_file(self, py_file):
        info = CodeAnalyzer.get_file_info(py_file)
        assert "lines" in info
        assert info["extension"] == ".py"
        assert info["size_bytes"] > 0

    def test_nonexistent_file(self, tmp_path):
        info = CodeAnalyzer.get_file_info(tmp_path / "ghost.py")
        assert "error" in info


class TestCodeAnalyzerAnalyzePython:
    def test_extracts_classes(self, py_file):
        info = CodeAnalyzer.analyze_python_file(py_file)
        classes = info.get("classes", [])
        assert any(c["name"] == "Foo" for c in classes)

    def test_extracts_functions(self, py_file):
        info = CodeAnalyzer.analyze_python_file(py_file)
        fns = info.get("functions", [])
        assert any(f["name"] == "top_func" for f in fns)

    def test_extracts_imports(self, py_file):
        info = CodeAnalyzer.analyze_python_file(py_file)
        imports = info.get("imports", [])
        assert "os" in imports or any("os" in i for i in imports)

    def test_syntax_error_returns_error(self, tmp_path):
        bad = tmp_path / "bad.py"
        bad.write_text("def foo(\n", encoding="utf-8")
        info = CodeAnalyzer.analyze_python_file(bad)
        assert "error" in info

    def test_non_python_file_returns_error(self, non_py_file):
        info = CodeAnalyzer.analyze_python_file(non_py_file)
        assert "error" in info
