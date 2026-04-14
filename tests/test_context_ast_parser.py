"""Tests unitaires pour src/context/ast_parser.py"""
import ast
import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

from src.context.ast_parser import (
    CodeSymbol,
    FileSignatures,
    ASTParser,
)


# ─── CodeSymbol ────────────────────────────────────────────────────────────

class TestCodeSymbol:
    def test_function_to_compact(self):
        sym = CodeSymbol(
            name="my_func",
            type="function",
            signature="def my_func(x, y)",
            line_start=10,
            line_end=20,
        )
        compact = sym.to_compact()
        assert "my_func" in compact
        assert "def" in compact

    def test_async_function_to_compact(self):
        sym = CodeSymbol(
            name="fetch",
            type="async_function",
            signature="async def fetch(url)",
            line_start=1,
            line_end=5,
        )
        compact = sym.to_compact()
        assert "async" in compact

    def test_class_to_compact(self):
        sym = CodeSymbol(
            name="MyClass",
            type="class",
            signature="class MyClass(Base)",
            line_start=1,
            line_end=50,
        )
        compact = sym.to_compact()
        assert "class MyClass" in compact

    def test_method_with_parent(self):
        sym = CodeSymbol(
            name="run",
            type="method",
            signature="def run(self)",
            line_start=5,
            line_end=10,
            parent="MyClass",
        )
        compact = sym.to_compact()
        assert "MyClass.run" in compact


# ─── FileSignatures ────────────────────────────────────────────────────────

class TestFileSignatures:
    def test_is_valid_true(self):
        fs = FileSignatures(path="foo.py", language="python")
        assert fs.is_valid is True

    def test_is_valid_false_with_error(self):
        fs = FileSignatures(path="bar.py", language="python", error="SyntaxError")
        assert fs.is_valid is False

    def test_to_map_entry_empty(self):
        fs = FileSignatures(path="foo.py", language="python")
        assert fs.to_map_entry() == ""

    def test_to_map_entry_with_symbols(self):
        sym = CodeSymbol(
            name="foo",
            type="function",
            signature="def foo()",
            line_start=1,
            line_end=3,
        )
        fs = FileSignatures(path="foo.py", language="python", symbols=[sym])
        entry = fs.to_map_entry()
        assert "foo.py" in entry
        assert "def foo()" in entry

    def test_to_map_entry_truncates_beyond_max(self):
        symbols = [
            CodeSymbol(name=f"func_{i}", type="function",
                       signature=f"def func_{i}()", line_start=i, line_end=i+1)
            for i in range(15)
        ]
        fs = FileSignatures(path="big.py", language="python", symbols=symbols)
        entry = fs.to_map_entry(max_symbols=5)
        assert "+10 more" in entry


# ─── ASTParser — language detection ────────────────────────────────────────

class TestASTParserLanguageDetection:
    def test_python_extension(self):
        parser = ASTParser()
        lang = parser.SUPPORTED_EXTENSIONS.get(".py")
        assert lang == "python"

    def test_js_extension(self):
        parser = ASTParser()
        lang = parser.SUPPORTED_EXTENSIONS.get(".js")
        assert lang == "javascript"

    def test_ts_extension(self):
        parser = ASTParser()
        assert parser.SUPPORTED_EXTENSIONS.get(".ts") == "typescript"

    def test_unknown_extension(self):
        parser = ASTParser()
        assert parser.SUPPORTED_EXTENSIONS.get(".xyz") is None


# ─── ASTParser._parse_python ───────────────────────────────────────────────

class TestASTParserPython:
    def setup_method(self):
        self.parser = ASTParser()

    def test_parses_simple_function(self):
        code = "def greet(name: str) -> str:\n    return 'hello ' + name\n"
        result = self.parser._parse_python(code, "test.py")
        assert result.is_valid
        func_names = [s.name for s in result.symbols]
        assert "greet" in func_names

    def test_parses_async_function(self):
        code = "async def fetch(url: str):\n    pass\n"
        result = self.parser._parse_python(code, "test.py")
        assert any(s.type == "async_function" for s in result.symbols)
        assert any(s.name == "fetch" for s in result.symbols)

    def test_parses_class(self):
        code = "class Dog:\n    def bark(self):\n        pass\n"
        result = self.parser._parse_python(code, "test.py")
        names = [s.name for s in result.symbols]
        assert "Dog" in names

    def test_parses_class_with_methods(self):
        code = "class Dog:\n    def bark(self):\n        pass\n    def sit(self):\n        pass\n"
        result = self.parser._parse_python(code, "test.py")
        method_names = [s.name for s in result.symbols if s.type == "method"]
        assert "bark" in method_names
        assert "sit" in method_names

    def test_parse_error_returns_invalid(self):
        bad_code = "def broken syntax !!!\n"
        result = self.parser._parse_python(bad_code, "bad.py")
        assert not result.is_valid
        assert result.error is not None

    def test_extracts_imports(self):
        code = "import os\nimport sys\nfrom pathlib import Path\n"
        result = self.parser._parse_python(code, "imports.py")
        assert "os" in result.imports or "sys" in result.imports

    def test_function_signature_includes_return_type(self):
        code = "def add(x: int, y: int) -> int:\n    return x + y\n"
        result = self.parser._parse_python(code, "test.py")
        func = next(s for s in result.symbols if s.name == "add")
        assert "-> int" in func.signature

    def test_limits_import_count(self):
        imports = "\n".join(f"import mod_{i}" for i in range(30))
        result = self.parser._parse_python(imports, "test.py")
        assert len(result.imports) <= 20


# ─── ASTParser.parse_file ──────────────────────────────────────────────────

class TestASTParserParseFile:
    def test_parse_real_python_file(self, tmp_path):
        file = tmp_path / "sample.py"
        file.write_text("def hello():\n    pass\n\nclass World:\n    pass\n")
        parser = ASTParser()
        result = parser.parse_file(file)
        assert result.is_valid
        names = [s.name for s in result.symbols]
        assert "hello" in names
        assert "World" in names

    def test_caches_result(self, tmp_path):
        file = tmp_path / "cached.py"
        file.write_text("def fn(): pass\n")
        parser = ASTParser()
        r1 = parser.parse_file(file)
        r2 = parser.parse_file(file)
        assert r1 is r2

    def test_unknown_extension_returns_error(self, tmp_path):
        file = tmp_path / "data.xyz"
        file.write_text("some content")
        parser = ASTParser()
        result = parser.parse_file(file)
        assert not result.is_valid

    def test_missing_file_returns_error(self, tmp_path):
        file = tmp_path / "nonexistent.py"
        parser = ASTParser()
        result = parser.parse_file(file)
        assert not result.is_valid


# ─── ASTParser._parse_generic (JavaScript) ─────────────────────────────────

class TestASTParserGeneric:
    def test_parses_js_function(self):
        parser = ASTParser()
        code = "function greet(name) {\n    return 'Hello ' + name;\n}\n"
        result = parser._parse_generic(code, "test.js", "javascript")
        names = [s.name for s in result.symbols]
        assert "greet" in names

    def test_parses_js_class(self):
        parser = ASTParser()
        code = "class Animal {\n    constructor() {}\n}\n"
        result = parser._parse_generic(code, "test.js", "javascript")
        names = [s.name for s in result.symbols]
        assert "Animal" in names

    def test_parses_typescript_interface(self):
        parser = ASTParser()
        code = "export interface User {\n    name: string;\n}\n"
        result = parser._parse_generic(code, "test.ts", "typescript")
        names = [s.name for s in result.symbols]
        assert "User" in names
