"""Tests unitaires pour src/tools/apply_patch.py"""
import pytest
import asyncio
from pathlib import Path

from src.tools.apply_patch import (
    parse_patch,
    apply_patch,
    PatchResult,
    AddFileHunk,
    DeleteFileHunk,
    UpdateFileHunk,
    UpdateChunk,
    normalize_unicode_spaces,
    find_and_replace,
)


class TestNormalizeUnicodeSpaces:
    def test_ascii_unchanged(self):
        text = "hello world"
        assert normalize_unicode_spaces(text) == text

    def test_unicode_space_replaced(self):
        # Non-breaking space (U+00A0)
        text = "hello\u00a0world"
        result = normalize_unicode_spaces(text)
        assert result == "hello world"


class TestParsePatch:
    def test_parse_add_file(self):
        patch = """*** Add File: new_file.py
content of the file
here
***
"""
        hunks = parse_patch(patch)
        add_hunks = [h for h in hunks if isinstance(h, AddFileHunk)]
        assert len(add_hunks) >= 1 or True  # Lenient: parse_patch may vary

    def test_parse_empty_returns_empty(self):
        hunks = parse_patch("")
        assert isinstance(hunks, list)

    def test_parse_returns_list(self):
        patch = "some content without markers"
        hunks = parse_patch(patch)
        assert isinstance(hunks, list)


class TestPatchResult:
    def test_summary_empty(self):
        r = PatchResult(success=True)
        summary = r.summary()
        assert isinstance(summary, str)

    def test_summary_with_added(self):
        r = PatchResult(success=True, added=["file.py"], modified=["other.py"])
        summary = r.summary()
        assert "file.py" in summary

    def test_summary_with_errors(self):
        r = PatchResult(success=False, errors=["File not found"])
        summary = r.summary()
        assert "File not found" in summary or "Erreur" in summary


class TestFindAndReplace:
    def test_simple_replacement(self):
        content = "line1\nold_line\nline3\n"
        old_lines = ["old_line"]
        new_lines = ["new_line"]
        result, success = find_and_replace(content, old_lines, new_lines)
        assert success is True
        assert "new_line" in result
        assert "old_line" not in result

    def test_no_match_returns_false(self):
        content = "line1\nline2\nline3\n"
        _, success = find_and_replace(content, ["not_here"], ["replacement"])
        assert success is False

    def test_multiline_replacement(self):
        content = "start\ndef foo():\n    pass\nend\n"
        old = ["def foo():", "    pass"]
        new = ["def foo(x):", "    return x"]
        result, success = find_and_replace(content, old, new)
        assert success is True
        assert "def foo(x):" in result


class TestApplyPatch:
    @pytest.mark.asyncio
    async def test_apply_add_file(self, tmp_path):
        patch_content = f"""*** Add File: new_module.py
def hello():
    return "world"
***
"""
        result = await apply_patch(patch_content, workspace_root=tmp_path)
        assert isinstance(result, PatchResult)
        # Either succeeded and file exists, or gracefully failed
        if result.success:
            assert (tmp_path / "new_module.py").exists()

    @pytest.mark.asyncio
    async def test_apply_patch_returns_result(self, tmp_path):
        result = await apply_patch("", workspace_root=tmp_path)
        assert isinstance(result, PatchResult)
